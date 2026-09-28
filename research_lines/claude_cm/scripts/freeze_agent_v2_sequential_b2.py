#!/usr/bin/env python3
"""Freeze B1-developed sequential models before held-out B2 evaluation."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Sequence

import torch
from safetensors.torch import save_file


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from analyze_agent_v2_b1_routing import Observation, _load_observations  # noqa: E402
from explore_agent_v2_sequential_b1 import (  # noqa: E402
    CALIBRATION_FOLD,
    DEVELOPMENT_FOLDS,
    TraceWindows,
    _calibrate_threshold,
    _combined_windows,
    _training_anchors,
    _trace_windows,
)
from phase_a.classifier import fit_ridge_classifier  # noqa: E402


EXPECTED_DEVELOPMENT_SHA256 = (
    "fdac2638ae687a549ae7594548c33ba1d796fdc561ae1feddce52ce895d98c4e"
)
EXPECTED_SAMPLE_INDEX_SHA256 = (
    "f1a95c5bf36d3233f445e4cb507c89e59c13eda1619af317c4efefadcea72df1"
)
SELECTED_ROUTE_FAMILY = "route_selection"
SELECTED_WIDTH = 16
SELECTED_PERSISTENCE = 1
SELECTED_CALIBRATION_QUANTILE = 0.99
MODEL_PATH = ROOT / "configs" / "models" / "agent_v2_sequential_b2.safetensors"
METADATA_PATH = ROOT / "configs" / "models" / "agent_v2_sequential_b2.json"


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "run_dir",
        nargs="?",
        type=Path,
        default=ROOT / "artifacts" / "agent_v2" / "agent_v2_5_b1",
    )
    parser.add_argument("--model-path", type=Path, default=MODEL_PATH)
    parser.add_argument("--metadata-path", type=Path, default=METADATA_PATH)
    return parser.parse_args()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _audit_development(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "sequential_development.json"
    if _sha256(path) != EXPECTED_DEVELOPMENT_SHA256:
        raise ValueError("B1 sequential development result differs from selection input")
    payload = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "feature_family": SELECTED_ROUTE_FAMILY,
        "width": SELECTED_WIDTH,
        "persistence": SELECTED_PERSISTENCE,
        "calibration_quantile": SELECTED_CALIBRATION_QUANTILE,
    }
    selected = payload["selected_candidate"]
    if any(selected[key] != value for key, value in expected.items()):
        raise ValueError("frozen settings do not match the B1-selected candidate")
    if not selected["eligible"]:
        raise ValueError("B1-selected candidate did not satisfy development constraints")
    return payload


def _fit_and_calibrate(
    name: str,
    rows: Sequence[TraceWindows],
    tensors: dict[str, torch.Tensor],
    metadata: dict[str, Any],
    feature_family: str,
) -> None:
    development = [
        row for row in rows if row.observation.fold in DEVELOPMENT_FOLDS
    ]
    calibration = [
        row for row in rows if row.observation.fold == CALIBRATION_FOLD
    ]
    features, targets = _training_anchors(development)
    model = fit_ridge_classifier(features, targets)
    calibration_scores = {
        row.observation.trace_id: model.score(row.features).reshape(-1)
        for row in calibration
    }
    threshold, maxima = _calibrate_threshold(
        calibration,
        calibration_scores,
        SELECTED_PERSISTENCE,
        SELECTED_CALIBRATION_QUANTILE,
    )
    tensors[f"{name}.feature_mean"] = model.feature_mean.contiguous()
    tensors[f"{name}.feature_scale"] = model.feature_scale.contiguous()
    tensors[f"{name}.weights"] = model.weights.contiguous()
    tensors[f"{name}.target_mean"] = model.target_mean.reshape(1).contiguous()
    metadata[name] = {
        "feature_family": feature_family,
        "feature_dimension": int(features.shape[1]),
        "window_width": SELECTED_WIDTH,
        "persistence": SELECTED_PERSISTENCE,
        "classifier": "ridge_least_squares",
        "penalty": float(features.shape[1]),
        "training_only_standardization": True,
        "training_folds": list(DEVELOPMENT_FOLDS),
        "training_trace_count": len(development),
        "training_anchor_count": int(targets.numel()),
        "training_positive_anchor_count": int((targets > 0).sum().item()),
        "calibration_fold": CALIBRATION_FOLD,
        "calibration_quantile": SELECTED_CALIBRATION_QUANTILE,
        "calibration_negative_segment_count": len(maxima),
        "threshold": threshold,
    }


def freeze(
    run_dir: Path, model_path: Path, metadata_path: Path
) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    _audit_development(run_dir)
    if _sha256(run_dir / "sample_index.jsonl") != EXPECTED_SAMPLE_INDEX_SHA256:
        raise ValueError("B1 sample index differs from sequential development input")
    observations, valid = _load_observations(run_dir)
    headline: list[Observation] = [
        row for row in observations if row.brief == "absent"
    ]
    route = _trace_windows(headline, SELECTED_ROUTE_FAMILY, SELECTED_WIDTH)
    token = _trace_windows(headline, "token_hash", SELECTED_WIDTH)
    combined = _combined_windows(route, token)
    tensors: dict[str, torch.Tensor] = {}
    models: dict[str, Any] = {}
    _fit_and_calibrate(
        "route", route, tensors, models, SELECTED_ROUTE_FAMILY
    )
    _fit_and_calibrate(
        "token_hash", token, tensors, models, "token_hash_2048"
    )
    _fit_and_calibrate(
        "route_plus_token_hash",
        combined,
        tensors,
        models,
        f"{SELECTED_ROUTE_FAMILY}+token_hash_2048",
    )

    model_path = model_path.resolve()
    metadata_path = metadata_path.resolve()
    model_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    save_file(
        tensors,
        model_path,
        metadata={
            "format": "agent-v2-sequential-b2-frozen-ridge-v1",
            "source_sample_index_sha256": EXPECTED_SAMPLE_INDEX_SHA256,
            "source_development_sha256": EXPECTED_DEVELOPMENT_SHA256,
        },
    )
    payload = {
        "schema_version": 1,
        "artifact_id": "agent-v2-sequential-b2-frozen-v1",
        "status": "frozen_before_b2_sequential_evaluation",
        "source_experiment_id": "agent-v2.5-b1-development-signal",
        "source_sample_index_sha256": EXPECTED_SAMPLE_INDEX_SHA256,
        "source_development_sha256": EXPECTED_DEVELOPMENT_SHA256,
        "routing_validation_pass_count": valid,
        "primary_model": "route",
        "control_models": ["token_hash", "route_plus_token_hash"],
        "models": models,
        "tensor_file": str(model_path.relative_to(ROOT)),
        "tensor_sha256": _sha256(model_path),
        "audit": {
            "b2_read": False,
            "development_folds_used_for_weights": list(DEVELOPMENT_FOLDS),
            "calibration_fold_used_for_threshold_only": CALIBRATION_FOLD,
        },
    }
    metadata_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        **payload,
        "metadata_file": str(metadata_path.relative_to(ROOT)),
        "metadata_sha256": _sha256(metadata_path),
    }


def main() -> int:
    args = _args()
    payload = freeze(args.run_dir, args.model_path, args.metadata_path)
    print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
