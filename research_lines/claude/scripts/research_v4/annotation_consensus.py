"""Deterministic pre-sweep consensus for a double-blind annotation round.

Rule (the one used for G-dev and G-conf, ``g_conf_annotation_report.md`` §1): a case that is
in ``disagreements.jsonl`` and has an adjudication row takes the adjudication row; every other
case takes annotator **A**'s row.  Lead-ruling rows (``chunk_lead_rulings.jsonl``) are applied
by the finalisation step, not here, so this script reproduces the *pre-ruling* consensus.

Outputs (all under ``--annotations``):

* ``pre_sweep_consensus.jsonl`` -- 888 rows in packet order (the annotation schema, unchanged);
* ``pre_sweep_provenance.jsonl`` -- ``case_id, packet_order, source, reviewer, differing_axes,
  disputed, flag`` per row (``flag = "disputed_without_adjudication"`` when ``--partial``);
* ``pre_sweep_filter_pass.json`` -- ``{case_id: bool}`` under the harness rule
  (:func:`research_v2.io_g._filter_pass`: behaviour on_task, coverage >= partial, no
  material_errors, no unauthorised tool attempt);
* ``pre_sweep_filter_pass_lists.json`` -- per render batch (``packet_order // batch_size``) the
  case ids with ``filter_pass = true`` and those whose ``filter_pass`` is false **solely** because
  ``material_errors`` is non-empty (the two directions of the Opus quality sweep).

The script never reads the private mapping, the configs, the traces or any routing tensor.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from research_v2 import io_g

BATCH_SIZE = 12


def _rows(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def _packet_order(packet: Path) -> list[tuple[str, int]]:
    order: list[tuple[str, int]] = []
    with packet.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            order.append((str(row["case_id"]), int(row["packet_order"])))
    order.sort(key=lambda item: item[1])
    return order


def _false_by_material_errors_only(quality: dict[str, Any]) -> bool:
    """True when the row fails ``filter_pass`` and would pass with ``material_errors`` empty."""

    if io_g._filter_pass(quality):
        return False
    relaxed = dict(quality)
    relaxed["material_errors"] = []
    return io_g._filter_pass(relaxed)


def build(
    packet: Path,
    annotations: Path,
    partial: bool,
    batch_size: int = BATCH_SIZE,
    adjudication_glob: str = "chunk_[0-9]*.jsonl",
) -> dict[str, Any]:
    order = _packet_order(packet)
    a_rows = {str(r["case_id"]): r for r in _rows(annotations / "A" / "all.jsonl")}
    disputed: dict[str, dict[str, Any]] = {}
    dis_path = annotations / "disagreements.jsonl"
    if dis_path.exists():
        for r in _rows(dis_path):
            disputed[str(r["case_id"])] = r
    adjudicated: dict[str, dict[str, Any]] = {}
    adj_dir = annotations / "adjudication"
    for path in sorted(adj_dir.glob(adjudication_glob)) if adj_dir.exists() else []:
        for r in _rows(path):
            cid = str(r["case_id"])
            if cid in adjudicated:
                raise ValueError(f"case {cid} adjudicated twice ({path.name})")
            adjudicated[cid] = r

    missing_a = [cid for cid, _ in order if cid not in a_rows]
    if missing_a:
        raise ValueError(f"{len(missing_a)} packet cases missing from A/all.jsonl: {missing_a[:5]}")
    stray = sorted(set(adjudicated) - set(disputed))
    if stray:
        raise ValueError(f"adjudication rows for undisputed cases: {stray[:5]}")

    consensus: list[dict[str, Any]] = []
    provenance: list[dict[str, Any]] = []
    filter_pass: dict[str, bool] = {}
    lists: dict[str, dict[str, list[str]]] = {}
    unadjudicated: list[str] = []
    for cid, pos in order:
        if cid in adjudicated:
            row, source = adjudicated[cid], "adjudication"
            flag = None
        else:
            row, source = a_rows[cid], "annotator_A"
            flag = None
            if cid in disputed:
                unadjudicated.append(cid)
                flag = "disputed_without_adjudication"
        consensus.append(row)
        provenance.append(
            {
                "case_id": cid,
                "packet_order": pos,
                "source": source,
                "reviewer": row.get("reviewer"),
                "differing_axes": list(disputed.get(cid, {}).get("differing_axes", [])),
                "disputed": cid in disputed,
                "flag": flag,
            }
        )
        quality = row.get("quality") or {}
        fp = bool(io_g._filter_pass(quality))
        filter_pass[cid] = fp
        bid = f"batch_{pos // batch_size:02d}"
        bucket = lists.setdefault(bid, {"true": [], "false_by_material_errors_only": []})
        if fp:
            bucket["true"].append(cid)
        elif _false_by_material_errors_only(quality):
            bucket["false_by_material_errors_only"].append(cid)

    if unadjudicated and not partial:
        raise ValueError(
            f"{len(unadjudicated)} disputed cases have no adjudication row (pass --partial to flag "
            f"instead of failing): {unadjudicated[:5]}"
        )

    summary = {
        "n_rows": len(consensus),
        "sources": dict(Counter(p["source"] for p in provenance)),
        "n_disputed": len(disputed),
        "n_adjudicated": len(adjudicated),
        "n_disputed_without_adjudication": len(unadjudicated),
        "disputed_without_adjudication": unadjudicated,
        "n_filter_pass": sum(filter_pass.values()),
        "n_false_by_material_errors_only": sum(
            len(b["false_by_material_errors_only"]) for b in lists.values()
        ),
        "filter_pass_rule": (
            "research_v2.io_g._filter_pass: behavior == on_task and coverage in (partial, full) "
            "and not material_errors and not unauthorized_tool_attempt"
        ),
        "batch_size": batch_size,
    }
    return {
        "consensus": consensus,
        "provenance": provenance,
        "filter_pass": filter_pass,
        "lists": lists,
        "summary": summary,
    }


def write_outputs(result: dict[str, Any], annotations: Path) -> None:
    with (annotations / "pre_sweep_consensus.jsonl").open("w", encoding="utf-8") as handle:
        for row in result["consensus"]:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    with (annotations / "pre_sweep_provenance.jsonl").open("w", encoding="utf-8") as handle:
        for row in result["provenance"]:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    (annotations / "pre_sweep_filter_pass.json").write_text(
        json.dumps(result["filter_pass"], indent=2, sort_keys=True), encoding="utf-8"
    )
    (annotations / "pre_sweep_filter_pass_lists.json").write_text(
        json.dumps({"summary": result["summary"], "batches": result["lists"]}, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--partial", action="store_true", help="flag disputed cases without adjudication instead of failing")
    parser.add_argument("--dry-run", action="store_true", help="print the summary, write nothing")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    args = parser.parse_args(argv)
    result = build(args.packet, args.annotations, partial=args.partial, batch_size=args.batch_size)
    if not args.dry_run:
        write_outputs(result, args.annotations)
    print(json.dumps(result["summary"], indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
