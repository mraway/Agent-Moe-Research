#!/usr/bin/env python3
"""Seal the G-conf confirmation batch: hash every trace, then make it read-only.

    g_conf_seal.py --root artifacts/agent_v2/dataset_g/g_conf \
        --packet artifacts/agent_v2/dataset_g/packets/g_conf/packet.jsonl \
        --mapping artifacts/agent_v2/dataset_g/private/g_conf/case_mapping.jsonl

``detector_prereg_v3_1.md`` section 13 says G-conf is **opened once, primary
cell only, after the label-freeze commit**.  "Sealed" is a process rule, not a
cryptographic one, so what this script can do is make the rule *checkable* and
make an accidental write *fail*:

* it records the sha256 of every ``trace.json`` and every ``manifest.jsonl``
  under ``--root`` (the routing tensors themselves are covered transitively:
  ``manifest.jsonl`` carries the per-shard digests that ``routing.validate_trace``
  checks), plus the packet, the private mapping and the resolved run configs;
* it records the frozen subset config hash and the dataset manifest hash, so the
  seal also pins *what was asked for*, not only what was produced;
* it then removes the write bit from every file and directory of the subset
  (``chmod a-w``) and records that it did.

Nothing here reads a routing value, and nothing here scores anything: the seal
is written before any detector has ever seen this subset.  ``--verify`` re-hashes
an existing seal instead of writing one, and ``--no-chmod`` writes the seal
without touching permissions (used by the tests).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from research_v2 import io_g  # noqa: E402

SEAL_VERSION = "dataset-g-conf-seal-1.0.0"
#: additive, non-destructive companion of the seal (prereg v3.2 lead ruling E14 / freeze
#: review DATA-3): the per-ARM trace-set hashes, so stage 1 of the two-stage unsealing can
#: PROVE it read the normal arms and only the normal arms.
ARM_HASHES_VERSION = "dataset-g-arm-hashes-1.0.0"
ARM_HASHES_FILENAME = "ARM_HASHES.json"

#: The rule this seal exists to make checkable, quoted from the pre-registration.
PREREG_SENTENCE = "opened once, primary cell only, after the label-freeze commit"
PREREG_SOURCE = "docs/research_v4/detector_prereg_v3_1.md section 13"
PREREG_SOURCE_LINE = (
    "只开启一次，只跑主格"
    "（V1 / message / w=8 / all24 / α=0.10 / H=352 / E_view / +16 / "
    "严格命中 / 实测 FAR 匹配 / S vs P）"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_of_pairs(pairs: list[tuple[str, str]]) -> str:
    """Order-independent digest of a (relative path, sha256) list."""

    payload = "\n".join(f"{name} {digest}" for name, digest in sorted(pairs))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def collect_trace_hashes(root: Path) -> dict[str, Any]:
    """sha256 of every trace.json / manifest.jsonl under ``root``, run by run."""

    traces: list[dict[str, Any]] = []
    for trace_path in sorted(root.glob("*/*/*/trace.json")):
        trace_dir = trace_path.parent
        manifest_path = trace_dir / "manifest.jsonl"
        payload = json.loads(trace_path.read_text(encoding="utf-8"))
        shard_count = len(list((trace_dir / "steps").glob("*.safetensors")))
        traces.append(
            {
                "path": str(trace_dir.relative_to(root)),
                "trace_id": payload.get("trace_id"),
                "complete": bool(payload.get("complete")),
                "trace_json_sha256": sha256_file(trace_path),
                "manifest_jsonl_sha256": (
                    sha256_file(manifest_path) if manifest_path.is_file() else None
                ),
                "manifest_rows": (
                    sum(1 for line in manifest_path.read_text(encoding="utf-8").splitlines() if line.strip())
                    if manifest_path.is_file()
                    else 0
                ),
                "shard_files": shard_count,
            }
        )
    return {
        "trace_count": len(traces),
        "incomplete": [t["path"] for t in traces if not t["complete"]],
        "trace_json_set_sha256": sha256_of_pairs(
            [(t["path"], t["trace_json_sha256"]) for t in traces]
        ),
        "manifest_jsonl_set_sha256": sha256_of_pairs(
            [(t["path"], t["manifest_jsonl_sha256"] or "") for t in traces]
        ),
        "traces": traces,
    }


def collect_run_configs(root: Path) -> list[dict[str, str]]:
    return [
        {"path": str(path.relative_to(root)), "sha256": sha256_file(path)}
        for path in sorted(root.glob("*/resolved_experiment_config.json"))
    ]


def make_read_only(root: Path, seal_path: Path) -> dict[str, Any]:
    """Drop the write bit from every file and directory under ``root``.

    The seal file itself is written first and then made read-only last, so the
    record of the chmod is inside the thing the chmod protects.
    """

    files = 0
    directories = 0
    for current, dir_names, file_names in os.walk(root, topdown=False):
        current_path = Path(current)
        for name in file_names:
            path = current_path / name
            if path == seal_path:
                continue
            mode = path.stat().st_mode
            path.chmod(mode & ~stat.S_IWUSR & ~stat.S_IWGRP & ~stat.S_IWOTH)
            files += 1
        for name in dir_names:
            path = current_path / name
            mode = path.stat().st_mode
            path.chmod(mode & ~stat.S_IWUSR & ~stat.S_IWGRP & ~stat.S_IWOTH)
            directories += 1
    seal_mode = seal_path.stat().st_mode
    seal_path.chmod(seal_mode & ~stat.S_IWUSR & ~stat.S_IWGRP & ~stat.S_IWOTH)
    files += 1
    root_mode = root.stat().st_mode
    root.chmod(root_mode & ~stat.S_IWUSR & ~stat.S_IWGRP & ~stat.S_IWOTH)
    directories += 1
    return {
        "applied": True,
        "command": "chmod a-w (recursive, files and directories, including the seal file and the subset root)",
        "files_made_read_only": files,
        "directories_made_read_only": directories,
        "root_mode": oct(root.stat().st_mode & 0o7777),
        "seal_mode": oct(seal_path.stat().st_mode & 0o7777),
    }


def build_seal(
    root: Path,
    *,
    subset: str,
    config: Path | None,
    manifest: Path | None,
    packet: Path | None,
    mapping: Path | None,
    extra: list[Path],
) -> dict[str, Any]:
    def entry(path: Path | None) -> dict[str, Any] | None:
        if path is None or not Path(path).is_file():
            return None
        path = Path(path)
        return {
            "path": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
        }

    traces = collect_trace_hashes(root)
    return {
        "seal_version": SEAL_VERSION,
        "subset": subset,
        "root": str(root.relative_to(ROOT)) if root.is_relative_to(ROOT) else str(root),
        "sealed_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "prereg_rule": {
            "sentence": PREREG_SENTENCE,
            "source": PREREG_SOURCE,
            "source_line": PREREG_SOURCE_LINE,
            "meaning": (
                "the routing of this subset stays unread until the algorithm, the "
                "thresholds, the state rule, the gates and the primary metric are all "
                "locked and the run is pinned to the label-freeze commit; opening it "
                "buys exactly one primary-cell run, with no re-tuning, no top-up and "
                "no additional cell"
            ),
            "no_detector_has_been_run": True,
        },
        "frozen_inputs": {
            "subset_config": entry(config),
            "dataset_manifest": entry(manifest),
        },
        "traces": traces,
        "run_configs": collect_run_configs(root),
        "packet": entry(packet),
        "private_mapping": entry(mapping),
        "extra_files": [item for item in (entry(path) for path in extra) if item],
        "read_only": {"applied": False},
    }


def build_arm_hashes(root: Path, *, subset: str) -> dict[str, Any]:
    """Per-ARM ``trace.json`` set hashes of a subset -- ADDITIVE, reads nothing else.

    The seal's ``trace_json_set_sha256`` covers the whole subset in one digest, so it
    cannot witness "stage 1 read the normal arms and nothing else" (freeze review DATA-3
    item 5).  This block adds one digest per arm, computed by the SAME function the
    detector harness uses (:func:`research_v2.io_g.trace_digest`), so
    ``normal_union_sha256`` here is bit-for-bit the ``inputs.normal_trace_set_sha256`` the
    threshold manifest records -- a reviewer can compare the two strings by eye.

    Only ``trace.json`` (and the subset config that names the two hard-normal roles) is
    read; no routing shard is opened, and nothing under ``root`` is written or chmod-ed.
    """

    per_arm: dict[str, Any] = {}
    for variant in io_g.KNOWN_VARIANTS:
        block = io_g.trace_digest(root, variants=(variant,))
        per_arm[variant] = {
            "trace_count": int(block["trace_count"]),
            "sha256": block["sha256"],
        }
    normal = io_g.trace_digest(root, variants=io_g.NORMAL_VARIANTS)
    everything = io_g.trace_digest(root)
    return {
        "arm_hashes_version": ARM_HASHES_VERSION,
        "subset": subset,
        "root": str(root.relative_to(ROOT)) if root.is_relative_to(ROOT) else str(root),
        "computed_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "arm_identity_source": (
            "trace.json perturbation.arm + arm directory name, with the subset config's "
            "factory.normal_variant recovering benign_lexical / legitimate_refusal"
        ),
        "variant_override_source": normal.get("variant_override_source"),
        "variant_override_config": normal.get("variant_override_config"),
        "per_arm": per_arm,
        "normal_variants": list(io_g.NORMAL_VARIANTS),
        "normal_union_sha256": normal["sha256"],
        "normal_union_trace_count": int(normal["trace_count"]),
        "all_traces_sha256": everything["sha256"],
        "all_traces_count": int(everything["trace_count"]),
        "rule": (
            "normal_union_sha256 is exactly the per-directory digest "
            "run_detectors_g.normal_trace_manifest records in "
            "inputs.normal_traces_per_dir[*].sha256 (it hashes RELATIVE paths plus file "
            "content, so it does not depend on how the directory was spelled on the "
            "command line); the manifest's top-level normal_trace_set_sha256 additionally "
            "binds the run-dir string, so compare against the per_dir entry"
        ),
        "reads": "trace.json metadata + the subset config only; no routing shard",
        "writes_nothing_under_root": True,
    }


def arm_hashes_destination(root: Path, subset: str, out: Path | None) -> tuple[Path, str]:
    """Where the arm-hash file may be written without disturbing a sealed subset."""

    if out is not None:
        return Path(out), "explicit --out"
    if os.access(root, os.W_OK):
        return root / ARM_HASHES_FILENAME, "next to SEALED.json (the subset root is writable)"
    fallback = ROOT / "artifacts" / "agent_v2" / "dataset_g" / f"{subset}_meta"
    return (
        fallback / ARM_HASHES_FILENAME,
        f"the subset root is READ-ONLY (sealed), so the file went to {fallback} instead; "
        "nothing under the sealed root was created, modified or chmod-ed",
    )


def verify(seal_path: Path) -> int:
    seal = json.loads(seal_path.read_text(encoding="utf-8"))
    root = ROOT / seal["root"]
    bad: list[str] = []
    for record in seal["traces"]["traces"]:
        trace_dir = root / record["path"]
        got = sha256_file(trace_dir / "trace.json")
        if got != record["trace_json_sha256"]:
            bad.append(f"{record['path']}/trace.json {record['trace_json_sha256']} -> {got}")
        manifest = trace_dir / "manifest.jsonl"
        if record["manifest_jsonl_sha256"] is not None:
            got = sha256_file(manifest)
            if got != record["manifest_jsonl_sha256"]:
                bad.append(
                    f"{record['path']}/manifest.jsonl {record['manifest_jsonl_sha256']} -> {got}"
                )
    for name in ("packet", "private_mapping"):
        record = seal.get(name)
        if record and (ROOT / record["path"]).is_file():
            got = sha256_file(ROOT / record["path"])
            if got != record["sha256"]:
                bad.append(f"{record['path']} {record['sha256']} -> {got}")
    print(
        json.dumps(
            {
                "seal": str(seal_path),
                "trace_count": seal["traces"]["trace_count"],
                "mismatches": bad,
                "verified": not bad,
            },
            indent=2,
        )
    )
    return 1 if bad else 0


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subset", default="g_conf")
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--manifest", type=Path, default=ROOT / "configs/dataset_g/manifest.json")
    parser.add_argument("--packet", type=Path, default=None)
    parser.add_argument("--mapping", type=Path, default=None)
    parser.add_argument("--extra", type=Path, action="append", default=[])
    parser.add_argument("--out", type=Path, default=None, help="default <root>/SEALED.json")
    parser.add_argument("--no-chmod", action="store_true")
    parser.add_argument("--verify", type=Path, default=None, help="re-hash an existing seal file")
    parser.add_argument(
        "--arm-hashes",
        action="store_true",
        help="ADDITIVE mode: compute the per-ARM trace-set hashes of --root and write "
        f"{ARM_HASHES_FILENAME}.  It re-seals NOTHING, changes no permission and touches "
        "no file under the subset root; if that root is read-only the file is written "
        "under artifacts/agent_v2/dataset_g/<subset>_meta/ and the output says so",
    )
    args = parser.parse_args()
    if args.verify is not None:
        return args
    if args.root is None:
        args.root = ROOT / "artifacts" / "agent_v2" / "dataset_g" / args.subset
    if args.config is None:
        args.config = ROOT / "configs" / "dataset_g" / f"{args.subset}.json"
    if args.packet is None:
        args.packet = ROOT / f"artifacts/agent_v2/dataset_g/packets/{args.subset}/packet.jsonl"
    if args.mapping is None:
        args.mapping = ROOT / f"artifacts/agent_v2/dataset_g/private/{args.subset}/case_mapping.jsonl"
    if args.out is None:
        args.out = args.root / "SEALED.json"
    return args


def main() -> int:
    args = _args()
    if args.verify is not None:
        return verify(args.verify.resolve())
    root = args.root.resolve()
    if not root.is_dir():
        raise SystemExit(f"no such subset root: {root}")
    if args.arm_hashes:
        payload = build_arm_hashes(root, subset=args.subset)
        # ``args.out`` defaults to ``args.root / "SEALED.json"`` with ``args.root``
        # exactly as it was typed, while ``root`` is resolved.  Comparing the two
        # unresolved-vs-resolved paths made a RELATIVE --root look like an explicit
        # --out and wrote the arm hashes INTO SEALED.json (observed on g_conf2 before
        # its seal existed).  Resolve both sides before deciding.
        requested_out = None if args.out is None else args.out.resolve()
        default_out = (args.root / "SEALED.json").resolve()
        destination, placement = arm_hashes_destination(
            root,
            args.subset,
            None if requested_out is None or requested_out == default_out else requested_out,
        )
        payload["written_to"] = str(destination)
        payload["placement"] = placement
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(
            f"arm hashes for {payload['subset']}: "
            + ", ".join(
                f"{arm}={block['trace_count']}@{block['sha256'][:12]}"
                for arm, block in payload["per_arm"].items()
                if block["trace_count"]
            )
        )
        print(f"  normal union = {payload['normal_union_sha256'][:16]} "
              f"({payload['normal_union_trace_count']} traces) -- this is the string "
              "stage 1's inputs.normal_traces_per_dir[*].sha256 must equal")
        print(f"  {placement}")
        print(f"  wrote {destination}")
        return 0
    seal = build_seal(
        root,
        subset=args.subset,
        config=args.config,
        manifest=args.manifest,
        packet=args.packet,
        mapping=args.mapping,
        extra=list(args.extra),
    )
    out = args.out.resolve()
    out.write_text(json.dumps(seal, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if not args.no_chmod:
        seal["read_only"] = make_read_only(root, out)
        # rewrite the seal with the chmod record, then re-apply the read-only bit
        out.chmod(out.stat().st_mode | stat.S_IWUSR)
        out.write_text(json.dumps(seal, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        out.chmod(out.stat().st_mode & ~stat.S_IWUSR & ~stat.S_IWGRP & ~stat.S_IWOTH)
    print(
        f"sealed {seal['subset']}: {seal['traces']['trace_count']} traces, "
        f"trace_json_set {seal['traces']['trace_json_set_sha256'][:16]}..., "
        f"manifest_set {seal['traces']['manifest_jsonl_set_sha256'][:16]}..., "
        f"read_only={seal['read_only']['applied']} -> {out}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
