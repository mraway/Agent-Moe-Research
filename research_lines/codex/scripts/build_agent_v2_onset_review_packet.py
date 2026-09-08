#!/usr/bin/env python3
"""Build a routing-free, label-free packet for independent onset review."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from analyze_agent_v2_engagement_mechanism import _config_hash  # noqa: E402
from apply_agent_v2_engagement_adjudications import _decode_pieces  # noqa: E402
from phase_a.normal_manifold import sha256  # noqa: E402


PLAN = "docs/agent_v2_onset_reliability_audit_plan.md"
PLAN_SHA256 = "22397eced11b375e921bdc3947be38a566578d876b97c22eb76a0159333574bf"
REPLAY_INDEX_SHA256 = "5399b0b32769c8ef98722080da9ef1f159f586fba6d1d907656afcc8e34eac10"
PREFIX_AUDIT_SHA256 = "3405298d0cc5d29a07899f32dc113bb84d08334de51bab6ce1dd442053a41359"
SOURCE_PACKET_SHA256 = "616056f059ac046d5feea2d11b1cb8602b4b31346858d677c32bef0532bf9405"
EXPECTED_CONFIG_HASH = "ab00100f56298fad9c08d4e2075aa70db7d2e1071ce609d9b4209e4d241abebf"
DEFAULT_REPLAY = ROOT / "artifacts" / "agent_v2" / "agent_v2_5_b2_horizon384"
DEFAULT_OUTPUT = ROOT / "artifacts" / "agent_v2" / "onset_reliability_audit_v1"


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay", type=Path, default=DEFAULT_REPLAY)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def opaque_case_id(trace_id: str) -> str:
    digest = hashlib.sha256(f"agent-v2-onset-audit-v1::{trace_id}".encode()).hexdigest()
    return f"onset-{digest[:12]}"


def visible_token_pieces(pieces: list[str], content: str) -> list[str]:
    """Trim generation-stop suffixes while preserving visible token boundaries."""

    normalized = [piece.replace("\ufffd\ufffd", "≈") for piece in pieces]
    for index in range(len(normalized) - 1):
        if normalized[index].endswith("\ufffd") and normalized[index + 1].startswith(
            "\ufffd"
        ):
            normalized[index] = normalized[index][:-1] + "≈"
            normalized[index + 1] = normalized[index + 1][1:]
    decoded = "".join(normalized)
    if not decoded.startswith(content):
        raise ValueError("decode pieces do not reconstruct visible output after normalization")
    result: list[str] = []
    remaining = len(content)
    for piece in normalized:
        if remaining <= 0:
            break
        visible = piece[:remaining]
        result.append(visible)
        remaining -= len(visible)
    if remaining != 0 or "".join(result) != content:
        raise ValueError("visible output could not be reconstructed from decode pieces")
    return result


def validate_inputs(replay_dir: Path) -> dict[str, str]:
    plan_path = ROOT / PLAN
    index_path = replay_dir / "sample_index.jsonl"
    prefix_path = replay_dir / "prefix_replay_audit.json"
    packet_path = replay_dir / "engagement_review_packet.jsonl"
    checks = {
        "plan_sha256": sha256(plan_path),
        "sample_index_sha256": sha256(index_path),
        "prefix_audit_sha256": sha256(prefix_path),
        "source_review_packet_sha256": sha256(packet_path),
    }
    expected = {
        "plan_sha256": PLAN_SHA256,
        "sample_index_sha256": REPLAY_INDEX_SHA256,
        "prefix_audit_sha256": PREFIX_AUDIT_SHA256,
        "source_review_packet_sha256": SOURCE_PACKET_SHA256,
    }
    if checks != expected:
        raise ValueError(f"onset-audit input hash mismatch: {checks}")
    if _read_json(prefix_path).get("exact_paired_replay_passed") is not True:
        raise ValueError("exact-prefix replay gate failed")
    config = _read_json(replay_dir / "resolved_experiment_config.json")
    if _config_hash(config) != EXPECTED_CONFIG_HASH:
        raise ValueError("replay config hash mismatch")
    return checks


def build_packet(replay_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    index = _read_jsonl(replay_dir / "sample_index.jsonl")
    source = _read_jsonl(replay_dir / "engagement_review_packet.jsonl")
    attack_index = {str(row["trace_id"]): row for row in index if row["arm"] == "attack"}
    source_by_id = {str(row["trace_id"]): row for row in source}
    if len(index) != 240 or len(attack_index) != 80 or len(source_by_id) != 80:
        raise ValueError("replay cohort size changed")
    if set(attack_index) != set(source_by_id):
        raise ValueError("review source and replay attack IDs differ")

    packet: list[dict[str, Any]] = []
    mapping: list[dict[str, Any]] = []
    for trace_id in sorted(attack_index):
        index_row = attack_index[trace_id]
        source_row = source_by_id[trace_id]
        trace_dir = (replay_dir / index_row["relative_path"]).resolve()
        trace = _read_json(trace_dir / "trace.json")
        event_index = int(source_row["event_index"])
        event = trace["events"][event_index]
        if event["kind"] != "model_generation":
            raise ValueError(f"review event is not a generation: {trace_id}")
        content = str(event["content"])
        pieces = visible_token_pieces(
            _decode_pieces(trace_dir, int(event["agent_step"])), content
        )
        case_id = opaque_case_id(trace_id)
        packet.append(
            {
                "case_id": case_id,
                "authorized_user_messages": source_row["model_visible_user_messages"],
                "injected_task": source_row["attack_input"],
                "target_domain": source_row["target_domain"],
                "final_output": content,
                "output_tokens": [
                    {"index": index, "text": piece}
                    for index, piece in enumerate(pieces)
                ],
                "review_fields": {
                    "trajectory_class": None,
                    "engagement": None,
                    "commitment": None,
                    "execution": None,
                    "task_specific_transition_sentence": None,
                    "support_resume": None,
                    "reviewer": None,
                    "overall_confidence": None,
                },
            }
        )
        mapping.append(
            {
                "case_id": case_id,
                "trace_id": trace_id,
                "pair_group_id": index_row["pair_group_id"],
                "relative_path": index_row["relative_path"],
                "event_index": event_index,
            }
        )
    packet.sort(key=lambda row: str(row["case_id"]))
    mapping.sort(key=lambda row: str(row["case_id"]))
    if len({row["case_id"] for row in packet}) != 80:
        raise ValueError("opaque case IDs collided")
    return packet, mapping


def calculate(replay_dir: Path, output_dir: Path) -> dict[str, Any]:
    input_hashes = validate_inputs(replay_dir)
    packet, mapping = build_packet(replay_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    packet_path = output_dir / "blind_review_packet.jsonl"
    mapping_path = output_dir / "private_case_mapping.jsonl"
    _write_jsonl(packet_path, packet)
    _write_jsonl(mapping_path, mapping)
    manifest = {
        "schema_version": 1,
        "analysis_id": "agent-v2-onset-reliability-audit-v1",
        "status": "awaiting_independent_review",
        "plan": PLAN,
        "plan_sha256": PLAN_SHA256,
        "b3_used": False,
        "case_count": len(packet),
        "input_hashes": input_hashes,
        "blind_packet": {
            "path": str(packet_path),
            "sha256": sha256(packet_path),
            "contains_routing": False,
            "contains_existing_behavior_labels": False,
            "contains_existing_onsets_or_evidence": False,
            "contains_detector_predictions": False,
            "contains_trace_ids": False,
        },
        "private_mapping": {
            "path": str(mapping_path),
            "sha256": sha256(mapping_path),
        },
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    args = _args()
    manifest = calculate(args.replay.resolve(), args.output_dir.resolve())
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
