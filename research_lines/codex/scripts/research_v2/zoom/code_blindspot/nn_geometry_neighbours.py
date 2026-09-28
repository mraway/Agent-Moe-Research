"""What ARE the nearest routine windows of a code window?  (diagnostic, post-hoc)

Decodes every top-10 nearest routine window of every CODE / OTHER-DRIFT window and
classifies it with a deterministic surface-form ladder, then compares the neighbour
mix against the routine base rate.  Also labels each *target* window's own text as
code-body vs prose so the two regimes can be separated.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import torch

from research_v2 import io as rio
from research_v2.scorers.wgm import WGMScorer

REPO = Path(__file__).resolve().parents[4]
OUT = REPO / "artifacts" / "agent_v2" / "research_v2" / "zoom_code_blindspot"

# -- surface-form ladder (evaluated in this order; first match wins) -----------
RE_JSON_ACTION = re.compile(r'"(type|name|arguments)"|"action"|\{\}|\}\}|","\w')
RE_JSON_ANY = re.compile(r'"\s*:|\{"|"\}|":"')
RE_LIST_MD = re.compile(r"\*\*|^\s*[-*]\s|\n\s*[-*]\s|\n\s*\d+\.|#{1,3}\s")
RE_ID = re.compile(r"\b(ORD|WAR|CASE|SUB|TCK|INV|RMA|REF)-?\d|\d{4}-\d{2}-\d{2}|\$\d")
RE_SIGNOFF = re.compile(
    r"(?i)(feel free|let me know|hope this|thank you|thanks for|apolog|"
    r"happy to help|please (reach|contact|let)|best regards|sincerely|"
    r"is there anything|if you (have|need)|assist you further)"
)
RE_KB = re.compile(
    r"(?i)(polic(y|ies)|knowledge base|article|according to|covers?\b|eligib|"
    r"terms and conditions|warrant(y|ies)|clause|section \d)"
)


def form_label(text: str) -> str:
    if RE_JSON_ACTION.search(text):
        return "json_tool_call"
    if RE_JSON_ANY.search(text):
        return "json_field"
    if RE_LIST_MD.search(text):
        return "list_markdown"
    if RE_ID.search(text):
        return "ids_dates_numbers"
    if RE_SIGNOFF.search(text):
        return "sign_off"
    if RE_KB.search(text):
        return "kb_recitation"
    return "prose"


RE_CODEBODY = re.compile(
    r"(?i)(```|SELECT\s|FROM\s|WHERE\s|GROUP BY|ORDER BY|JOIN |COUNT\(|SUM\(|AS total|"
    r"\bdef \b|\breturn \b|\bfn \b|\blet \b|\bconst \b|\bfunction\b|=>|::|"
    r"\bimport \b|\bfor \b\w+ in |\bif __|\bpub fn|\bstruct \b|_id\b|\w+\(\)|;\n|\{\n)"
)
RE_CODEISH = re.compile(r"[_(){};=<>\[\]]|\bAS\b|\bSELECT\b|\bDESC\b|\bASC\b")


def own_label(text: str) -> str:
    """code_body if the window looks like a code/SQL body, else prose_about."""
    if RE_CODEBODY.search(text):
        return "code_body"
    punct = sum(ch in "_(){};=<>[]#|" for ch in text)
    if punct >= 3 or (RE_CODEISH.search(text) and punct >= 2):
        return "code_body"
    return "prose_about"


def main(band: str = "middle_late", anchor: str = "product", space: str = "whitened") -> None:
    scorer = WGMScorer(window_width=8, layers=band, metric="g1")
    batches = rio.load_core()
    report: dict = {"band": band, "anchor": anchor, "space": space, "batches": {}}
    examples: list[str] = []

    for batch, traces in batches.items():
        cache = torch.load(OUT / f"nn_{band}_{batch}.pt", weights_only=False)
        routine = [t for t in traces if rio.arm_class(t) in ("clean", "benign")]
        assert [t.trace_id for t in routine] == cache["routine_trace_ids"]
        drift_by_id = {t.trace_id: t for t in traces if rio.arm_class(t) == "drift"}
        rt, re_ = cache["routine_trace_idx"], cache["routine_end"]

        # routine window texts + base-rate form mix
        r_text: list[str] = []
        for gi in range(rt.numel()):
            ti, e = int(rt[gi]), int(re_[gi])
            r_text.append(rio.decode_text(routine[ti].token_ids[e - 7 : e + 1]))
        r_form = [form_label(t) for t in r_text]
        base = Counter(r_form)
        n_base = len(r_form)

        meta = cache["meta"]
        idx = cache[f"target_idx_{space}"]
        dist = cache[f"target_d_{space}"]

        per_trace: dict[str, dict] = {}
        by_group: dict[str, Counter] = defaultdict(Counter)
        by_group_own: dict[str, Counter] = defaultdict(Counter)
        by_group_split: dict[str, Counter] = defaultdict(Counter)
        for i, m in enumerate(meta):
            if m["anchor"] != anchor:
                continue
            tr = drift_by_id[m["trace_id"]]
            own = rio.decode_text(tr.token_ids[m["end"] - 7 : m["end"] + 1])
            ol = own_label(own)
            grp = "code" if m["domain"] == "programming" else "other"
            nb_forms = [r_form[int(j)] for j in idx[i]]
            nb_traces = {int(rt[int(j)]) for j in idx[i]}
            nb_workflows = {routine[int(rt[int(j)])].workflow for j in idx[i]}
            for f in nb_forms:
                by_group[grp][f] += 1
                by_group[f"dom:{m['domain']}"][f] += 1
                if grp == "code":
                    by_group_split[f"code|{ol}"][f] += 1
            by_group_own[grp][ol] += 1
            rec = per_trace.setdefault(
                m["trace_id"],
                {
                    "domain": m["domain"],
                    "product_class": m["product_class"],
                    "workflow": tr.workflow,
                    "n_windows": 0,
                    "forms": Counter(),
                    "own": Counter(),
                    "nb_trace_ids": Counter(),
                    "nb_workflows": Counter(),
                    "same_workflow_nb": 0,
                    "nb_total": 0,
                },
            )
            rec["n_windows"] += 1
            rec["forms"].update(nb_forms)
            rec["own"][ol] += 1
            for j in idx[i]:
                ti = int(rt[int(j)])
                rec["nb_trace_ids"][routine[ti].trace_id] += 1
                rec["nb_workflows"][routine[ti].workflow] += 1
                rec["same_workflow_nb"] += int(routine[ti].workflow == tr.workflow)
                rec["nb_total"] += 1
            if m["domain"] == "programming" and m["end"] % 16 == 7:
                examples.append(
                    f"{m['trace_id']} end={m['end']} own[{ol}]={own!r}\n"
                    + "\n".join(
                        f"    d={float(dist[i,j]):7.2f} [{r_form[int(idx[i,j])]}] "
                        f"{r_text[int(idx[i,j])]!r} ({routine[int(rt[int(idx[i,j])])].trace_id})"
                        for j in range(5)
                    )
                )

        def pct(c: Counter) -> dict:
            n = sum(c.values())
            return {k: round(v / n, 4) for k, v in c.most_common()} | {"_n": n}

        report["batches"][batch] = {
            "routine_base_rate": pct(base) | {"_n": n_base},
            "neighbour_mix": {g: pct(c) for g, c in by_group.items()},
            "code_split_by_own_form": {g: pct(c) for g, c in by_group_split.items()},
            "own_form_mix": {g: pct(c) for g, c in by_group_own.items()},
            "per_trace": {
                tid: {
                    "domain": r["domain"],
                    "product_class": r["product_class"],
                    "workflow": r["workflow"],
                    "n_windows": r["n_windows"],
                    "own_form": dict(r["own"]),
                    "neighbour_forms": pct(r["forms"]),
                    "distinct_neighbour_traces": len(r["nb_trace_ids"]),
                    "top_neighbour_traces": r["nb_trace_ids"].most_common(3),
                    "distinct_neighbour_workflows": len(r["nb_workflows"]),
                    "same_workflow_share": round(r["same_workflow_nb"] / r["nb_total"], 4),
                }
                for tid, r in per_trace.items()
            },
        }
        print(f"=== {batch} routine base rate: {pct(base)}", flush=True)

    (OUT / f"neighbours_{band}_{anchor}_{space}.json").write_text(json.dumps(report, indent=2))
    (OUT / f"neighbour_examples_{band}_{anchor}_{space}.txt").write_text("\n".join(examples))


if __name__ == "__main__":
    import sys
    main(*(sys.argv[1:] or []))
