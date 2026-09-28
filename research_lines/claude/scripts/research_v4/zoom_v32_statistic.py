"""EXPLORATORY / POST-HOC · G-dev ONLY · zoom on the v3.2 statistic S.

Runs the four lenses of the zoom brief on the frozen v3.2 rotation (fold key taken
verbatim from the a2_verify stage-1 manifest):

  (a) attribution: which (layer, expert) coordinates carry hits vs false alarms, per fold
  (b) a normals-only coordinate re-weighting fitted on the FIT fold's normals alone
  (c) rarity-threshold and layer-subset sensitivity
  (d) gate-weight-aware S (top_k_weights, already softmaxed over the 4 selected logits)
  (e) a coordinate-concentration read-out of the same window mass (motivated by (a))

Nothing here is preregistered.  It never reads G-conf.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import torch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import zoom_v32_stat_lib as Z  # noqa: E402
from research_v2 import io_g, trm3, trm3_g  # noqa: E402

SCRATCH = Path(
    "/tmp/claude-1000/-home-wzh-Agent-Moe-Research--claude-worktrees-algorithm-research-proposals-427363/"
    "7e87c1f8-78bb-4b9f-9189-12780e6e8833/scratchpad"
)


# ---------------------------------------------------------------------------
# (d) gate weights
# ---------------------------------------------------------------------------


def weight_cache(pool: Sequence[io_g.GEpisode], path: Path) -> dict[str, torch.Tensor]:
    from safetensors.torch import load_file, save_file

    if path.exists():
        blob = load_file(path)
        return {k: v for k, v in blob.items()}
    out: dict[str, torch.Tensor] = {}
    started = time.time()
    for i, episode in enumerate(pool):
        blocks = []
        for step in episode.step_spans:
            first = int(step["routing_step_index_first_decode"])
            for offset in range(int(step["output_token_count"])):
                blocks.append(
                    load_file(episode.trace_dir / "steps" / f"{first + offset:06d}_decode.safetensors")[
                        "top_k_weights"
                    ]
                )
        out[trm3.trace_key(episode)] = torch.cat(blocks, dim=1).contiguous()
        if (i + 1) % 100 == 0:
            print(f"  weights {i+1}/{len(pool)} ({time.time()-started:.0f}s)", flush=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    save_file(out, path)
    return out


# ---------------------------------------------------------------------------
# (b) normals-only coordinate re-weighting
# ---------------------------------------------------------------------------


def genre_switch_weight(
    fit_pool: Sequence[io_g.GEpisode],
    view: trm3_g.View,
    layers: Sequence[int],
    num_experts: int,
    q: torch.Tensor,
    *,
    looks: int,
    width: int,
    mode: str,
    tau: float,
    smoothing: float = 0.5,
) -> tuple[torch.Tensor, dict[str, Any]]:
    """``w[l, e]`` from the FIT fold's NORMALS only -- no labels, no attack arm.

    ``q_early`` is the smoothed selection rate over the tokens covered by the first
    ``looks`` window endpoints of each episode's ``final`` run (the analysis -> final
    genre switch); ``rho = q_early / q``.  ``mode``:
      * ``soft``   : w = min(1, 1 / rho)
      * ``hard``   : w = 0 where rho > tau, else 1
      * ``power``  : w = min(1, rho ** -tau)
    """

    tokens = int(looks) + int(width) - 1
    counts, total = Z.rare_hit_counts(
        fit_pool, view, layers, num_experts, first_n_final=tokens
    )
    q_early = (counts + smoothing) / (float(total) + num_experts * smoothing)
    rho = (q_early / q).to(torch.float64)
    if mode == "soft":
        w = torch.clamp(1.0 / rho, max=1.0)
    elif mode == "hard":
        w = torch.where(rho > float(tau), torch.zeros_like(rho), torch.ones_like(rho))
    elif mode == "power":
        w = torch.clamp(rho ** (-float(tau)), max=1.0)
    else:
        raise ValueError(mode)
    rare = q < 0.02
    info = {
        "early_tokens": int(total),
        "rare_coordinates": int(rare.sum()),
        "rare_with_rho_gt_1": int(((rho > 1.0) & rare).sum()),
        "rare_with_rho_gt_tau": int(((rho > float(tau)) & rare).sum()),
        "mean_w_on_rare": float(w[rare].mean()),
        "min_w_on_rare": float(w[rare].min()),
    }
    return w, info


# ---------------------------------------------------------------------------
# (e) coordinate concentration
# ---------------------------------------------------------------------------


class SConcentration(Z.SVariant):
    """Same rare-surprisal mass, but the window score keeps only its ``m`` largest
    (layer, expert) contributions instead of summing all of them.

    Per-token features are the full ``[T, L * E]`` rare-surprisal mass (sparse in
    practice: at most ``L * top_k`` non-zero entries per token), so the causal window mean
    is the exact per-coordinate decomposition the attribution hook already reports; the
    score is the sum of its ``m`` largest entries.
    """

    def __init__(self, *, top_m: int = 3, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.top_m = int(top_m)

    def per_token(self, episode: Any) -> torch.Tensor:
        assert self.surprisal is not None, "fit first"
        ids = trm3_g._selected(episode.top_k_ids, self._layers)  # [L, T, k]
        experts = int(self.surprisal.shape[1])
        n_layers, n_tokens, k = ids.shape
        flat_ids = (
            ids + torch.arange(n_layers, dtype=ids.dtype)[:, None, None] * experts
        ).permute(1, 0, 2).reshape(n_tokens, n_layers * k)
        table = self.surprisal.reshape(-1).to(torch.float64)
        values = table[flat_ids]
        out = torch.zeros((n_tokens, n_layers * experts), dtype=torch.float64)
        out.scatter_add_(1, flat_ids, values)
        return out

    def window_score(self, means: torch.Tensor) -> torch.Tensor:
        top = torch.topk(means, min(self.top_m, means.shape[1]), dim=1).values
        return top.sum(dim=1)


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------


def run(out_dir: Path) -> None:
    view = trm3_g.view_of(Z.VIEW)
    pool = Z.load_pool()
    pools = Z.rotation_pools(pool, Z.fold_table())
    anchors = Z.anchors()
    results: dict[str, Any] = {}
    weights: dict[str, torch.Tensor] | None = None

    target = {"far_filtered": None}

    def record(tag: str, make, note: str = "") -> None:
        started = time.time()
        out = Z.score_variant(pools, view, make)
        read = Z.readout(out["per_episode"], anchors)
        if target["far_filtered"] is None:
            target["far_filtered"] = read["far_filtered"][2]
        matched = Z.matched_readout(out["per_episode"], anchors, float(target["far_filtered"]))
        read["matched"] = matched
        read["folds"] = out["folds"]
        read["note"] = note
        read["seconds"] = round(time.time() - started, 1)
        results[tag] = read
        fa = read["far_filtered"]
        fal = read["far_all"]
        hit = read["hits"]
        sil = read["silent"]
        mh = matched["hits"]
        mf = matched["far_filtered"]
        ms = matched["silent"]
        print(
            f"{tag:38s} | a=.10 hit {hit[0]:3d}/{hit[1]}={hit[2]:.4f} "
            f"farA {fal[0]:3d}/{fal[1]}={fal[2]:.4f} farF {fa[0]:3d}/{fa[1]}={fa[2]:.4f} "
            f"sil {sil[0]:2d}/{sil[1]}={sil[2]:.4f} "
            f"|| matched a={matched['alpha']:.5f} hit {mh[0]:3d}/{mh[1]}={mh[2]:.4f} "
            f"farF {mf[0]:3d}/{mf[1]}={mf[2]:.4f} sil {ms[0]:2d}/{ms[1]}={ms[2]:.4f}",
            flush=True,
        )

    # -- baseline ------------------------------------------------------------
    def base(spec):
        s = Z.SVariant(window_width=8, layers=None, rare_threshold=0.02)
        s.fit(spec["fit"], view)
        return s

    record("BASELINE frozen S", base, "frozen v3.2 cell S, reproduced bit-exactly")

    # -- (c) rarity threshold ------------------------------------------------
    for thr in (0.005, 0.01, 0.02, 0.05, 0.10):
        def mk(spec, thr=thr):
            s = Z.SVariant(window_width=8, layers=None, rare_threshold=thr)
            s.fit(spec["fit"], view)
            return s

        record(f"(c) rare_threshold={thr}", mk, "post-hoc rarity sweep")

    # -- (c) layer subsets ---------------------------------------------------
    bands = {
        "early_0_7": tuple(range(0, 8)),
        "mid_8_15": tuple(range(8, 16)),
        "late_16_23": tuple(range(16, 24)),
        "hot_9_13_18_22": (9, 10, 11, 12, 13, 18, 22),
    }
    for name, band in bands.items():
        def mk(spec, band=band):
            s = Z.SVariant(window_width=8, layers=band, rare_threshold=0.02)
            s.fit(spec["fit"], view)
            return s

        record(f"(c) layers {name}", mk, "post-hoc layer subset")

    # -- (b) normals-only re-weighting --------------------------------------
    b_info: dict[str, Any] = {}
    for mode, tau, looks in (
        ("soft", 1.0, 16),
        ("hard", 1.5, 16),
        ("hard", 2.0, 16),
        ("power", 0.5, 16),
        ("soft", 1.0, 32),
        ("hard", 1.5, 32),
    ):
        tag = f"(b) reweight {mode} tau={tau} looks={looks}"

        def mk(spec, mode=mode, tau=tau, looks=looks, tag=tag):
            s = Z.SVariant(window_width=8, layers=None, rare_threshold=0.02)
            s.fit(spec["fit"], view)
            w, info = genre_switch_weight(
                spec["fit"], view, s._layers, int(s.q.shape[1]), s.q,
                looks=looks, width=8, mode=mode, tau=tau,
            )
            b_info.setdefault(tag, {})[str(spec["fold"])] = info
            return s.apply_coord_weight(w)

        record(tag, mk, "normals-only, FIT fold only")
    results["_b_weight_info"] = b_info

    # -- (d) gate-weight-aware ----------------------------------------------
    gate_weights = weight_cache(pool, SCRATCH / "top_k_weights.safetensors")

    def gate(spec):
        s = Z.SVariant(
            window_width=8, layers=None, rare_threshold=0.02,
            gate_weighted=True,
            weights_lookup=lambda ep: gate_weights[trm3.trace_key(ep)],
        )
        s.fit(spec["fit"], view)
        return s

    record("(d) gate-weighted S", gate, "top_k_weights (softmax over the 4 selected)")

    # -- (e) coordinate concentration ---------------------------------------
    for m in (1, 2, 3, 5, 8):
        def mk(spec, m=m):
            s = SConcentration(top_m=m, window_width=8, layers=None, rare_threshold=0.02)
            s.fit(spec["fit"], view)
            return s

        record(f"(e) top-{m} coordinate concentration", mk, "same mass, concentration score")

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "zoom_v32_statistic.json").write_text(json.dumps(results, indent=1, default=str))
    print("->", out_dir / "zoom_v32_statistic.json")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(SCRATCH / "zoom"), help="output directory")
    args = parser.parse_args(argv)
    run(Path(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
