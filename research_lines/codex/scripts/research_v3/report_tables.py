"""Markdown table generator for ``docs/research_v3/trm3_report.md``.

Read-only: every number comes from ``artifacts/agent_v2/research_v3/trm3/final_*/result.json``
(and, for the attribution / regime blocks, ``outputs.jsonl`` via ``report_outputs.py``).
The only arithmetic done here is formatting plus the frozen attainable-alpha formula
``alpha_eff = sum_c floor((n+1) w_c alpha) / (n+1)`` (``trm3.effective_alpha``), which the
runner records at alpha = 0.10 only and which section S3 needs at the other grid points.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "artifacts" / "agent_v2" / "research_v3" / "trm3"
CELLS = {"b1": "final_b1_both", "b2": "final_b2_both", "h384": "final_h384_both",
         "c1_heldout": "final_c1_heldout_C1"}
ORDER = ["b2", "b1", "h384", "c1_heldout"]
VARIANTS = ["trm3", "m_only", "s_only", "j_only", "sm", "mj", "sj",
            "unseen_only", "surprisal_marginal", "no_temporal", "no_temporal2"]
LABEL = {"trm3": "TRM-3", "m_only": "B-M (m_only)", "s_only": "A-S (s_only)",
         "j_only": "A-J (j_only)", "sm": "A-SM", "mj": "A-MJ", "sj": "A-SJ",
         "unseen_only": "B-U (degenerate)", "surprisal_marginal": "B-S",
         "no_temporal": "B-NT", "no_temporal2": "B-NT2"}
CELL_LABEL = {"b2": "b2 (B1->B2)", "b1": "b1 (B2->B1)", "h384": "h384 replay",
              "c1_heldout": "c1 fold 4"}
_C: dict[str, dict] = {}


def load(cell: str) -> dict:
    if cell not in _C:
        _C[cell] = json.loads((BASE / CELLS[cell] / "result.json").read_text())
    return _C[cell]


def f(x, nd=3):
    if x is None:
        return "-"
    if isinstance(x, bool):
        return "yes" if x else "**NO**"
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def blocks():
    for cell in ORDER:
        d = load(cell)
        for col, cdata in d["columns"].items():
            for variant in VARIANTS:
                vdata = cdata["variants"][variant]
                for set_name, block in vdata["sets"].items():
                    yield cell, col, variant, set_name, vdata, block


def alpha_eff(n_reference: int, alpha: float, weights=(0.2, 0.4, 0.4)) -> float:
    total = 0.0
    for w in weights:
        rank = max(0, min(int(math.floor((n_reference + 1) * w * alpha + 1e-12)), n_reference + 1))
        total += rank / (n_reference + 1.0)
    return total


def t_far() -> str:
    out = ["| target | column | variant | set | traces | alpha_eff | FAR clean | FAR benign |"
           " FAR pooled | matched-group | half gap | alarm ep/1k | onsets/1k |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for cell, col, variant, set_name, vdata, b in blocks():
        if cell == "c1_heldout" and set_name != "target":
            continue
        far, ep = b["far"], b["endpoint"]
        out.append(
            f"| {CELL_LABEL[cell]} | {col} | {LABEL[variant]} | {set_name} | {b['trace_count']} |"
            f" {f(vdata['alpha_budget']['alpha_eff'])} | {f(far['clean'])} | {f(far['benign'])} |"
            f" {f(far['pooled'])} | {f(far.get('matched_group'))} | {f(b['far_half_gap'])} |"
            f" {f(ep['alarm_endpoint_rate'] * 1000, 1)} |"
            f" {f(ep['alarm_onsets_per_1000_eligible'])} |"
        )
    return "\n".join(out)


def t_recall(kind: str) -> str:
    key = {"strict": "recall_strict", "tolerant": "recall_tolerant",
           "secondary": "recall_strict_secondary",
           "secondary_tolerant": "recall_tolerant_secondary"}[kind]
    out = ["| target | column | variant | positives | +8 | +16 | +32 | +64 | final |"
           " pre-onset | latency median | latency p90 |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for cell, col, variant, set_name, vdata, b in blocks():
        if set_name != "target":
            continue
        r = b.get(key)
        if not r:
            continue
        out.append(
            f"| {CELL_LABEL[cell]} | {col} | {LABEL[variant]} | {r['positive_count']} |"
            f" {f(r['recall_plus_8'])} | {f(r['recall_plus_16'])} | {f(r['recall_plus_32'])} |"
            f" {f(r['recall_plus_64'])} | {f(r['recall_final'])} | {f(r['pre_onset_rate'])} |"
            f" {f(r['latency_median'], 1)} | {f(r['latency_p90'], 1)} |"
        )
    return "\n".join(out)


def t_gates() -> str:
    out = ["| target | column | variant | G1 | G2 | G3 | G4 | G5 | G6 | G7 | G8 | failed |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for cell in ORDER:
        d = load(cell)
        for col, cdata in d["columns"].items():
            for variant in VARIANTS:
                g = cdata["gates"][variant]
                cells = []
                for gk in ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8"):
                    blk = g[gk]
                    if blk["status"] != "evaluated":
                        cells.append("n/e")
                    else:
                        cells.append(("" if blk["pass"] else "**FAIL** ") + f(blk["value"]))
                failed = ", ".join(g["summary"]["failed"]) or "-"
                out.append(
                    f"| {CELL_LABEL[cell]} | {col} | {LABEL[variant]} | " + " " + " | ".join(cells)
                    + f" | {failed} |"
                )
    return "\n".join(out)


def t_mcnemar() -> str:
    out = ["| target | column | comparison | pairs | both | neither | only a | only b |"
           " net (a-b) | p |", "|---|---|---|---|---|---|---|---|---|---|"]
    for cell in ["b2", "b1", "h384"]:
        d = load(cell)
        for col, cdata in d["columns"].items():
            for variant in VARIANTS:
                if variant == "m_only":
                    continue
                m = cdata["mcnemar"][variant]
                out.append(
                    f"| {CELL_LABEL[cell]} | {col} | {LABEL[variant]} vs B-M (nominal 0.10) |"
                    f" {m['pair_count']} | {m['both']} | {m['neither']} | {m['only_a']} |"
                    f" {m['only_b']} | {m['net_gain_a_over_b']} | {f(m['p_value'], 4)} |"
                )
            pm = cdata["p1_matched_alpha"]
            m = pm["mcnemar_matched"]
            out.append(
                f"| {CELL_LABEL[cell]} | {col} | **TRM-3 vs B-M at alpha_matched ="
                f" {f(pm['alpha_matched'], 4)}** | {m['pair_count']} | {m['both']} |"
                f" {m['neither']} | {m['only_a']} | {m['only_b']} |"
                f" {m['net_gain_a_over_b']} | {f(m['p_value'], 4)} |"
            )
    return "\n".join(out)


def t_alpha_grid() -> str:
    out = ["| target | column | variant | alpha | alpha_eff (frozen formula) | FAR clean |"
           " FAR benign | FAR pooled | matched-group | recall +8 | recall final |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    for cell, col, variant, set_name, vdata, b in blocks():
        if set_name != "target" or variant not in ("trm3", "m_only", "s_only", "j_only"):
            continue
        halves = vdata["alpha_budget"]["by_half"]
        n_ref = min(int(h["n_reference"]) for h in halves.values())
        for key, g in (b.get("alpha_grid") or {}).items():
            a = g["alpha"]
            if variant == "trm3":
                ae = alpha_eff(n_ref, a)
            else:
                rank = max(0, min(int(math.floor((n_ref + 1) * a + 1e-12)), n_ref + 1))
                ae = rank / (n_ref + 1.0)
            r = g.get("recall") or {}
            out.append(
                f"| {CELL_LABEL[cell]} | {col} | {LABEL[variant]} | {f(a, 2)} | {f(ae)} |"
                f" {f(g['far']['clean'])} | {f(g['far']['benign'])} | {f(g['far']['pooled'])} |"
                f" {f(g['far'].get('matched_group'))} | {f(r.get('recall_plus_8'))} |"
                f" {f(r.get('recall_final'))} |"
            )
    return "\n".join(out)


def t_worst(dim: str) -> str:
    out = ["| target | column | group | traces | normals | FAR | positives | recall +8 |",
           "|---|---|---|---|---|---|---|---|"]
    for cell in ["b2", "b1", "h384"]:
        d = load(cell)
        for col, cdata in d["columns"].items():
            wg = cdata["variants"]["trm3"]["sets"]["target"]["worst_group"][dim]
            for name, g in sorted(wg["groups"].items()):
                out.append(
                    f"| {CELL_LABEL[cell]} | {col} | {name} | {g['trace_count']} |"
                    f" {g['normal_count']} | {f(g['far'])} | {g['positive_count']} |"
                    f" {f(g['recall_plus_8'])} |"
                )
    return "\n".join(out)


def t_worst_summary() -> str:
    out = ["| target | column | dimension | worst FAR group | worst recall+8 group |",
           "|---|---|---|---|---|"]
    for cell in ["b2", "b1", "h384"]:
        d = load(cell)
        for col, cdata in d["columns"].items():
            wg = cdata["variants"]["trm3"]["sets"]["target"]["worst_group"]
            for dim in ("workflow", "channel", "domain", "length_tertile"):
                wf = wg[dim].get("worst_far")
                wr = wg[dim].get("worst_recall_plus_8")
                out.append(
                    f"| {CELL_LABEL[cell]} | {col} | {dim} |"
                    f" {wf[0] if wf else '-'} = {f(wf[1]) if wf else '-'} |"
                    f" {wr[0] if wr else '-'} = {f(wr[1]) if wr else '-'} |"
                )
    return "\n".join(out)


def t_temporal() -> str:
    out = ["| column | variant | class | traces | NONE | UNCERTAIN | SUSTAINED | RECOVERING |"
           " any-alarm | abstention | censored | earliest decision median |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    d = load("h384")
    for col, cdata in d["columns"].items():
        for variant in ("trm3", "m_only"):
            t = cdata["variants"][variant]["sets"]["target"]["temporal"]
            for name, b in t["confusion"].items():
                st = b["states"]
                out.append(
                    f"| {col} | {LABEL[variant]} | {name} | {b['trace_count']} |"
                    f" {st.get('NONE', 0)} | {st.get('UNCERTAIN', 0)} | {st.get('SUSTAINED', 0)} |"
                    f" {st.get('RECOVERING', 0)} | {f(b['any_alarm_rate'])} |"
                    f" {f(b['abstention_rate'])} | {f(b['censored_rate'])} |"
                    f" {f(b['earliest_decision_median'], 1)} |"
                )
    return "\n".join(out)


def t_cost() -> str:
    out = ["| target | column | variant | scored endpoints | scoring seconds |"
           " seconds / 1000 endpoints | reference floats |",
           "|---|---|---|---|---|---|---|"]
    for cell, col, variant, set_name, vdata, b in blocks():
        if set_name != "target" or variant not in ("trm3", "m_only", "s_only", "j_only",
                                                   "surprisal_marginal"):
            continue
        c = vdata["cost"]
        out.append(
            f"| {CELL_LABEL[cell]} | {col} | {LABEL[variant]} | {c['scored_endpoints']} |"
            f" {f(c['scoring_seconds'])} | {f(c['seconds_per_1000_endpoints'], 4)} |"
            f" {c['reference_floats']} |"
        )
    return "\n".join(out)


def t_budget() -> str:
    out = ["| target | column | half | n_cal | alpha_S rank / attainable |"
           " alpha_M rank / attainable | alpha_J rank / attainable | alpha_eff |"
           " B-M alpha_matched |", "|---|---|---|---|---|---|---|---|---|"]
    for cell in ORDER:
        d = load(cell)
        for col, cdata in d["columns"].items():
            ab = cdata["variants"]["trm3"]["alpha_budget"]
            for half, h in ab["by_half"].items():
                ch = h["effective"]["channels"]
                out.append(
                    f"| {CELL_LABEL[cell]} | {col} | {half} | {h['n_reference']} |"
                    + " " + " | ".join(
                        f"{ch[c]['rank']} / {f(ch[c]['attainable'], 4)}" for c in ("S", "M", "J")
                    )
                    + f" | {f(h['effective']['alpha_eff'], 4)} |"
                    f" {f(h['matched']['alpha_matched'], 4)} |"
                )
    return "\n".join(out)


TABLES = {
    "far": t_far, "recall_strict": lambda: t_recall("strict"),
    "recall_tolerant": lambda: t_recall("tolerant"),
    "recall_secondary": lambda: t_recall("secondary"),
    "recall_secondary_tolerant": lambda: t_recall("secondary_tolerant"),
    "gates": t_gates, "mcnemar": t_mcnemar, "alpha_grid": t_alpha_grid,
    "worst_summary": t_worst_summary, "worst_domain": lambda: t_worst("domain"),
    "worst_length": lambda: t_worst("length_tertile"),
    "worst_workflow": lambda: t_worst("workflow"), "worst_channel": lambda: t_worst("channel"),
    "temporal": t_temporal, "cost": t_cost, "budget": t_budget,
}


def main() -> int:
    name = sys.argv[1]
    if name == "list":
        print("\n".join(TABLES))
        return 0
    print(TABLES[name]())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
