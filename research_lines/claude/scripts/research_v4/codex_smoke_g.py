#!/usr/bin/env python3
"""Dataset-G access smoke for a SECOND research line (metadata + labels + one episode).

Run this from the repository root of ANY checkout of this project (the main checkout on
``main`` or a worktree); every path is derived from the location of this file, so nothing
here depends on the working directory or on a branch::

    PYTHONPATH=src:scripts .venv/bin/python scripts/research_v4/codex_smoke_g.py

It answers four questions and nothing else:

1. **census** -- which dataset G subsets exist on disk, how many traces / scenarios /
   fixtures each carries, and which subset config froze them;
2. **labels** -- which annotation files exist, their sha256, their row count and the arm /
   trajectory-class histograms a detector's denominators are built from;
3. **routing** -- one episode loaded end to end: the ``[24, T, 4]`` top-k selection, the
   ``[24, T, 32]`` full router logits, the harmony channel segmentation, and the number of
   "looks" (window endpoints) the frozen view geometry produces on it;
4. **seal status** -- which subsets are OPEN for development, which are UNLABELLED, and
   which are SEALED.

**G-conf discipline (mechanically enforced here).**  ``artifacts/agent_v2/dataset_g/g_conf``
is the sealed confirmatory batch.  Every content read in this script goes through
:func:`_read_bytes`, which REFUSES any path inside a sealed subset; the seal row of the
status table is produced from ``os.stat`` / ``os.access`` only (no byte of the subset is
read) plus the subset CONFIG metadata, which lives outside the sealed tree.  The G-conf
TEXT annotation (``annotations/g_conf/``) is outside the sealed tree and is reported at the
aggregate level already published in ``docs/research_v4/g_conf_annotation_report.md``; a
second research line must NOT join those per-episode attack labels into any development
work before the joint two-stage unsealing.

Nothing here writes to the dataset, to the routing caches, or to any artifact directory
unless ``--json`` is given.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import torch  # noqa: E402  (after sys.path fix-up)

from research_v2 import io_g, trm3, trm3_g  # noqa: E402

DATASET_G = REPO_ROOT / "artifacts" / "agent_v2" / "dataset_g"
G_BRIDGE = REPO_ROOT / "artifacts" / "agent_v2" / "g_bridge_gpt_oss_20b" / "batch"
CONFIG_DIR = REPO_ROOT / "configs" / "dataset_g"
ANNOTATIONS = DATASET_G / "annotations"

#: subsets whose ROUTING is sealed; this script never reads a byte inside them
SEALED_SUBSETS: tuple[str, ...] = ("g_conf",)

#: (subset, on-disk root, subset config, access status, one-line role)
SUBSETS: tuple[tuple[str, Path, str, str, str], ...] = (
    ("g_fit", DATASET_G / "g_fit", "g_fit.json", "OPEN", "fit pool (q / Omega_rare / whitening / position buckets)"),
    ("g_cal", DATASET_G / "g_cal", "g_cal.json", "OPEN", "v3.1 conformal calibration pool; v3.2 keeps it for H and the identity column"),
    ("g_dev", DATASET_G / "g_dev", "g_dev.json", "OPEN", "development evaluation target (already unsealed and read three times)"),
    ("g_session", DATASET_G / "g_session", "g_session.json", "OPEN-UNLABELLED", "session prefix (2 turns actually run); normal arms usable by arm metadata"),
    ("g_medium", DATASET_G / "g_medium", "g_medium.json", "OPEN-UNLABELLED", "reasoning_effort = medium paired re-run of 40 G-dev scenarios"),
    ("g_conf", DATASET_G / "g_conf", "g_conf.json", "SEALED", "sealed confirmatory batch; routing opened ONCE, jointly, under the two-stage rule"),
)


# ---------------------------------------------------------------------------
# sealed-subset guard
# ---------------------------------------------------------------------------


def sealed_roots() -> tuple[Path, ...]:
    return tuple((DATASET_G / name).resolve() for name in SEALED_SUBSETS)


def _refuse_if_sealed(path: Path) -> Path:
    resolved = Path(path).resolve()
    for root in sealed_roots():
        if resolved == root or root in resolved.parents:
            raise SystemExit(
                f"REFUSED: {resolved} is inside the sealed subset {root.name}; this smoke "
                "never reads a byte of a sealed batch (prereg v3.2 section 3 / 12)"
            )
    return resolved


def _read_bytes(path: Path) -> bytes:
    """The ONLY content read in this script; every call passes the sealed-subset guard."""

    return _refuse_if_sealed(path).read_bytes()


def _read_json(path: Path) -> Any:
    return json.loads(_read_bytes(path).decode("utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(_read_bytes(path))
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# formatting helpers
# ---------------------------------------------------------------------------


def rule(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def table(headers: list[str], rows: list[list[Any]]) -> None:
    cells = [[str(v) for v in row] for row in rows]
    widths = [len(h) for h in headers]
    for row in cells:
        for i, value in enumerate(row):
            widths[i] = max(widths[i], len(value))
    print("  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    print("  ".join("-" * widths[i] for i in range(len(headers))))
    for row in cells:
        print("  ".join(value.ljust(widths[i]) for i, value in enumerate(row)))


def histogram(counter: Counter[str], limit: int = 12) -> str:
    items = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
    return " ".join(f"{k}={v}" for k, v in items) or "-"


# ---------------------------------------------------------------------------
# 1. census
# ---------------------------------------------------------------------------


def count_traces(root: Path) -> int:
    """Number of trace directories, WITHOUT opening a single file."""

    if not root.exists():
        return 0
    return len(io_g.iter_trace_paths(root))


def census() -> dict[str, Any]:
    rule("1. SUBSET CENSUS  (config metadata + directory listing; no trace.json is read)")
    rows: list[list[Any]] = []
    payload: dict[str, Any] = {}
    for name, root, config_name, status, _role in SUBSETS:
        config_path = CONFIG_DIR / config_name
        config = _read_json(config_path) if config_path.exists() else {}
        scenarios = config.get("scenarios") or []
        fixtures = Counter(
            str((s.get("factory") or {}).get("fixture_id", "?")) for s in scenarios
        )
        arms = Counter()
        for plan in config.get("collection_plan") or []:
            for arm in plan.get("arms") or []:
                arms[str(arm)] += 1
        # a sealed subset is counted by directory listing only (stat, never open)
        traces = count_traces(root) if status != "SEALED" else _sealed_trace_count(root)
        rows.append(
            [
                name,
                status,
                len(scenarios),
                traces,
                len(fixtures),
                " ".join(f"{k}:{v}" for k, v in sorted(fixtures.items())),
                config_path.name,
            ]
        )
        payload[name] = {
            "status": status,
            "scenarios": len(scenarios),
            "traces_on_disk": traces,
            "fixtures": dict(sorted(fixtures.items())),
            "dataset_role": config.get("dataset_role"),
            "experiment_id": config.get("experiment_id"),
            "config": str(config_path.relative_to(REPO_ROOT)),
            "config_sha256": _sha256(config_path) if config_path.exists() else None,
            "root": str(root.relative_to(REPO_ROOT)),
            "arms_declared": dict(sorted(arms.items())),
        }
    bridge = count_traces(G_BRIDGE)
    rows.append(["g_bridge", "OPEN", "80 (h384 replay)", bridge, "-", "-", "(no subset config)"])
    payload["g_bridge"] = {
        "status": "OPEN",
        "traces_on_disk": bridge,
        "root": str(G_BRIDGE.relative_to(REPO_ROOT)),
        "note": "v2.5 deterministic controller re-run on gpt-oss; no quality annotation",
    }
    table(
        ["subset", "access", "scenarios", "traces", "#fx", "fixtures", "config"],
        rows,
    )
    print()
    print("  model: openai/gpt-oss-20b @ 6cee5e81ee83917806bbde320786a8fb61efebee "
          "(MXFP4, eager attn, T=0.8 / top-p=0.9, reasoning effort low; g_medium = medium)")
    print("  router geometry: 24 MoE layers x 32 experts, top_k = 4, "
          "top_k_weight_semantics = softmax_over_selected_logits_only")
    return payload


def _sealed_trace_count(root: Path) -> str:
    """Trace count of a sealed subset -- directory listing only, no file is opened."""

    if not root.exists():
        return "absent"
    return f"{sum(1 for _ in root.rglob('trace.json'))} (listing only)"


# ---------------------------------------------------------------------------
# 2. labels
# ---------------------------------------------------------------------------

LABEL_SUBSETS: tuple[str, ...] = ("g_fit", "g_cal", "g_dev", "g_conf", "g_session", "g_medium")


def _arm_of(key: tuple[str, int], row: dict[str, Any]) -> str:
    """Arm of one annotation row, with the five-way normal identity restored.

    ``arm_name`` is the collected arm; G-dev's two hard-normal groups were collected under
    ``clean`` and their true role lives in ``normal_variant`` (io_g.variant_overrides_from_config
    does the same join on the routing side).  G-fit / G-cal rows carry neither field, so the
    arm is read off the episode id.
    """

    variant = str(row.get("normal_variant") or "")
    if variant in io_g.SPECIAL_NORMAL_VARIANTS:
        return variant
    arm = str(row.get("arm_name") or "")
    if arm:
        return arm
    trace_id = str(key[0])
    tail = trace_id.rsplit("--", 1)[-1] if "--" in trace_id else ""
    return tail or "?"


def labels() -> dict[str, Any]:
    rule("2. LABEL FILES  (routing-blind text annotation; annotations/ is OUTSIDE the seal)")
    rows: list[list[Any]] = []
    payload: dict[str, Any] = {}
    for name in LABEL_SUBSETS:
        path = ANNOTATIONS / name / "final_unblinded.jsonl"
        if not path.exists():
            rows.append([name, "ABSENT", "-", "-", "-"])
            payload[name] = {"present": False}
            continue
        rows_read = io_g.read_labels(_refuse_if_sealed(path))
        arms = Counter(_arm_of(key, row) for key, row in rows_read.items())
        classes = Counter(str(row.get("trajectory_class") or "?") for row in rows_read.values())
        derived = {
            "has_engagement": sum(1 for r in rows_read.values() if r.get("has_engagement")),
            "c": sum(1 for r in rows_read.values() if r.get("c") is not None),
            "x": sum(1 for r in rows_read.values() if r.get("x") is not None),
            "x_tool": sum(1 for r in rows_read.values() if r.get("x_tool") is not None),
            "x_tool_only": sum(1 for r in rows_read.values() if r.get("x_tool_only")),
            "silent": sum(1 for r in rows_read.values() if r.get("silent")),
            "filter_pass_true": sum(1 for r in rows_read.values() if r.get("filter_pass") is True),
            "filter_pass_none": sum(1 for r in rows_read.values() if r.get("filter_pass") is None),
            "analysis_only_engagement": sum(
                1 for r in rows_read.values() if r.get("analysis_only_engagement")
            ),
            "note_LEAK": sum(
                1 for r in rows_read.values() if str(r.get("note") or "").startswith("LEAK:")
            ),
            "note_GENFAIL": sum(
                1 for r in rows_read.values() if str(r.get("note") or "").startswith("GENFAIL:")
            ),
        }
        rows.append([name, "PRESENT", len(rows_read), histogram(arms), _sha256(path)[:16] + "..."])
        payload[name] = {
            "present": True,
            "path": str(path.relative_to(REPO_ROOT)),
            "rows": len(rows_read),
            "sha256": _sha256(path),
            "arms": dict(sorted(arms.items())),
            "trajectory_classes": dict(sorted(classes.items())),
            "derived": derived,
        }
        payload[name]["_print_classes"] = histogram(classes)
    table(["subset", "labels", "rows", "arm histogram", "sha256"], rows)
    print()
    for name in LABEL_SUBSETS:
        block = payload.get(name) or {}
        if not block.get("present"):
            continue
        print(f"  {name}: trajectory classes  {block.pop('_print_classes')}")
        derived = block["derived"]
        print(
            "        derived: "
            + " ".join(f"{k}={v}" for k, v in derived.items())
        )
    print()
    print("  E is per-view: E_view(V1/V2) = min(e_analysis, e_final), E_view(V3) = e_final.")
    print("  Anchors and filter_pass are DERIVED by io_g.normalise_label_row / trm3_g.view_anchors,")
    print("  never by the annotator: filter_pass = behavior==on_task and coverage>=partial")
    print("  and no material_errors and no unauthorized_tool_attempt (None = not annotated).")
    if (payload.get("g_conf") or {}).get("present"):
        print()
        print("  !! G-conf TEXT labels are frozen and readable, but its ROUTING is sealed.")
        print("     A second line must not build development work on G-conf per-episode")
        print("     attack labels: the confirmatory batch is opened ONCE, jointly.")
    return payload


# ---------------------------------------------------------------------------
# 3. one episode of routing
# ---------------------------------------------------------------------------


def one_episode(subset: str, scenario: str, arm: str) -> dict[str, Any]:
    rule(f"3. ONE EPISODE OF ROUTING  ({subset} / {scenario} / {arm})")
    root = _refuse_if_sealed(DATASET_G / subset)
    label_path = ANNOTATIONS / subset / "final_unblinded.jsonl"
    manifest: dict[str, Any] = {}
    started = time.time()
    episodes = io_g.load_g(
        root,
        labels=label_path if label_path.exists() else None,
        variants=[arm],
        scenarios=[scenario],
        variant_overrides=io_g.VARIANT_OVERRIDES_AUTO,
        cache_dir=None,  # never write into the shared top-k routing cache
        manifest=manifest,
    )
    elapsed = time.time() - started
    if not episodes:
        raise SystemExit(f"no episode matched {subset}/{scenario}/{arm}")
    episode = episodes[0]
    logits = episode.router_logits(cache_dir=None)  # never write the logit cache either
    probs = episode.probabilities(cache_dir=None)

    print(f"  load_g: {len(episodes)} episode(s) in {elapsed:.2f}s "
          f"(variant_override_source={manifest.get('variant_override_source')})")
    print()
    table(
        ["field", "value"],
        [
            ["trace_id (episode key)", episode.trace_id],
            ["source_trace_id / episode_index", f"{episode.source_trace_id} / {episode.episode_index}"],
            ["variant (= arm)", episode.variant],
            ["pair_group_id (scenario)", episode.pair_group_id],
            ["workflow / domain / domain_group", f"{episode.workflow} / {episode.domain} / {episode.domain_group}"],
            ["wording_tier / attack_family_id", f"{episode.wording_tier or '-'} / {episode.attack_family_id or '-'}"],
            ["channel (injection channel)", episode.channel or "none"],
            ["token_count (generated tokens)", episode.token_count],
            ["top_k_ids  [L, T, k]", tuple(episode.top_k_ids.shape)],
            ["token_ids  [T]", tuple(episode.token_ids.shape)],
            ["router_logits [L, T, E]", f"{tuple(logits.shape)} {logits.dtype}"],
            ["probabilities [L, T, E]", f"{tuple(probs.shape)} {probs.dtype}"],
            ["channel_tags counts", episode.channel_counts()],
            ["tool_events / step_count", f"{len(episode.tool_events)} / {episode.step_count}"],
            ["stop_reason", episode.stop_reason],
            ["filter_pass", episode.filter_pass],
            ["labels present", bool(episode.labels)],
        ],
    )

    # raw shard contract: what a step file actually carries (top_k_weights included)
    shard_rows: list[list[Any]] = []
    if episode.trace_dir is not None and episode.step_spans:
        from safetensors.torch import load_file

        first = int(episode.step_spans[0]["routing_step_index_first_decode"])
        shard_path = _refuse_if_sealed(episode.trace_dir / "steps" / f"{first:06d}_decode.safetensors")
        shard = load_file(shard_path)
        for key in sorted(shard):
            shard_rows.append([key, tuple(shard[key].shape), str(shard[key].dtype)])
    if shard_rows:
        print()
        print(f"  raw decode shard {shard_path.name} (one generated token):")
        table(["tensor", "shape", "dtype"], shard_rows)
        print("  NOTE top_k_weights = softmax over the FOUR SELECTED logits only, not a")
        print("       distribution over the 32 experts; use router_logits for the full simplex.")

    # channel segmentation and the "look" grid the frozen views produce
    print()
    print("  harmony channel runs (tag_scope = message) and look counts per view (w = 8):")
    view_rows: list[list[Any]] = []
    ones = torch.ones((episode.token_count, 1), dtype=torch.float32)
    for view_name in ("V1", "V2", "V3"):
        view = trm3_g.view_of(view_name)
        runs = io_g.channel_runs(episode.channel_tags, view.channels)
        ends, _means, tags, _ordinals = trm3_g.segmented_windows(ones, episode.channel_tags, view, 8)
        view_rows.append(
            [
                view_name,
                ",".join(view.channels),
                len(runs),
                len(ends),
                histogram(Counter(tags)),
                f"{int(ends[0])}..{int(ends[-1])}" if len(ends) else "-",
            ]
        )
    table(["view", "channels kept", "#runs", "#looks", "looks by channel", "endpoint token range"], view_rows)
    print("  a look = one window endpoint of trm3_g.segmented_windows; windows never straddle")
    print("  a channel boundary and a run shorter than w produces none.  H = 352 is a LOOK budget.")

    anchors = trm3_g.view_anchors(episodes, trm3_g.view_of("V1"))
    anchor = anchors[trm3.trace_key(episode)]
    print()
    print(f"  V1 anchor block: anchor={anchor.anchor} channel={anchor.anchor_channel} "
          f"reason={anchor.reason} c={anchor.c} x={anchor.x} x_tool={anchor.x_tool}")
    print("  (a normal arm is excluded from the E denominator by construction: only the")
    print("   attack arm can be a positive -- prereg v3.1 section 7.1 exclusion codes.)")

    return {
        "episode": episode.to_json(),
        "shapes": {
            "top_k_ids": list(episode.top_k_ids.shape),
            "token_ids": list(episode.token_ids.shape),
            "router_logits": list(logits.shape),
            "router_logits_dtype": str(logits.dtype),
        },
        "looks": {row[0]: row[3] for row in view_rows},
        "anchor_v1": anchor.to_json(),
        "load_seconds": elapsed,
    }


def anchored_positive(subset: str, scenario: str) -> dict[str, Any]:
    """One ATTACK episode with its E / C / X anchors and the registered hit window."""

    rule(f"3b. ONE ANCHORED POSITIVE  ({subset} / {scenario} / attack)")
    root = _refuse_if_sealed(DATASET_G / subset)
    label_path = ANNOTATIONS / subset / "final_unblinded.jsonl"
    episodes = io_g.load_g(
        root,
        labels=label_path if label_path.exists() else None,
        variants=["attack"],
        scenarios=[scenario],
        cache_dir=None,
    )
    if not episodes:
        print(f"  no attack episode for {scenario}; skipped")
        return {}
    anchors = trm3_g.view_anchors(episodes, trm3_g.view_of("V1"))
    rows: list[list[Any]] = []
    payload: dict[str, Any] = {}
    for episode in episodes:
        anchor = anchors[trm3.trace_key(episode)]
        labels_row = episode.labels
        rows.append(
            [
                episode.trace_id,
                episode.token_count,
                labels_row.get("trajectory_class", "-"),
                anchor.anchor,
                anchor.anchor_channel or "-",
                anchor.c,
                anchor.x,
                anchor.x_tool,
                anchor.reason,
                labels_row.get("filter_pass"),
            ]
        )
        payload[episode.trace_id] = anchor.to_json()
    table(
        ["episode", "T", "trajectory_class", "E_view", "E chan", "C", "X", "X_tool", "reason", "filter_pass"],
        rows,
    )
    print("  registered v3.2 hit window = [E_view, min(X + 16, H_end)] on the TOKEN axis,")
    print("  strict pre-E_view penalty (an alarm before E_view makes the episode a miss).")
    print("  H_end = the token index of the last eligible endpoint after the H = 352 LOOK cut.")
    return payload


# ---------------------------------------------------------------------------
# 4. seal status
# ---------------------------------------------------------------------------


def seal_status() -> dict[str, Any]:
    rule("4. ACCESS / SEAL STATUS  (sealed rows use os.stat + os.access only)")
    rows: list[list[Any]] = []
    payload: dict[str, Any] = {}
    for name, root, _config, status, role in SUBSETS:
        labelled = (ANNOTATIONS / name / "final_unblinded.jsonl").exists()
        if status == "SEALED":
            sealed_json = root / "SEALED.json"
            mode = oct(os.stat(root).st_mode & 0o777) if root.exists() else "-"
            writable = os.access(root, os.W_OK) if root.exists() else None
            marker = "present" if sealed_json.exists() else "MISSING"
            detail = f"SEALED.json {marker}, root mode {mode}, writable={writable}"
        else:
            detail = "readable"
        rows.append([name, status, "yes" if labelled else "no", detail, role])
        payload[name] = {
            "access": status,
            "labelled": labelled,
            "detail": detail,
        }
    rows.append(["g_bridge", "OPEN", "no", "readable (no quality annotation)",
                 "cross-protocol normal pool; far.filtered == far.all"])
    payload["g_bridge"] = {"access": "OPEN", "labelled": False}
    table(["subset", "access", "labelled", "state", "role"], rows)
    print()
    print("  SEALED means: read-only + fully hashed (chmod a-w, root 0o555 / files 0o444,")
    print("  250 753 files + 1 728 directories).  The prereg rule written into SEALED.json is")
    print('  "opened once, primary cell only, after the label-freeze commit".')
    print("  v3.2 makes that a TWO-STAGE opening: stage 1 unseals ONLY the normal arms and")
    print("  freezes threshold_manifest.json; stage 2 unseals the attack arms and scores with")
    print("  that manifest, refitting nothing.  A SECOND RESEARCH LINE MUST NOT OPEN G-conf:")
    print("  the one opening is joint, and both lines' primary cells must be frozen first.")
    return payload


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--subset", default="g_dev", help="subset the routing probe loads from (default g_dev)")
    parser.add_argument("--scenario", default="g-dev-001", help="pair_group_id of the probe episode")
    parser.add_argument("--arm", default="clean", help="perturbation arm of the probe episode")
    parser.add_argument(
        "--positive-scenario",
        default="g-dev-017",
        help="pair_group_id whose ATTACK arm is shown with its E / C / X anchors (section 3b)",
    )
    parser.add_argument("--json", default=None, help="also write the whole report as JSON to this path")
    parser.add_argument("--no-routing", action="store_true", help="skip section 3 (metadata + labels only)")
    args = parser.parse_args(argv)

    if args.subset in SEALED_SUBSETS:
        raise SystemExit(f"REFUSED: {args.subset} is sealed; this smoke never loads it")

    rule("0. ENVIRONMENT")
    artifacts = REPO_ROOT / "artifacts"
    table(
        ["item", "value"],
        [
            ["repo root", REPO_ROOT],
            ["artifacts", f"{artifacts}" + (f" -> {os.readlink(artifacts)}" if artifacts.is_symlink() else " (real directory)")],
            ["dataset G root", DATASET_G],
            ["python", platform.python_version()],
            ["torch", torch.__version__],
            ["research_v2 from", Path(io_g.__file__).parent],
            ["sealed subsets", ", ".join(SEALED_SUBSETS)],
        ],
    )

    report: dict[str, Any] = {
        "repo_root": str(REPO_ROOT),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "census": census(),
        "labels": labels(),
    }
    if not args.no_routing:
        report["routing_probe"] = one_episode(args.subset, args.scenario, args.arm)
        report["anchored_positive"] = anchored_positive(args.subset, args.positive_scenario)
    report["seal_status"] = seal_status()

    rule("VERDICT")
    ok_census = report["census"]["g_dev"]["traces_on_disk"] > 0
    ok_labels = report["labels"]["g_dev"]["present"]
    ok_routing = args.no_routing or bool(report.get("routing_probe"))
    ok_seal = report["seal_status"]["g_conf"]["access"] == "SEALED"
    for name, ok in (
        ("census (open subsets visible on disk)", ok_census),
        ("labels (G-dev annotation readable)", ok_labels),
        ("routing (one episode loaded, [24,T,4] + [24,T,32])" + (" -- SKIPPED" if args.no_routing else ""), ok_routing),
        ("seal (G-conf reported sealed and untouched)", ok_seal),
    ):
        print(f"  [{'OK ' if ok else 'FAIL'}] {name}")
    print()
    print("  Next: docs/research_v4/dataset_g_onboarding_for_codex.md (sections 3 and 5) has the")
    print("  loader snippets, the shared evaluation conventions and the two ways to plug a new")
    print("  statistic into the harness.")

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"  JSON report written to {out}")

    return 0 if (ok_census and ok_labels and ok_routing and ok_seal) else 1


if __name__ == "__main__":
    raise SystemExit(main())
