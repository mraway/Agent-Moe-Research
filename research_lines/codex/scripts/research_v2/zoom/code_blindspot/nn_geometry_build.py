"""LENS = nearest-neighbour geometry in the WGM feature space (diagnostic, post-hoc).

Builds w=8 top-8 selection-rate signatures (CAND-A's feature) for
  * every routine window (clean + benign_control) of a batch,
  * every CODE window (product_onset .. +48) of the 8 programming drifts,
  * every OTHER-DRIFT window (same rule) of the 51 non-programming drifts,
and computes, for each target window, its k=10 nearest routine windows of the
SAME batch, both in the raw signature space and in CAND-A's whitened space
(whitening refit on that batch's routine = clean + benign_control).

A routine-held-out reference is computed leave-one-trace-out: for every routine
window, the k=10 nearest routine windows drawn from *other* routine traces.

Read-only on traces/labels/frozen results.  Writes only under
artifacts/agent_v2/research_v2/zoom_code_blindspot/.
"""

from __future__ import annotations

import json
from pathlib import Path

import torch

from research_v2 import io as rio
from research_v2.scorers.wgm import WGMScorer

REPO = Path(__file__).resolve().parents[4]
OUT = REPO / "artifacts" / "agent_v2" / "research_v2" / "zoom_code_blindspot"
LABELS = REPO / "docs" / "research_v2" / "labels" / "product_onset_v1_adjudicated.jsonl"
WIDTH = 8
SPAN = 48
K = 10
BANDS = {"middle_late": "middle_late", "all": "all"}

torch.set_num_threads(32)


def load_labels() -> dict[str, dict]:
    rows = [json.loads(line) for line in LABELS.read_text().splitlines() if line.strip()]
    return {row["trace_id"]: row for row in rows}


def target_window_mask(ends: torch.Tensor, onset: int, tokens: int) -> torch.Tensor:
    """Windows fully inside [onset, min(onset+SPAN, T))."""
    stop = min(onset + SPAN, tokens)
    starts = ends - (WIDTH - 1)
    return (starts >= onset) & (ends <= stop - 1)


def knn(query: torch.Tensor, ref: torch.Tensor, k: int, block: int = 512,
        exclude: torch.Tensor | None = None, ref_trace: torch.Tensor | None = None):
    """Return (dists [Nq,k], idx [Nq,k]).  `exclude` = per-query trace index to drop."""
    d_out, i_out = [], []
    for s in range(0, query.shape[0], block):
        q = query[s : s + block]
        d = torch.cdist(q, ref)
        if exclude is not None:
            assert ref_trace is not None
            bad = exclude[s : s + block, None] == ref_trace[None, :]
            d = d.masked_fill(bad, float("inf"))
        vals, idx = torch.topk(d, k, dim=1, largest=False)
        d_out.append(vals)
        i_out.append(idx)
    return torch.cat(d_out), torch.cat(i_out)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    labels = load_labels()
    batches = rio.load_core()

    summary: dict[str, dict] = {}
    for band_name, band in BANDS.items():
        scorer = WGMScorer(window_width=WIDTH, layers=band, metric="g1")
        for batch, traces in batches.items():
            routine = [t for t in traces if rio.arm_class(t) in ("clean", "benign")]
            drift = [t for t in traces if rio.arm_class(t) == "drift"]
            state = scorer.fit(routine)

            # -- routine windows ------------------------------------------------
            r_feats, r_trace, r_end = [], [], []
            r_ids = []
            for ti, t in enumerate(routine):
                ends, win = scorer._windows(t)
                if not ends.numel():
                    continue
                r_feats.append(win)
                r_trace.append(torch.full((ends.numel(),), ti, dtype=torch.long))
                r_end.append(ends)
                r_ids.append(t.trace_id)
            R = torch.cat(r_feats).float()
            r_trace_t = torch.cat(r_trace)
            r_end_t = torch.cat(r_end)
            Rz = (R - state.mu) / state.sd

            # -- target windows -------------------------------------------------
            tgt_feats, tgt_meta = [], []
            for t in drift:
                lab = labels[t.trace_id]
                ends, win = scorer._windows(t)
                if not ends.numel():
                    continue
                for anchor_name, onset in (
                    ("product", int(lab["product_onset"])),
                    ("evidence", int(lab["evidence_onset"])),
                ):
                    mask = target_window_mask(ends, onset, t.token_count)
                    if not bool(mask.any()):
                        continue
                    sel = torch.nonzero(mask).flatten()
                    tgt_feats.append(win[sel])
                    for j in sel.tolist():
                        tgt_meta.append(
                            {
                                "trace_id": t.trace_id,
                                "domain": lab["domain"],
                                "product_class": lab["product_class"],
                                "anchor": anchor_name,
                                "onset": onset,
                                "end": int(ends[j]),
                                "tokens": int(t.token_count),
                            }
                        )
            Q = torch.cat(tgt_feats).float()
            Qz = (Q - state.mu) / state.sd

            res = {"n_routine_windows": int(R.shape[0]), "n_routine_traces": len(routine),
                   "n_target_windows": int(Q.shape[0]), "dim": int(R.shape[1])}

            for space, (qq, rr) in {"raw": (Q, R), "whitened": (Qz, Rz)}.items():
                d, idx = knn(qq, rr, K)
                res[f"target_d_{space}"] = d
                res[f"target_idx_{space}"] = idx
                dh, idxh = knn(rr, rr, K, exclude=r_trace_t, ref_trace=r_trace_t)
                res[f"routine_d_{space}"] = dh
                res[f"routine_idx_{space}"] = idxh
                res[f"target_norm_{space}"] = qq.norm(dim=1)
                res[f"routine_norm_{space}"] = rr.norm(dim=1)

            key = f"{band_name}|{batch}"
            torch.save(
                {
                    "meta": tgt_meta,
                    "routine_trace_ids": r_ids,
                    "routine_trace_idx": r_trace_t,
                    "routine_end": r_end_t,
                    **{k: v for k, v in res.items() if isinstance(v, torch.Tensor)},
                    "scalars": {k: v for k, v in res.items() if not isinstance(v, torch.Tensor)},
                },
                OUT / f"nn_{band_name}_{batch}.pt",
            )
            summary[key] = res["n_target_windows"], res["n_routine_windows"]
            print(key, res["n_routine_windows"], "routine windows;", res["n_target_windows"], "target windows", flush=True)

    (OUT / "nn_build_summary.json").write_text(json.dumps({k: list(v) for k, v in summary.items()}, indent=2))


if __name__ == "__main__":
    main()
