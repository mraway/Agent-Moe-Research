"""Sample-level audit step 2 (read-only): decode and classify every alarm window.

For each frozen final cell this joins the endpoint index built by
``audit_sample_level_index.py`` with the cached decode token ids (``rio.decode_token_texts``)
and the frozen anchor labels, and emits, per trace of interest:

* the TRM-3 and B-M (m_only) first-alarm endpoints at nominal and matched alpha,
* the attribution channel at the TRM-3 first alarm and the channel p-values there,
* the decoded 8-token evidence window and the surrounding text,
* a window classification: anchor region (pre-onset / spanning / post-onset) and
  surface form (json scaffolding / prose), plus off-domain topic-mention detection.

Read-only: no artifact, label, trace, source or prereg file is written or modified.
All numbers are development-set evidence (B1/B2, h384 replay, C1).
"""

from __future__ import annotations

import json
import pickle
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import research_v2.io as rio  # noqa: E402
from research_v2 import trm3  # noqa: E402

SP = Path(
    "/tmp/claude-1000/-home-wzh-Agent-Moe-Research--claude-worktrees-algorithm-research-proposals-427363"
    "/7e87c1f8-78bb-4b9f-9189-12780e6e8833/scratchpad"
)
LABELS = ROOT / "docs" / "research_v2" / "labels"
PRODUCT_ONSET_FILE = LABELS / "product_onset_v1_adjudicated.jsonl"
TOPIC_ENTRY_FILE = LABELS / "topic_entry_v1_adjudicated.jsonl"

W = 8  # TRM-3 evidence window width (frozen)

DOMAIN_TERMS = {
    "travel_planning": ["route", "metro", "itinerar", "flight", "hotel", "transit", "train",
                        "station", "trip", "travel", "tour", "museum", "walk", "bus", "line",
                        "montreal", "kyoto", "lisbon", "airport", "sightsee"],
    "cooking": ["recipe", "ingredient", "cup", "tbsp", "tsp", "bake", "oven", "saute", "sauté",
                "simmer", "dough", "flour", "grocery", "meal", "cook", "dish", "season", "pan"],
    "poetry": ["verse", "stanza", "poem", "rhyme", "sonnet", "limerick", "quatrain", "haiku",
               "metaphor", "lyric", "free verse", "monologue"],
    "fiction": ["story", "character", "scene", "narrat", "chapter", "dialogue", "fable",
                "protagonist", "plot", "novel", "monologue"],
    "mathematics": ["theorem", "proof", "lemma", "equation", "integral", "derivative", "sum",
                    "geometry", "triangle", "angle", "matrix", "algebra", "qed", "derivation"],
    "programming": ["function", "code", "python", "variable", "loop", "class ", "def ",
                    "return ", "algorithm", "compile", "array", "script", "api", "import"],
    "legal_analysis": ["clause", "statute", "liab", "contract", "jurisdic", "counsel",
                       "legal", "law", "court", "provision", "indemn", "attorney"],
    "general_knowledge": ["history", "science", "invent", "discover", "century", "planet",
                          "species", "capital of", "population", "war ", "empire"],
}

JSON_CHARS = set('{}[]":,')


def read_jsonl(path: Path):
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def core_anchor_map():
    by_id = {}
    for row in read_jsonl(PRODUCT_ONSET_FILE):
        value = row.get("product_onset", row.get("evidence_onset"))
        by_id[str(row["trace_id"])] = None if value is None else int(value)
    for row in read_jsonl(TOPIC_ENTRY_FILE):
        value = row.get("topic_entry_onset")
        by_id.setdefault(str(row["trace_id"]), None if value is None else int(value))
    return by_id


def window_form(text: str) -> str:
    """Surface form of an evidence window: json scaffolding vs prose."""
    stripped = text.strip()
    if not stripped:
        return "empty"
    json_ratio = sum(1 for ch in stripped if ch in JSON_CHARS) / len(stripped)
    alpha = sum(1 for ch in stripped if ch.isalpha())
    if json_ratio >= 0.15 or (alpha / len(stripped) < 0.5 and json_ratio > 0.05):
        return "json_scaffolding"
    return "prose"


def topic_hits(text: str, domain: str) -> list[str]:
    low = text.lower()
    return [t for t in DOMAIN_TERMS.get(domain, []) if t in low]


def classify(trace_texts, start, end, anchor, domain):
    """Classify the evidence window ``[start, end]`` of one trace."""
    win = "".join(trace_texts[max(0, start): end + 1])
    ctx = "".join(trace_texts[max(0, start - 12): min(len(trace_texts), end + 13)])
    block = {
        "window_tokens": [start, end],
        "window_text": win,
        "context_text": ctx,
        "form": window_form(win),
        "form_context": window_form(ctx),
        "topic_terms_in_window": topic_hits(win, domain),
        "topic_terms_in_context": topic_hits(ctx, domain),
    }
    if anchor is None:
        block["anchor_region"] = "no_anchor"
    elif end < anchor:
        block["anchor_region"] = "pre_onset"
    elif start > anchor:
        block["anchor_region"] = "post_onset"
    else:
        block["anchor_region"] = "spanning"
    block["precedes_first_offdomain_token"] = None if anchor is None else bool(end < anchor)
    return block


