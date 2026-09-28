#!/usr/bin/env python3
"""Form-label statistics (T) and T/R agreement (prereg sections 2, 7.1-7.2).

Routine-only: tau and the k-means clusters are fitted on the S1 fitting-side routine pool
of each direction.  Drift and resisted-attack windows are described, never fitted.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import OUT, cases_for  # noqa: E402
from research_v2 import io as rio  # noqa: E402
from research_v2.features import MIDDLE_LATE_LAYERS, selection_rate_windows  # noqa: E402
from research_v2.harness import routine_traces  # noqa: E402
from research_v2.io import arm_class  # noqa: E402
from research_v2.scorers.fcm import kmeans, structured_fraction_windows  # noqa: E402

torch.set_num_threads(12)
WIDTH = 8
QUANTILE = 0.80


def _mutual_information(table: torch.Tensor) -> dict[str, float]:
    total = float(table.sum())
    joint = table / total
    px = joint.sum(1)
    py = joint.sum(0)
    mi = 0.0
    for i in range(joint.shape[0]):
        for j in range(joint.shape[1]):
            if joint[i, j] > 0:
                mi += float(joint[i, j]) * math.log(float(joint[i, j]) / float(px[i] * py[j]))
    hx = -sum(float(v) * math.log(float(v)) for v in px if v > 0)
    hy = -sum(float(v) * math.log(float(v)) for v in py if v > 0)
    nmi = 2 * mi / (hx + hy) if (hx + hy) > 0 else 0.0
    purity = float(table.amax(dim=1).sum() / total)
    return {"mi_nats": mi, "nmi": nmi, "purity_cluster_to_form": purity}


def main() -> None:
    batches = rio.load_core()
    cases = cases_for(batches)
    report: dict[str, dict] = {}
    for name, case in cases.items():
        fit_routine = routine_traces(case.fit_traces, "cb")
        fractions = torch.cat(
            [structured_fraction_windows(t.token_ids, WIDTH)[1] for t in fit_routine]
        )
        tau = float(torch.quantile(fractions, QUANTILE))

        block: dict = {"tau": tau, "fit_routine_traces": len(fit_routine)}
        # routine (fitting side) window/trace statistics
        per_trace = []
        for trace in fit_routine:
            _, values = structured_fraction_windows(trace.token_ids, WIDTH)
            flags = (values >= tau)
            per_trace.append(float(flags.double().mean()))
        per_trace_sorted = sorted(per_trace)
        block["fit_routine"] = {
            "windows": int(fractions.numel()),
            "structured_windows": int((fractions >= tau).sum()),
            "structured_window_share": float((fractions >= tau).double().mean()),
            "traces_with_any_structured_window": sum(1 for v in per_trace if v > 0),
            "traces_with_ge20pct_structured": sum(1 for v in per_trace if v >= 0.20),
            "trace_structured_share_quantiles": {
                q: round(per_trace_sorted[min(len(per_trace_sorted) - 1, int(q / 100 * len(per_trace_sorted)))], 4)
                for q in (10, 25, 50, 75, 90)
            },
        }
        # description of the target side by arm class (never fitted)
        by_arm: dict[str, dict] = {}
        for trace in case.target_traces:
            values = structured_fraction_windows(trace.token_ids, WIDTH)[1]
            if not values.numel():
                continue
            key = arm_class(trace)
            entry = by_arm.setdefault(key, {"traces": 0, "windows": 0, "structured": 0, "shares": []})
            entry["traces"] += 1
            entry["windows"] += int(values.numel())
            entry["structured"] += int((values >= tau).sum())
            entry["shares"].append(float((values >= tau).double().mean()))
        for key, entry in by_arm.items():
            shares = sorted(entry.pop("shares"))
            entry["structured_window_share"] = entry["structured"] / entry["windows"]
            entry["median_trace_share"] = shares[len(shares) // 2]
        block["target_by_arm"] = by_arm

        # per-domain description of drift traces
        by_domain: dict[str, dict] = {}
        for trace in case.target_traces:
            if not trace.positive:
                continue
            values = structured_fraction_windows(trace.token_ids, WIDTH)[1]
            if not values.numel():
                continue
            entry = by_domain.setdefault(trace.scenario_domain, {"traces": 0, "windows": 0, "structured": 0})
            entry["traces"] += 1
            entry["windows"] += int(values.numel())
            entry["structured"] += int((values >= tau).sum())
        for entry in by_domain.values():
            entry["structured_window_share"] = entry["structured"] / entry["windows"]
        block["drift_by_domain"] = by_domain

        # ---- T / R agreement on the fitting-side routine windows ----
        feature_blocks = []
        label_blocks = []
        for trace in fit_routine:
            ends, windows = selection_rate_windows(trace.top_k_ids, WIDTH, MIDDLE_LATE_LAYERS)
            if not ends.numel():
                continue
            values = structured_fraction_windows(trace.token_ids, WIDTH)[1][: ends.numel()]
            feature_blocks.append(windows)
            label_blocks.append((values >= tau).long())
        matrix = torch.cat(feature_blocks)
        labels = torch.cat(label_blocks)
        z = (matrix - matrix.mean(0)) / (matrix.std(0) + 1e-3)
        agreement: dict[str, dict] = {}
        for k in (2, 4, 8):
            centres = kmeans(z, k, seed=0)
            assign = torch.cdist(z, centres).argmin(dim=1)
            table = torch.zeros((k, 2), dtype=torch.float64)
            for cluster in range(k):
                mask = assign == cluster
                table[cluster, 0] = float((labels[mask] == 0).sum())
                table[cluster, 1] = float((labels[mask] == 1).sum())
            stats = _mutual_information(table)
            aligned = int(torch.argmax(table[:, 1] / table.sum(1).clamp_min(1)))
            tp = float(table[aligned, 1])
            stats.update(
                {
                    "contingency": table.tolist(),
                    "aligned_cluster": aligned,
                    "aligned_precision": tp / float(table[aligned].sum()) if table[aligned].sum() else 0.0,
                    "aligned_recall": tp / float(table[:, 1].sum()) if table[:, 1].sum() else 0.0,
                }
            )
            agreement[str(k)] = stats
        block["t_r_agreement"] = agreement
        report[name] = block
        print(name, "tau", tau, "structured share", block["fit_routine"]["structured_window_share"], flush=True)

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "form_stats.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print("written", OUT / "form_stats.json")


if __name__ == "__main__":
    main()
