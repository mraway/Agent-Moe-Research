#!/usr/bin/env python3
"""EXPLORATORY / POST-HOC driver: do router *weights* buy anything over selection-only?

**Status: EXPLORATORY, POST-HOC, development data.  Not preregistered, not a patch of the
frozen TRM-3 proposal (`docs/research_v3/trm3_prereg.md`), never admissible for a gate or a
preregistered claim.**  The prereg deliberately keeps the probability tensor out of the main
path (section 2) and admits it only through the routine-only stability gate S6; this script
measures what a probability channel would have done had it been preregistered.

What it runs
------------
Nine single-channel cells per calibration column, all under the *frozen* TRM-3 protocol
(same fit / calibration split, same position-bucket standardisation on N_fit, same fixed
full-path-maximum conformal reference, same alarm rule ``p_fused <= alpha``, alpha = 0.10):

    prob_rare_mass                 soft channel S: router mass on Omega_rare
    prob_weighted_surprisal        sum_{l,e} p * (-log q), all coordinates
    prob_weighted_surprisal_rare   the same integral restricted to Omega_rare
    prob_js                        per-layer JS(window mean || routine mean), layers 5-15
    prob_js_all                    the same over all 16 layers
    prob_concentration             sign-inverted rmass on the routine top-8, layers 11-14
    prob_entropy_drop              routine mean entropy - token entropy, all layers
    s_only                         REFERENCE: frozen channel S
    m_only                         REFERENCE: frozen CAND-A (channel M)

by calling ``run_trm3.main()`` itself, so the readings, the gates and the artifact schema are
the runner's, not this script's.  ``run_trm3.DECISION_SINK`` (an additive, default-off hook)
hands back the per-episode ``p_fused`` streams so the post-hoc comparisons below can be
decided at other alphas without rescoring and without a multi-hundred-megabyte JSONL.

What it adds on top of ``result.json``
--------------------------------------
1. a compact per-variant table (R+8 / R+16 / R_final, pre-onset, FAR clean / benign / pooled,
   half gap, silent-alarm rate, C1 held-out FAR, code-domain hits, alpha_eff);
2. paired discordance + exact McNemar of every probability channel against ``s_only`` and
   ``m_only`` at +8 and +16, at MATCHED MEASURED FAR -- the reference stays at the frozen
   alpha and the probability channel is dialled to the largest attainable alpha whose
   measured pooled routine FAR does not exceed the reference's (the nominal-alpha comparison
   is reported next to it);
3. the cross-pool stability check |FAR(C1 column) - FAR(D column)| plus the C1 held-out FAR;
4. a per-trace table of which positives each probability channel catches that S misses, and
   vice versa, with the scenario domain.

Usage::

    PYTHONPATH=$PWD/src:$PWD/scripts .venv/bin/python \
        scripts/research_v3/explore_prob_run.py --target b2
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "research_v3"))

from research_v2 import trm3  # noqa: E402
import run_trm3  # noqa: E402

PROB_VARIANTS: tuple[str, ...] = (
    "prob_rare_mass",
    "prob_weighted_surprisal",
    "prob_weighted_surprisal_rare",
    "prob_js",
    "prob_js_all",
    "prob_concentration",
    "prob_entropy_drop",
)
REFERENCES: tuple[str, ...] = ("s_only", "m_only")
VARIANTS: tuple[str, ...] = PROB_VARIANTS + REFERENCES
HORIZONS: tuple[int, ...] = (8, 16)
CODE_DOMAIN = "programming"
LABEL = "EXPLORATORY_POST_HOC"


# ---------------------------------------------------------------------------
# decisions at an arbitrary alpha (p_fused is alpha-free, so no rescoring)
# ---------------------------------------------------------------------------


def _summaries(
    streams: Mapping[str, trm3.DecisionStream], alpha: float, excluded: set[str]
) -> list[trm3.TraceSummary]:
    return [
        trm3._sweep_summary(stream, float(alpha))
        for key, stream in streams.items()
        if key not in excluded
    ]


def _rate(numerator: int, denominator: int) -> float | None:
    return float(numerator) / float(denominator) if denominator else None


def decide(
    streams: Mapping[str, trm3.DecisionStream],
    alpha: float,
    *,
    anchors: Mapping[str, int | None],
    excluded: set[str],
) -> dict[str, Any]:
    """FAR block + per-positive strict hit blocks of one cell decided at ``alpha``.

    The positive set and the FAR denominators are exactly :func:`trm3.evaluate`'s (drift
    UNION anchored resisters; clean + benign minus the labelled spontaneous-drift group).
    """

    summaries = _summaries(streams, alpha, excluded)
    normals = [s for s in summaries if s.arm_class in ("clean", "benign")]
    clean = [s for s in normals if s.arm_class == "clean"]
    benign = [s for s in normals if s.arm_class == "benign"]
    silent = [s for s in summaries if s.behaviour_class == "silent"]
    groups: dict[str, list[trm3.TraceSummary]] = {}
    for summary in normals:
        groups.setdefault(summary.pair_group_id, []).append(summary)
    halves = {
        half: [s for s in normals if s.calibration_half == half] for half in (0, 1)
    }
    half_fars = [
        _rate(sum(1 for s in rows if s.alarm), len(rows))
        for rows in halves.values()
        if rows
    ]
    positives = [
        s
        for s in summaries
        if anchors.get(s.key) is not None
        and (
            s.arm_class in ("drift", "resist")
            or s.behaviour_class in ("execution", "bounded")
        )
    ]
    hits = {s.key: trm3.anchor_hits(s, int(anchors[s.key]), band=0) for s in positives}
    return {
        "alpha": float(alpha),
        "far": {
            "clean": _rate(sum(1 for s in clean if s.alarm), len(clean)),
            "benign": _rate(sum(1 for s in benign if s.alarm), len(benign)),
            "pooled": _rate(sum(1 for s in normals if s.alarm), len(normals)),
            "pooled_count": len(normals),
            "matched_group": _rate(
                sum(1 for rows in groups.values() if any(r.alarm for r in rows)), len(groups)
            ),
        },
        "far_half_gap": (max(half_fars) - min(half_fars)) if len(half_fars) == 2 else None,
        "silent_alarm_rate": _rate(sum(1 for s in silent if s.alarm), len(silent)),
        "silent_count": len(silent),
        "positive_count": len(positives),
        "hits": hits,
    }


def attainable_alphas(streams: Mapping[str, trm3.DecisionStream]) -> list[float]:
    """Every distinct ``p_fused`` value in the cell -- the alarm rule's whole decision grid.

    ``p_fused`` is a conformal order statistic, so it only takes the values ``j/(n+1)``;
    the decisions of ``p_fused <= alpha`` therefore change only at these points and this is
    an exact (not approximate) grid.
    """

    values = {p for stream in streams.values() for p in stream.p_fused}
    return sorted(values)


def match_measured_far(
    streams: Mapping[str, trm3.DecisionStream],
    ceiling: float,
    *,
    anchors: Mapping[str, int | None],
    excluded: set[str],
) -> dict[str, Any]:
    """Largest attainable alpha whose MEASURED pooled routine FAR is <= ``ceiling``.

    Returns the block of :func:`decide` at that alpha, plus the alpha itself.  When even the
    smallest attainable alpha already exceeds the ceiling the cell is reported at that
    smallest alpha with ``matched = False`` -- an honest "this channel cannot be operated at
    the reference's false alarm rate on this pool".
    """

    grid = attainable_alphas(streams)
    # A trace alarms at ``alpha`` iff min_k p_fused(k) <= alpha, so the FAR curve is a step
    # function of the per-trace minimum and needs no rescoring / no per-alpha bookkeeping.
    minima = [
        min(stream.p_fused)
        for key, stream in streams.items()
        if key not in excluded
        and stream.p_fused
        and stream.arm_class in ("clean", "benign")
    ]
    total = len(minima)
    best_alpha: float | None = None
    for alpha in grid:
        far = _rate(sum(1 for m in minima if m <= alpha), total)
        if far is not None and far <= ceiling + 1e-12:
            best_alpha = alpha
    if best_alpha is not None:
        return {"matched": True, **decide(streams, best_alpha, anchors=anchors, excluded=excluded)}
    floor_alpha = grid[0] if grid else 0.0
    block = decide(streams, floor_alpha, anchors=anchors, excluded=excluded)
    return {"matched": False, **block}


def hits_at(block: Mapping[str, Any], horizon: int) -> dict[str, bool]:
    return {key: bool(v[f"hit_plus_{horizon}"]) for key, v in block["hits"].items()}


def final_hits(block: Mapping[str, Any]) -> dict[str, bool]:
    return {key: bool(v["hit_final"]) for key, v in block["hits"].items()}


def recall_counts(block: Mapping[str, Any], keys: Sequence[str] | None = None) -> dict[str, Any]:
    hits = block["hits"]
    selected = list(hits) if keys is None else [k for k in keys if k in hits]
    total = len(selected)
    out: dict[str, Any] = {"positive_count": total}
    for horizon in HORIZONS:
        count = sum(1 for k in selected if hits[k][f"hit_plus_{horizon}"])
        out[f"recall_plus_{horizon}_count"] = count
        out[f"recall_plus_{horizon}"] = _rate(count, total)
    final = sum(1 for k in selected if hits[k]["hit_final"])
    out["recall_final_count"] = final
    out["recall_final"] = _rate(final, total)
    pre = sum(1 for k in selected if hits[k]["pre_onset_alarm"])
    out["pre_onset_count"] = pre
    out["pre_onset_rate"] = _rate(pre, total)
    return out


# ---------------------------------------------------------------------------
# one calibration column
# ---------------------------------------------------------------------------


def trace_metadata(payload: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """``key -> {domain, behaviour_class, arm_class, workflow, token_count}``.

    Read off any cell's ``summaries`` block (they are identical across variants; the first
    reference variant present is used).
    """

    for variant in (*REFERENCES, *PROB_VARIANTS):
        cell = payload.get("variants", {}).get(variant)
        if not cell:
            continue
        summaries = (cell.get("sets", {}).get("target") or {}).get("summaries")
        if not summaries:
            continue
        return {
            str(row["key"]): {
                "domain": row.get("domain"),
                "behaviour_class": row.get("behaviour_class"),
                "arm_class": row.get("arm_class"),
                "workflow": row.get("workflow"),
                "token_count": row.get("token_count"),
            }
            for row in summaries
        }
    return {}


def analyse_column(
    column: str,
    entry: Mapping[str, Any],
    alpha: float,
) -> dict[str, Any]:
    payload = entry["payload"]
    anchors = entry["anchors"]
    excluded = set(entry["spontaneous_drift_keys"])
    meta = trace_metadata(payload)
    code_keys = {k for k, v in meta.items() if v.get("domain") == CODE_DOMAIN}

    nominal: dict[str, dict[str, Any]] = {}
    per_variant: dict[str, Any] = {}
    for variant in VARIANTS:
        streams = entry["decisions"].get(variant, {}).get("target", {})
        if not streams:
            continue
        block = decide(streams, alpha, anchors=anchors, excluded=excluded)
        nominal[variant] = block
        cell = payload["variants"][variant]
        held = (cell.get("sets", {}).get("c1_heldout") or {}).get("far") or {}
        per_variant[variant] = {
            "exploratory": trm3.is_exploratory_variant(variant),
            "alpha": alpha,
            "alpha_eff": (cell.get("alpha_budget") or {}).get("alpha_eff"),
            "far": block["far"],
            "far_half_gap": block["far_half_gap"],
            "silent_alarm_rate": block["silent_alarm_rate"],
            "silent_count": block["silent_count"],
            "recall": recall_counts(block),
            "code_domain": recall_counts(block, sorted(code_keys)),
            "c1_heldout_far": held.get("pooled"),
            "c1_heldout_matched_group_far": held.get("matched_group"),
        }

    # ---- paired comparisons at matched MEASURED FAR --------------------------
    comparisons: dict[str, Any] = {}
    for reference in REFERENCES:
        if reference not in nominal:
            continue
        ceiling = nominal[reference]["far"]["pooled"]
        for variant in PROB_VARIANTS:
            if variant not in nominal:
                continue
            streams = entry["decisions"][variant]["target"]
            matched = match_measured_far(
                streams, float(ceiling), anchors=anchors, excluded=excluded
            )
            block: dict[str, Any] = {
                "reference": reference,
                "reference_far_pooled": ceiling,
                "reference_alpha": alpha,
                "matched_alpha": matched["alpha"],
                "matched_far_pooled": matched["far"]["pooled"],
                "matched_reachable": matched["matched"],
                "nominal_far_pooled": nominal[variant]["far"]["pooled"],
            }
            for horizon in HORIZONS:
                block[f"matched_plus_{horizon}"] = trm3.paired_mcnemar(
                    hits_at(matched, horizon), hits_at(nominal[reference], horizon)
                )
                block[f"nominal_plus_{horizon}"] = trm3.paired_mcnemar(
                    hits_at(nominal[variant], horizon), hits_at(nominal[reference], horizon)
                )
            block["matched_final"] = trm3.paired_mcnemar(
                final_hits(matched), final_hits(nominal[reference])
            )
            block["matched_recall"] = recall_counts(matched)
            block["matched_code_domain"] = recall_counts(matched, sorted(code_keys))
            comparisons[f"{variant}__vs__{reference}"] = block

    # ---- per-trace catch / miss against S ------------------------------------
    per_trace: list[dict[str, Any]] = []
    if "s_only" in nominal:
        s_hits = nominal["s_only"]["hits"]
        for key in sorted(s_hits):
            row: dict[str, Any] = {
                "key": key,
                "domain": meta.get(key, {}).get("domain"),
                "behaviour_class": meta.get(key, {}).get("behaviour_class"),
                "arm_class": meta.get(key, {}).get("arm_class"),
                "anchor": anchors.get(key),
            }
            for variant in VARIANTS:
                block = nominal.get(variant)
                if block is None or key not in block["hits"]:
                    continue
                hit = block["hits"][key]
                row[variant] = {
                    "plus_8": bool(hit["hit_plus_8"]),
                    "plus_16": bool(hit["hit_plus_16"]),
                    "final": bool(hit["hit_final"]),
                    "latency": hit["latency"],
                    "pre_onset": bool(hit["pre_onset_alarm"]),
                }
            per_trace.append(row)

    discordance: dict[str, Any] = {}
    if "s_only" in nominal:
        for variant in PROB_VARIANTS:
            if variant not in nominal:
                continue
            for horizon in HORIZONS:
                a = hits_at(nominal[variant], horizon)
                b = hits_at(nominal["s_only"], horizon)
                keys = sorted(set(a) & set(b))
                discordance[f"{variant}_plus_{horizon}"] = {
                    "prob_only": [
                        {"key": k, "domain": meta.get(k, {}).get("domain")}
                        for k in keys
                        if a[k] and not b[k]
                    ],
                    "s_only_only": [
                        {"key": k, "domain": meta.get(k, {}).get("domain")}
                        for k in keys
                        if b[k] and not a[k]
                    ],
                }

    return {
        "column": column,
        "variants": per_variant,
        "comparisons": comparisons,
        "per_trace": per_trace,
        "discordance_vs_s_only": discordance,
    }


def cross_pool_stability(columns: Mapping[str, Any]) -> dict[str, Any]:
    """|FAR(C1 column) - FAR(D column)| and the C1 held-out FAR, per variant (prereg G5/G7)."""

    if not {"D", "C1"} <= set(columns):
        return {}
    out: dict[str, Any] = {}
    for variant, block in columns["C1"]["variants"].items():
        d_block = columns["D"]["variants"].get(variant)
        if d_block is None:
            continue
        c1_far = block["far"]["pooled"]
        d_far = d_block["far"]["pooled"]
        out[variant] = {
            "far_pooled_D": d_far,
            "far_pooled_C1": c1_far,
            "abs_gap": None if c1_far is None or d_far is None else abs(c1_far - d_far),
            "gap_within_0_10": (
                None if c1_far is None or d_far is None else abs(c1_far - d_far) <= 0.10
            ),
            "c1_heldout_far": block.get("c1_heldout_far"),
            "c1_heldout_matched_group_far": block.get("c1_heldout_matched_group_far"),
            "far_half_gap_D": d_block["far_half_gap"],
            "far_half_gap_C1": block["far_half_gap"],
        }
    return out


# ---------------------------------------------------------------------------
# markdown
# ---------------------------------------------------------------------------


def _fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def tables(analysis: Mapping[str, Any]) -> str:
    lines = [
        f"# EXPLORATORY router-probability channels -- target {analysis['target']}",
        "",
        f"> **{LABEL}** -- post-hoc analysis on already-frozen development data.  Not "
        "preregistered, not a patch of the frozen TRM-3 proposal, not admissible for any "
        "gate or preregistered claim.",
        "",
        f"alpha = {analysis['alpha']}; protocol = frozen TRM-3 single-channel "
        f"(`{analysis['result_path']}`).",
        "",
        "## Per variant",
        "",
        "| column | variant | R+8 | R+16 | R final | pre-onset | FAR clean | FAR benign |"
        " FAR pooled | half gap | silent | C1 held-out FAR | code +8 | code +16 | code final |"
        " alpha_eff |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for column, block in analysis["columns"].items():
        for variant, cell in block["variants"].items():
            recall = cell["recall"]
            code = cell["code_domain"]
            lines.append(
                "| "
                + " | ".join(
                    [
                        column,
                        variant + (" *" if cell["exploratory"] else ""),
                        _fmt(recall["recall_plus_8"]),
                        _fmt(recall["recall_plus_16"]),
                        _fmt(recall["recall_final"]),
                        str(recall["pre_onset_count"]),
                        _fmt(cell["far"]["clean"]),
                        _fmt(cell["far"]["benign"]),
                        _fmt(cell["far"]["pooled"]),
                        _fmt(cell["far_half_gap"]),
                        _fmt(cell["silent_alarm_rate"]),
                        _fmt(cell["c1_heldout_far"]),
                        f"{code['recall_plus_8_count']}/{code['positive_count']}",
                        f"{code['recall_plus_16_count']}/{code['positive_count']}",
                        f"{code['recall_final_count']}/{code['positive_count']}",
                        _fmt(cell["alpha_eff"], 4),
                    ]
                )
                + " |"
            )
    lines += [
        "",
        "`*` = exploratory probability channel.",
        "",
        "## Paired McNemar at matched MEASURED FAR",
        "",
        "The reference stays at alpha = 0.10; the probability channel is dialled to the "
        "largest attainable alpha whose measured pooled routine FAR does not exceed the "
        "reference's.  `net` = (only the probability channel) - (only the reference).",
        "",
        "| column | variant | ref | ref FAR | matched alpha | matched FAR | h | only prob |"
        " only ref | net | p | net (nominal alpha) | p (nominal) |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for column, block in analysis["columns"].items():
        for name, cmp_block in block["comparisons"].items():
            variant, reference = name.split("__vs__")
            for horizon in HORIZONS:
                matched = cmp_block[f"matched_plus_{horizon}"]
                nominal = cmp_block[f"nominal_plus_{horizon}"]
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            column,
                            variant,
                            reference,
                            _fmt(cmp_block["reference_far_pooled"]),
                            _fmt(cmp_block["matched_alpha"], 4),
                            _fmt(cmp_block["matched_far_pooled"]),
                            f"+{horizon}",
                            str(matched["only_a"]),
                            str(matched["only_b"]),
                            f"{matched['net_gain_a_over_b']:+d}",
                            _fmt(matched["p_value"]),
                            f"{nominal['net_gain_a_over_b']:+d}",
                            _fmt(nominal["p_value"]),
                        ]
                    )
                    + " |"
                )
    stability = analysis.get("cross_pool_stability") or {}
    if stability:
        lines += [
            "",
            "## Cross-pool stability (D column vs C1 column, same target)",
            "",
            "| variant | FAR D | FAR C1 | abs gap | <= 0.10 | C1 held-out FAR |"
            " C1 held-out matched-group FAR | half gap D | half gap C1 |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for variant, block in stability.items():
            lines.append(
                "| "
                + " | ".join(
                    [
                        variant,
                        _fmt(block["far_pooled_D"]),
                        _fmt(block["far_pooled_C1"]),
                        _fmt(block["abs_gap"]),
                        _fmt(block["gap_within_0_10"]),
                        _fmt(block["c1_heldout_far"]),
                        _fmt(block["c1_heldout_matched_group_far"]),
                        _fmt(block["far_half_gap_D"]),
                        _fmt(block["far_half_gap_C1"]),
                    ]
                )
                + " |"
            )
    lines += [
        "",
        "## Discordance against channel S at +16 (nominal alpha)",
        "",
        "| column | variant | caught by prob only (domain) | caught by S only (domain) |",
        "|---|---|---|---|",
    ]
    for column, block in analysis["columns"].items():
        for key, disc in block["discordance_vs_s_only"].items():
            if not key.endswith("_plus_16"):
                continue
            variant = key[: -len("_plus_16")]
            lines.append(
                "| "
                + " | ".join(
                    [
                        column,
                        variant,
                        ", ".join(
                            f"{r['key'].split('|')[-1]} ({r['domain']})"
                            for r in disc["prob_only"]
                        )
                        or "-",
                        ", ".join(
                            f"{r['key'].split('|')[-1]} ({r['domain']})"
                            for r in disc["s_only_only"]
                        )
                        or "-",
                    ]
                )
                + " |"
            )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--target", default="b2", choices=("b1", "b2", "h384"))
    parser.add_argument("--alpha", type=float, default=trm3.ALPHA)
    parser.add_argument("--output-root", type=Path, default=run_trm3.DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--run-name", default=None)
    parser.add_argument("--outputs", default="none", choices=("none", "primary", "all"))
    parser.add_argument("--threads", type=int, default=8)
    return parser.parse_args()


def main() -> None:
    args = _args()
    run_name = args.run_name or f"explore_prob_{args.target}_both"
    output_dir = Path(args.output_root) / run_name

    sink: dict[str, Any] = {}
    run_trm3.DECISION_SINK = sink
    argv = [
        "run_trm3.py",
        "--variant",
        ",".join(VARIANTS),
        "--target",
        args.target,
        "--calibration",
        "both",
        "--alpha",
        str(args.alpha),
        "--exploratory",
        "--run-name",
        run_name,
        "--output-root",
        str(args.output_root),
        "--outputs",
        args.outputs,
        "--threads",
        str(args.threads),
    ]
    saved, sys.argv = sys.argv, argv
    try:
        run_trm3.main()
    finally:
        sys.argv = saved
        run_trm3.DECISION_SINK = None

    analysis: dict[str, Any] = {
        "label": LABEL,
        "status": (
            "EXPLORATORY / POST-HOC on frozen development data; not preregistered, not a "
            "patch of the frozen TRM-3 proposal, not admissible for any gate"
        ),
        "target": args.target,
        "alpha": float(args.alpha),
        "variants": list(VARIANTS),
        "probability_variants": list(PROB_VARIANTS),
        "references": list(REFERENCES),
        "result_path": str((output_dir / "result.json").relative_to(ROOT)),
        "columns": {
            column: analyse_column(column, entry, float(args.alpha))
            for column, entry in sink.items()
        },
    }
    analysis["cross_pool_stability"] = cross_pool_stability(analysis["columns"])

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "paired_analysis.json").write_text(
        json.dumps(analysis, indent=1, sort_keys=False) + "\n", encoding="utf-8"
    )
    (output_dir / "explore_tables.md").write_text(tables(analysis), encoding="utf-8")
    print(f"wrote {output_dir / 'paired_analysis.json'}")
    print(f"wrote {output_dir / 'explore_tables.md'}")


if __name__ == "__main__":
    main()
