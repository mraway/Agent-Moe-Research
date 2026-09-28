#!/usr/bin/env python3
"""PART B of the no-JSON-routine counterfactual: the matched, no-conformal diagnostic.

Fit the candidate's statistic on PROSE-ONLY routine traces of one batch and take the
reference distribution to be PROSE-ONLY routine windows of the same batch (the matched
version that `whitening_lens.md` section 5 never ran: there the whitening was prose-only
but the reference distribution was still ALL routine windows).

Routine percentiles are leave-one-trace-out: a routine trace's own windows are removed
from the reference before its percentiles / tail fractions are computed.

For each drift trace the product window set is every window ENDING in
``product_onset .. min(product_onset + 48, T-1)``.

Also reported, for contrast: the same statistic against the UNMATCHED reference (all
clean+benign windows of the fitting batch, prose and JSON-shaped alike).

Usage:
    PYTHONPATH=$PWD/src:$PWD/scripts python scripts/research_v2/zoom/code_blindspot/no_json_partB.py
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from research_v2 import io as rio  # noqa: E402
from research_v2.harness import routine_traces  # noqa: E402
from research_v2.scorers import build as build_scorer  # noqa: E402

OUT = ROOT / "artifacts" / "agent_v2" / "research_v2" / "no_json_routine"
LABELS = ROOT / "docs" / "research_v2" / "labels" / "product_onset_v1_adjudicated.jsonl"
PRODUCT_SPAN = 48

CANDIDATES = {
    "CAND-A": ("wgm", {"metric": "g1", "layers": "middle_late"}, 8),
    "CAND-B": ("pdm", {"model": "d1", "layers": "middle"}, 4),
}


def percentile_of(reference: torch.Tensor, values: torch.Tensor) -> torch.Tensor:
    """Fraction of ``reference`` strictly below each value (searchsorted, left)."""

    idx = torch.searchsorted(reference, values.contiguous(), right=False)
    return idx.to(torch.float64) / float(reference.numel())


def main() -> None:
    batches = rio.load_core()
    product = {}
    for line in LABELS.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            product[row["trace_id"]] = row

    out: dict = {"product_span": PRODUCT_SPAN, "candidates": {}}
    for cand, (scorer_name, cfg, width) in CANDIDATES.items():
        out["candidates"][cand] = {"scorer": scorer_name, "config": cfg, "window_width": width}
        for fit_batch in ("b1", "b2"):
            scorer = build_scorer(scorer_name, {**cfg, "window_width": width})
            prose = routine_traces(batches[fit_batch], "cb_prose")
            allcb = routine_traces(batches[fit_batch], "cb")
            state = scorer.fit(prose)

            routine_scores = {}
            for t in prose:
                s, e = scorer.score(state, t)
                if e.numel():
                    routine_scores[t.trace_id] = s.to(torch.float64)
            all_cb_scores = []
            for t in allcb:
                s, e = scorer.score(state, t)
                if e.numel():
                    all_cb_scores.append(s.to(torch.float64))
            prose_ref = torch.sort(torch.cat(list(routine_scores.values())))[0]
            all_ref = torch.sort(torch.cat(all_cb_scores))[0]

            def tails(ref: torch.Tensor) -> tuple[float, float]:
                n = ref.numel()
                return (
                    float(ref[min(n - 1, int(0.95 * n))]),
                    float(ref[min(n - 1, int(0.99 * n))]),
                )

            prose_q95, prose_q99 = tails(prose_ref)
            all_q95, all_q99 = tails(all_ref)

            per_trace: list[dict] = []
            # drift traces, both batches
            for batch in ("b1", "b2"):
                for t in batches[batch]:
                    if not t.positive:
                        continue
                    s, e = scorer.score(state, t)
                    if not e.numel():
                        continue
                    onset = product[t.trace_id]["product_onset"]
                    mask = (e >= onset) & (e <= onset + PRODUCT_SPAN)
                    vals = s.to(torch.float64)[mask]
                    if not vals.numel():
                        continue
                    pct = percentile_of(prose_ref, vals)
                    pct_all = percentile_of(all_ref, vals)
                    per_trace.append(
                        {
                            "trace_id": t.trace_id,
                            "batch": batch,
                            "domain": t.scenario_domain,
                            "kind": "drift",
                            "n_windows": int(vals.numel()),
                            "median_percentile_prose_ref": float(pct.median()),
                            "frac_above_prose_q95": float((vals >= prose_q95).to(torch.float64).mean()),
                            "frac_above_prose_q99": float((vals >= prose_q99).to(torch.float64).mean()),
                            "median_percentile_all_ref": float(pct_all.median()),
                            "frac_above_all_q95": float((vals >= all_q95).to(torch.float64).mean()),
                            "frac_above_all_q99": float((vals >= all_q99).to(torch.float64).mean()),
                        }
                    )
            # prose routine traces, leave-one-trace-out
            for tid, vals in routine_scores.items():
                others = torch.sort(
                    torch.cat([v for k, v in routine_scores.items() if k != tid])
                )[0]
                n = others.numel()
                q95 = float(others[min(n - 1, int(0.95 * n))])
                q99 = float(others[min(n - 1, int(0.99 * n))])
                pct = percentile_of(others, vals)
                per_trace.append(
                    {
                        "trace_id": tid,
                        "batch": fit_batch,
                        "domain": "routine_prose",
                        "kind": "routine_prose_loo",
                        "n_windows": int(vals.numel()),
                        "median_percentile_prose_ref": float(pct.median()),
                        "frac_above_prose_q95": float((vals >= q95).to(torch.float64).mean()),
                        "frac_above_prose_q99": float((vals >= q99).to(torch.float64).mean()),
                        "median_percentile_all_ref": float(percentile_of(all_ref, vals).median()),
                        "frac_above_all_q95": float((vals >= all_q95).to(torch.float64).mean()),
                        "frac_above_all_q99": float((vals >= all_q99).to(torch.float64).mean()),
                    }
                )
            out["candidates"][cand][f"fit_{fit_batch}"] = {
                "fit_prose_trace_count": len(prose),
                "prose_reference_window_count": int(prose_ref.numel()),
                "all_cb_reference_window_count": int(all_ref.numel()),
                "prose_q95": prose_q95,
                "prose_q99": prose_q99,
                "prose_median": float(prose_ref[prose_ref.numel() // 2]),
                "all_q95": all_q95,
                "all_q99": all_q99,
                "all_median": float(all_ref[all_ref.numel() // 2]),
                "per_trace": per_trace,
                "by_domain": by_domain(per_trace),
            }
            print(cand, fit_batch, "done", flush=True)

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "partB_diagnostic.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print("wrote", OUT / "partB_diagnostic.json")


def by_domain(rows: list[dict]) -> dict:
    result: dict = {}
    for domain in sorted({r["domain"] for r in rows}):
        sub = [r for r in rows if r["domain"] == domain]
        result[domain] = {
            "n_traces": len(sub),
            "median_of_window_median_percentile_prose_ref": statistics.median(
                r["median_percentile_prose_ref"] for r in sub
            ),
            "median_frac_above_prose_q95": statistics.median(
                r["frac_above_prose_q95"] for r in sub
            ),
            "mean_frac_above_prose_q95": statistics.fmean(r["frac_above_prose_q95"] for r in sub),
            "median_frac_above_prose_q99": statistics.median(
                r["frac_above_prose_q99"] for r in sub
            ),
            "mean_frac_above_prose_q99": statistics.fmean(r["frac_above_prose_q99"] for r in sub),
            "median_of_window_median_percentile_all_ref": statistics.median(
                r["median_percentile_all_ref"] for r in sub
            ),
            "median_frac_above_all_q95": statistics.median(r["frac_above_all_q95"] for r in sub),
            "median_frac_above_all_q99": statistics.median(r["frac_above_all_q99"] for r in sub),
        }
    return result


if __name__ == "__main__":
    main()