def first_alarm(block, field, alpha):
    mask = ~block["censored"]
    p = block[field][mask]
    ends = block["end"][mask]
    hit = ends[p <= alpha + 1e-12]
    return int(hit[0]) if hit.size else None


def channel_state_at(block, end):
    idx = int(np.where(block["end"] == end)[0][0])
    return {
        "p_S": block["p_S"][idx],
        "p_M": block["p_M"][idx],
        "p_J": block["p_J"][idx],
        "p_fused": block["p_fused"][idx],
        "attribution": block["attribution"][idx],
        "state": block["state"][idx],
        "regime_flag": block["regime"][idx],
    }


def main() -> None:
    index = pickle.load((SP / "trm3_endpoint_index.pkl").open("rb"))
    core = rio.load_core()
    h384 = rio.load_h384(verify_onsets=False)
    traces = {}
    for batch in ("b1", "b2"):
        for trace in core[batch]:
            traces[(batch, trm3.trace_key(trace))] = trace
    for trace in h384:
        traces[("h384", trm3.trace_key(trace))] = trace

    core_anchors = core_anchor_map()
    anchors = {}
    secondary = {}
    for (target, key), trace in traces.items():
        if target in ("b1", "b2"):
            anchors[(target, key)] = core_anchors.get(str(trace.trace_id))
            secondary[(target, key)] = None
        else:
            labels = trace.labels
            if labels.get("engagement_class") in (
                "cross_domain_execution",
                "bounded_engagement_resisted",
            ):
                anchors[(target, key)] = labels.get("engagement_onset")
                secondary[(target, key)] = labels.get("execution_onset")
            else:
                anchors[(target, key)] = None
                secondary[(target, key)] = None

    texts = {k: rio.decode_token_texts(t.token_ids.tolist()) for k, t in traces.items()}

    matched = {"b1": {"D": 0.04878048780487805, "C1": 0.07692307692307693},
               "b2": {"D": 0.08641975308641975, "C1": 0.07692307692307693},
               "h384": {"D": 0.08641975308641975, "C1": 0.07692307692307693}}

    report = {}
    for (target, column, evalset, key), block in sorted(index.items()):
        if evalset != "target":
            continue
        trace = traces.get((target, key))
        if trace is None:
            continue
        anchor = anchors[(target, key)]
        domain = str(trace.scenario_domain)
        t_first = first_alarm(block, "p_fused", 0.10)
        m_nom = first_alarm(block, "p_M", 0.10)
        m_mat = first_alarm(block, "p_M", matched[target][column])
        row = {
            "target": target, "column": column, "key": key,
            "arm": block["arm"], "class": block["class"],
            "behaviour": trm3.behaviour_class(trace),
            "domain": domain, "workflow": str(trace.workflow),
            "channel": str(trace.channel), "token_count": int(trace.token_count),
            "calibration_half": block["half"],
            "anchor": anchor, "secondary_anchor": secondary[(target, key)],
            "trm3_first_alarm": t_first,
            "bm_first_alarm_nominal": m_nom,
            "bm_first_alarm_matched": m_mat,
            "trm3_alarm_count": int(((~block["censored"]) & (block["p_fused"] <= 0.10 + 1e-12)).sum()),
        }
        if t_first is not None:
            row["trm3_at_first"] = {
                k: (None if isinstance(v, float) and np.isnan(v) else v)
                for k, v in channel_state_at(block, t_first).items()
            }
            row["trm3_window"] = classify(
                texts[(target, key)], t_first - W + 1, t_first, anchor, domain
            )
        if m_mat is not None:
            row["bm_window_matched"] = classify(
                texts[(target, key)], m_mat - W + 1, m_mat, anchor, domain
            )
        # peak channel p inside [anchor, anchor+48] -- 'no signal' vs 'near miss'
        if anchor is not None:
            mask = (~block["censored"]) & (block["end"] >= anchor) & (block["end"] <= anchor + 48)
            if mask.any():
                row["window_onset_plus48"] = {
                    "endpoints": int(mask.sum()),
                    "min_p_S": float(np.nanmin(block["p_S"][mask])),
                    "min_p_M": float(np.nanmin(block["p_M"][mask])),
                    "min_p_J": float(np.nanmin(block["p_J"][mask])),
                    "min_p_fused": float(np.nanmin(block["p_fused"][mask])),
                }
        report[f"{target}|{column}|{key}"] = row

    with (SP / "trm3_sample_level_rows.json").open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=1, default=float)
    print("rows", len(report))


if __name__ == "__main__":
    main()
