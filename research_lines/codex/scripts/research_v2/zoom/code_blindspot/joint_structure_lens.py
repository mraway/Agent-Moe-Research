"""Code-blindspot zoom, lens = per-layer marginals vs cross-layer joint structure.

Diagnostic only (post-hoc, uses labels/anchors freely).  Nothing here is a detector.

Measures, all fitted on ROUTINE (clean + benign_control, both batches) with a
2-fold pair-group split so that the routine reference values are held out:

  (1) MARGINAL novelty  -- the CAND-A statistic itself (whitened squared distance of
      the w=8 top-8 selection-rate window, layers 5-15, variance floor 1e-3) and its
      exact per-layer decomposition (g1 is separable over dims, hence over layers).
  (2) PAIR-JOINT novelty -- adjacent layer pairs (l, l+1), l = 0..14:
      (2a) rare rate of the (top-1 at l, top-1 at l+1) pair,
      (2b) rare rate of the (top-8 set at l x top-8 set at l+1) cross pairs,
      (2c) rare rate of the |S_l cap S_{l+1}| overlap-count pattern.
      "rare" = routine relative frequency strictly below the 1st percentile of the
      routine-fit instance frequencies (so routine is ~1% rare by construction).
  (3) FULL-CHAIN novelty -- CAND-B's D1 depth-chain surprisal (top-1, smoothing 0.5)
      over layers 5-11 and over 0-15, plus hard unseen-link / unseen-chain counts.

Regions: CODE / OTHER-DRIFT window = tokens [product_onset, min(product_onset+48, T))
(evidence_onset variant also computed).  ROUTINE = all decode tokens of held-out
routine traces.
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import torch

ROOT = Path("/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363")
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from research_v2 import io as rio  # noqa: E402
from research_v2.features import selection_rate_windows  # noqa: E402

OUT = ROOT / "artifacts" / "agent_v2" / "research_v2" / "zoom_code_blindspot"
LABELS = ROOT / "docs" / "research_v2" / "labels" / "product_onset_v1_adjudicated.jsonl"

EXPERTS = 64
TOP_K = 8
WGM_LAYERS = tuple(range(5, 16))
PDM_LAYERS = tuple(range(5, 12))
ALL_LAYERS = tuple(range(16))
WGM_W = 8
PDM_W = 4
REGION = 48
VAR_FLOOR = 1e-3
RARE_PCT = 1.0
SMOOTHINGS = (0.5, 0.05, 0.005)


def load_labels() -> dict:
    out = {}
    for line in LABELS.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            out[r["trace_id"]] = r
    return out


def region_slice(trace, onset: int) -> tuple[int, int]:
    T = trace.token_count
    lo = max(0, int(onset))
    hi = min(lo + REGION, T)
    return lo, hi


# ---------------------------------------------------------------- fitting
class RoutineReference:
    """All routine-fitted tables for one fold."""

    def __init__(self, traces, wgm_layers=WGM_LAYERS):
        self.wgm_layers = tuple(wgm_layers)
        # --- (1) WGM whitening
        blocks = []
        for t in traces:
            _, w = selection_rate_windows(t.top_k_ids, WGM_W, self.wgm_layers)
            if w.shape[0]:
                blocks.append(w)
        matrix = torch.cat(blocks)
        self.mu = matrix.mean(0)
        self.sd = matrix.std(0) + VAR_FLOOR
        z = (matrix - self.mu) / self.sd
        self.centre = z.mean(0)

        # --- (2) pair tables
        self.n_tokens = 0
        top1_pair = torch.zeros((15, EXPERTS, EXPERTS), dtype=torch.float64)
        cross_pair = torch.zeros((15, EXPERTS, EXPERTS), dtype=torch.float64)
        overlap = torch.zeros((15, TOP_K + 1), dtype=torch.float64)
        # --- (3) depth chain tables
        depth_counts = {}
        for band in ("pdm", "all"):
            layers = PDM_LAYERS if band == "pdm" else ALL_LAYERS
            depth_counts[band] = torch.zeros(
                (len(layers) - 1, EXPERTS, EXPERTS), dtype=torch.float64
            )
        depth_initial = {
            "pdm": torch.zeros(EXPERTS, dtype=torch.float64),
            "all": torch.zeros(EXPERTS, dtype=torch.float64),
        }
        chain_counts = {"pdm": Counter(), "all": Counter()}
        for t in traces:
            ids = t.top_k_ids.long()
            T = ids.shape[1]
            self.n_tokens += T
            top1 = ids[:, :, 0]  # [16, T]
            for l in range(15):
                flat = top1[l] * EXPERTS + top1[l + 1]
                top1_pair[l] += torch.bincount(flat, minlength=EXPERTS * EXPERTS).reshape(
                    EXPERTS, EXPERTS
                )
                a = ids[l]  # [T, 8]
                b = ids[l + 1]
                cp = (a.unsqueeze(2) * EXPERTS + b.unsqueeze(1)).reshape(-1)
                cross_pair[l] += torch.bincount(cp, minlength=EXPERTS * EXPERTS).reshape(
                    EXPERTS, EXPERTS
                )
                ov = (a.unsqueeze(2) == b.unsqueeze(1)).sum(dim=(1, 2))
                overlap[l] += torch.bincount(ov, minlength=TOP_K + 1)
            for band, layers in (("pdm", PDM_LAYERS), ("all", ALL_LAYERS)):
                sel = top1[list(layers)]  # [L, T]
                depth_initial[band] += torch.bincount(sel[0], minlength=EXPERTS)
                for i in range(sel.shape[0] - 1):
                    flat = sel[i] * EXPERTS + sel[i + 1]
                    depth_counts[band][i] += torch.bincount(
                        flat, minlength=EXPERTS * EXPERTS
                    ).reshape(EXPERTS, EXPERTS)
                for tok in range(T):
                    chain_counts[band][tuple(sel[:, tok].tolist())] += 1

        self.top1_pair_freq = top1_pair / self.n_tokens
        self.cross_pair_freq = cross_pair / self.n_tokens
        self.overlap_freq = overlap / self.n_tokens
        self.depth_counts = depth_counts
        self.depth_initial = depth_initial
        self.chain_counts = chain_counts

        # log-conditional tables at each smoothing
        self.log_depth = {}
        self.log_initial = {}
        for s in SMOOTHINGS:
            for band in ("pdm", "all"):
                c = depth_counts[band] + s
                self.log_depth[(band, s)] = torch.log(c / c.sum(dim=2, keepdim=True))
                i0 = depth_initial[band] + s
                self.log_initial[(band, s)] = torch.log(i0 / i0.sum())

        # --- rarity thresholds from the FIT routine instances
        self.thresholds = {}
        vals = []
        for t in traces:
            ids = t.top_k_ids.long()
            top1 = ids[:, :, 0]
            for l in range(15):
                vals.append(self.top1_pair_freq[l][top1[l], top1[l + 1]])
        self.thresholds["top1_pair"] = float(
            torch.quantile(torch.cat(vals).double(), RARE_PCT / 100.0)
        )
        vals = []
        for t in traces:
            ids = t.top_k_ids.long()
            for l in range(15):
                a, b = ids[l], ids[l + 1]
                vals.append(
                    self.cross_pair_freq[l][a.unsqueeze(2), b.unsqueeze(1)].reshape(-1)
                )
        self.thresholds["cross_pair"] = float(
            torch.quantile(torch.cat(vals).double(), RARE_PCT / 100.0)
        )
        vals = []
        for t in traces:
            ids = t.top_k_ids.long()
            for l in range(15):
                ov = (ids[l].unsqueeze(2) == ids[l + 1].unsqueeze(1)).sum(dim=(1, 2))
                vals.append(self.overlap_freq[l][ov])
        self.thresholds["overlap"] = float(
            torch.quantile(torch.cat(vals).double(), RARE_PCT / 100.0)
        )

        # --- surprisal standardization (routine-fit mean/sd), per band/smoothing
        self.surp_mean = {}
        self.surp_sd = {}
        for band in ("pdm", "all"):
            for s in SMOOTHINGS:
                pooled = torch.cat([self.depth_surprisal(t.top_k_ids, band, s) for t in traces])
                self.surp_mean[(band, s)] = float(pooled.mean())
                self.surp_sd[(band, s)] = float(pooled.std()) + 1e-6

    # ---------------------------------------------------------- per-token readers
    def depth_surprisal(self, top_k_ids, band: str, smoothing: float) -> torch.Tensor:
        layers = PDM_LAYERS if band == "pdm" else ALL_LAYERS
        sel = top_k_ids.long()[list(layers), :, 0]
        total = self.log_initial[(band, smoothing)][sel[0]].clone()
        ld = self.log_depth[(band, smoothing)]
        for i in range(sel.shape[0] - 1):
            total = total + ld[i][sel[i], sel[i + 1]]
        return -total

    def unseen_links(self, top_k_ids, band: str) -> torch.Tensor:
        """[T] number of top-1 depth links with routine count 0."""
        layers = PDM_LAYERS if band == "pdm" else ALL_LAYERS
        sel = top_k_ids.long()[list(layers), :, 0]
        counts = self.depth_counts[band]
        out = torch.zeros(sel.shape[1], dtype=torch.float64)
        for i in range(sel.shape[0] - 1):
            out += (counts[i][sel[i], sel[i + 1]] == 0).double()
        return out

    def unseen_chain(self, top_k_ids, band: str) -> torch.Tensor:
        layers = PDM_LAYERS if band == "pdm" else ALL_LAYERS
        sel = top_k_ids.long()[list(layers), :, 0]
        cc = self.chain_counts[band]
        return torch.tensor(
            [0.0 if tuple(sel[:, t].tolist()) in cc else 1.0 for t in range(sel.shape[1])],
            dtype=torch.float64,
        )

    def pair_rare(self, top_k_ids) -> dict:
        """Per-token, per-layer-pair rarity indicators. Returns [15, T] tensors."""
        ids = top_k_ids.long()
        T = ids.shape[1]
        top1 = ids[:, :, 0]
        r1 = torch.zeros((15, T), dtype=torch.float64)
        r8 = torch.zeros((15, T), dtype=torch.float64)
        rov = torch.zeros((15, T), dtype=torch.float64)
        u1 = torch.zeros((15, T), dtype=torch.float64)
        for l in range(15):
            f = self.top1_pair_freq[l][top1[l], top1[l + 1]]
            r1[l] = (f < self.thresholds["top1_pair"]).double()
            u1[l] = (f == 0).double()
            a, b = ids[l], ids[l + 1]
            cf = self.cross_pair_freq[l][a.unsqueeze(2), b.unsqueeze(1)]  # [T,8,8]
            r8[l] = (cf < self.thresholds["cross_pair"]).double().mean(dim=(1, 2))
            ov = (a.unsqueeze(2) == b.unsqueeze(1)).sum(dim=(1, 2))
            rov[l] = (self.overlap_freq[l][ov] < self.thresholds["overlap"]).double()
        return {"top1_rare": r1, "cross_rare": r8, "overlap_rare": rov, "top1_unseen": u1}

    def wgm_windows(self, top_k_ids):
        ends, w = selection_rate_windows(top_k_ids, WGM_W, self.wgm_layers)
        if not ends.numel():
            return ends, None, None
        z = (w - self.mu) / self.sd - self.centre
        per_layer = (z**2).reshape(z.shape[0], len(self.wgm_layers), EXPERTS).sum(2)
        return ends, per_layer.sum(1), per_layer


# ---------------------------------------------------------------- evaluation
def window_mean(vec: torch.Tensor, width: int) -> torch.Tensor:
    if vec.numel() < width:
        return torch.empty(0, dtype=torch.float64)
    c = torch.cat([torch.zeros(1, dtype=torch.float64), vec.double().cumsum(0)])
    return (c[width:] - c[:-width]) / width


def summarize_region(ref: RoutineReference, trace, lo: int, hi: int) -> dict:
    ids = trace.top_k_ids
    out = {}
    # (1) marginal
    ends, g1, per_layer = ref.wgm_windows(ids)
    if ends is not None and ends.numel():
        mask = (ends >= lo + WGM_W - 1) & (ends < hi)
        if not bool(mask.any()):
            mask = (ends >= lo) & (ends < hi)
        sel = g1[mask]
        out["m_g1_median"] = float(sel.median()) if sel.numel() else None
        out["m_g1_max"] = float(sel.max()) if sel.numel() else None
        pl = per_layer[mask]
        out["m_g1_layer_median"] = [float(v) for v in pl.median(0).values] if pl.numel() else None
        out["m_windows"] = int(sel.numel())
    # (2) pairs
    rare = ref.pair_rare(ids)
    seg = slice(lo, hi)
    out["p_top1_rare"] = float(rare["top1_rare"][:, seg].mean())
    out["p_cross_rare"] = float(rare["cross_rare"][:, seg].mean())
    out["p_overlap_rare"] = float(rare["overlap_rare"][:, seg].mean())
    out["p_top1_unseen"] = float(rare["top1_unseen"][:, seg].mean())
    out["p_top1_rare_bylayer"] = [float(v) for v in rare["top1_rare"][:, seg].mean(1)]
    out["p_cross_rare_bylayer"] = [float(v) for v in rare["cross_rare"][:, seg].mean(1)]
    out["p_tok_any_top1_rare"] = float((rare["top1_rare"][:, seg].sum(0) > 0).double().mean())
    # (3) chains
    for band in ("pdm", "all"):
        ul = ref.unseen_links(ids, band)[seg]
        uc = ref.unseen_chain(ids, band)[seg]
        out[f"c_{band}_unseen_links"] = float(ul.mean())
        out[f"c_{band}_tok_any_unseen_link"] = float((ul > 0).double().mean())
        out[f"c_{band}_unseen_chain"] = float(uc.mean())
        wm = window_mean((ul > 0).double(), PDM_W)
        out[f"c_{band}_w4_max_novel_frac"] = float(wm.max()) if wm.numel() else None
        for s in SMOOTHINGS:
            raw = ref.depth_surprisal(ids, band, s)[seg]
            z = (raw - ref.surp_mean[(band, s)]) / ref.surp_sd[(band, s)]
            out[f"c_{band}_s{s}_raw_mean"] = float(raw.mean())
            out[f"c_{band}_s{s}_z_mean"] = float(z.mean())
            zw = window_mean(z, PDM_W)
            out[f"c_{band}_s{s}_z_w4max"] = float(zw.max()) if zw.numel() else None
    return out


def summarize_routine(ref: RoutineReference, traces) -> dict:
    g1s, pl_all = [], []
    tok = defaultdict(list)
    for t in traces:
        ids = t.top_k_ids
        ends, g1, per_layer = ref.wgm_windows(ids)
        if ends is not None and ends.numel():
            g1s.append(g1)
            pl_all.append(per_layer)
        rare = ref.pair_rare(ids)
        tok["top1_rare"].append(rare["top1_rare"].mean(0))
        tok["cross_rare"].append(rare["cross_rare"].mean(0))
        tok["overlap_rare"].append(rare["overlap_rare"].mean(0))
        tok["top1_unseen"].append(rare["top1_unseen"].mean(0))
        tok["top1_rare_bylayer"].append(rare["top1_rare"])
        tok["cross_rare_bylayer"].append(rare["cross_rare"])
        tok["any_top1_rare"].append((rare["top1_rare"].sum(0) > 0).double())
        for band in ("pdm", "all"):
            ul = ref.unseen_links(ids, band)
            tok[f"{band}_unseen_links"].append(ul)
            tok[f"{band}_any_unseen_link"].append((ul > 0).double())
            tok[f"{band}_unseen_chain"].append(ref.unseen_chain(ids, band))
            wm = window_mean((ul > 0).double(), PDM_W)
            if wm.numel():
                tok[f"{band}_w4_novel_frac"].append(wm)
            for s in SMOOTHINGS:
                raw = ref.depth_surprisal(ids, band, s)
                tok[f"{band}_s{s}_raw"].append(raw)
                z = (raw - ref.surp_mean[(band, s)]) / ref.surp_sd[(band, s)]
                tok[f"{band}_s{s}_z"].append(z)
                zw = window_mean(z, PDM_W)
                if zw.numel():
                    tok[f"{band}_s{s}_zw4"].append(zw)
    g1 = torch.cat(g1s)
    pl = torch.cat(pl_all)
    out = {
        "m_g1_median": float(g1.median()),
        "m_g1_q90": float(torch.quantile(g1.double(), 0.90)),
        "m_g1_layer_median": [float(v) for v in pl.median(0).values],
        "m_windows": int(g1.numel()),
        "n_tokens": int(sum(t.token_count for t in traces)),
    }
    for key, blocks in tok.items():
        if key.endswith("_bylayer"):
            m = torch.cat(blocks, dim=1)
            out[f"r_{key}_mean"] = [float(v) for v in m.mean(1)]
            continue
        v = torch.cat(blocks).double()
        out[f"r_{key}_mean"] = float(v.mean())
        out[f"r_{key}_median"] = float(v.median())
        out[f"r_{key}_q90"] = float(torch.quantile(v, 0.90))
        out[f"r_{key}_q99"] = float(torch.quantile(v, 0.99))
    return out


def main() -> None:
    labels = load_labels()
    batches = rio.load_core()
    traces = [t for k in ("b1", "b2") for t in batches[k]]
    routine = [t for t in traces if rio.arm_class(t) in ("clean", "benign")]
    drift = [t for t in traces if t.positive]
    groups = sorted({t.pair_group_id for t in routine})
    fold_of = {g: i % 2 for i, g in enumerate(groups)}
    folds = {f: [t for t in routine if fold_of[t.pair_group_id] == f] for f in (0, 1)}

    result = {"folds": {}, "config": {
        "wgm_layers": list(WGM_LAYERS), "pdm_layers": list(PDM_LAYERS),
        "region_len": REGION, "rare_percentile": RARE_PCT, "smoothings": list(SMOOTHINGS),
        "routine_traces": len(routine), "fold_sizes": {str(f): len(v) for f, v in folds.items()},
    }}
    for f in (0, 1):
        print(f"[fold {f}] fitting on {len(folds[f])} routine traces ...", flush=True)
        ref = RoutineReference(folds[f])
        block = {
            "fit_tokens": ref.n_tokens,
            "thresholds": ref.thresholds,
            "routine_holdout": summarize_routine(ref, folds[1 - f]),
            "drift": {},
        }
        print(f"[fold {f}] routine done; scoring {len(drift)} drift traces ...", flush=True)
        for t in drift:
            lab = labels[t.trace_id]
            entry = {"domain": t.domain, "batch": t.batch, "T": t.token_count,
                     "product_onset": lab["product_onset"], "evidence_onset": lab["evidence_onset"],
                     "product_class": lab["product_class"]}
            lo, hi = region_slice(t, lab["product_onset"])
            entry["product"] = summarize_region(ref, t, lo, hi)
            lo2, hi2 = region_slice(t, lab["evidence_onset"])
            entry["evidence"] = summarize_region(ref, t, lo2, hi2)
            block["drift"][t.trace_id] = entry
        result["folds"][str(f)] = block
        print(f"[fold {f}] done", flush=True)

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "joint_structure.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    print("wrote", OUT / "joint_structure.json")


if __name__ == "__main__":
    main()
