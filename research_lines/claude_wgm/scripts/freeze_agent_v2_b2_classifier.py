#!/usr/bin/env python3
"""Freeze the B1-selected classifiers before collecting Agent v2.5 B2."""

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

from analyze_agent_v2_b1_routing import (  # noqa: E402
    EXPECTED_SAMPLE_INDEX_SHA256,
    Observation,
    _feature_matrix,
    _load_final_prefill,
    _load_observations,
)
from phase_a.classifier import (  # noqa: E402
    average_precision,
    balanced_accuracy_threshold,
    binary_auroc,
    extract_features,
    fit_ridge_classifier,
    leave_one_group_out_scores,
)


MODEL_PATH = ROOT / "configs" / "models" / "agent_v2_5_b2_frozen.safetensors"
METADATA_PATH = ROOT / "configs" / "models" / "agent_v2_5_b2_frozen.json"
FREEZE_SOURCE_COMMIT = "53d67eb"


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


def _metadata_categories(
    observations: Sequence[Observation],
) -> dict[str, tuple[str, ...]]:
    return {
        name: tuple(sorted({str(getattr(row, name)) for row in observations}))
        for name in ("brief", "workflow", "channel", "domain")
    }


def _metadata_matrix(
    observations: Sequence[Observation],
    categories: dict[str, tuple[str, ...]],
) -> torch.Tensor:
    rows: list[list[float]] = []
    for observation in observations:
        values: list[float] = []
        for name, choices in categories.items():
            current = str(getattr(observation, name))
            values.extend(float(current == choice) for choice in choices)
        rows.append(values)
    return torch.tensor(rows, dtype=torch.float32)


def _metrics(scores: torch.Tensor, labels: torch.Tensor) -> dict[str, Any]:
    return {
        "auroc": binary_auroc(scores, labels),
        "average_precision": average_precision(scores, labels),
        **balanced_accuracy_threshold(scores, labels),
    }


def _freeze_model(
    *,
    name: str,
    features: torch.Tensor,
    labels: torch.Tensor,
    folds: Sequence[str],
    tensors: dict[str, torch.Tensor],
    metadata: dict[str, Any],
    feature_family: str,
    window: str,
) -> None:
    oof_scores = leave_one_group_out_scores(
        features, labels, folds, classifier="ridge"
    )
    model = fit_ridge_classifier(features, labels.float() * 2.0 - 1.0)
    tensors[f"{name}.feature_mean"] = model.feature_mean.contiguous()
    tensors[f"{name}.feature_scale"] = model.feature_scale.contiguous()
    tensors[f"{name}.weights"] = model.weights.contiguous()
    tensors[f"{name}.target_mean"] = model.target_mean.reshape(1).contiguous()
    metadata[name] = {
        "feature_family": feature_family,
        "window": window,
        "feature_dimension": int(features.shape[1]),
        "classifier": "ridge_least_squares",
        "penalty": float(features.shape[1]),
        "training_only_standardization": True,
        "oof": _metrics(oof_scores, labels),
    }


def freeze(
    run_dir: Path, model_path: Path, metadata_path: Path
) -> dict[str, Any]:
    observations, valid = _load_observations(run_dir.resolve())
    attacks = [row for row in observations if row.arm == "attack"]
    training = [row for row in attacks if row.brief == "absent"]
    if len(training) != 40 or sum(row.positive for row in training) != 24:
        raise ValueError("frozen B2 training cohort must be 40 attacks / 24 drift")
    labels = torch.tensor([row.positive for row in training])
    folds = [row.fold for row in training]
    tensors: dict[str, torch.Tensor] = {}
    models: dict[str, Any] = {}

    _freeze_model(
        name="decode_first16_route_selection",
        features=_feature_matrix(training, "route_selection", 16),
        labels=labels,
        folds=folds,
        tensors=tensors,
        metadata=models,
        feature_family="route_selection",
        window="first_16_decode_tokens",
    )
    _freeze_model(
        name="decode_first16_token_hash",
        features=_feature_matrix(training, "token_hash", 16),
        labels=labels,
        folds=folds,
        tensors=tensors,
        metadata=models,
        feature_family="token_hash_2048",
        window="first_16_decode_tokens",
    )
    _freeze_model(
        name="decode_full_route_selection",
        features=_feature_matrix(training, "route_selection", None),
        labels=labels,
        folds=folds,
        tensors=tensors,
        metadata=models,
        feature_family="route_selection",
        window="full_decode_secondary_only",
    )

    prefill_features: list[torch.Tensor] = []
    for observation in training:
        prefill_features.append(
            extract_features(
                _load_final_prefill(observation.trace_dir), "route_selection"
            )
        )
    _freeze_model(
        name="prefill_route_selection",
        features=torch.stack(prefill_features),
        labels=labels,
        folds=folds,
        tensors=tensors,
        metadata=models,
        feature_family="route_selection",
        window="entire_final_prefill_context_only",
    )

    categories = _metadata_categories(attacks)
    _freeze_model(
        name="nuisance_metadata",
        features=_metadata_matrix(training, categories),
        labels=labels,
        folds=folds,
        tensors=tensors,
        metadata=models,
        feature_family="one_hot_metadata",
        window="trace_level",
    )

    model_path = model_path.resolve()
    metadata_path = metadata_path.resolve()
    model_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    save_file(
        tensors,
        model_path,
        metadata={
            "format": "agent-v2.5-b2-frozen-ridge-v1",
            "source_sample_index_sha256": EXPECTED_SAMPLE_INDEX_SHA256,
        },
    )
    tensor_sha256 = _sha256(model_path)
    payload = {
        "schema_version": 1,
        "artifact_id": "agent-v2.5-b2-frozen-classifiers-v1",
        "status": "frozen_before_b2_collection",
        "freeze_source_commit": FREEZE_SOURCE_COMMIT,
        "source_experiment_id": "agent-v2.5-b1-development-signal",
        "source_sample_index_sha256": EXPECTED_SAMPLE_INDEX_SHA256,
        "routing_validation_pass_count": valid,
        "training_cohort": {
            "selection": "arm=attack and response_brief_condition=absent",
            "trace_count": len(training),
            "positive_count": int(labels.sum().item()),
            "negative_count": int((~labels).sum().item()),
            "trace_ids": [row.trace_id for row in training],
        },
        "primary_model": "decode_first16_route_selection",
        "secondary_models": [
            "decode_first16_token_hash",
            "decode_full_route_selection",
            "prefill_route_selection",
            "nuisance_metadata",
        ],
        "metadata_categories": {
            name: list(values) for name, values in categories.items()
        },
        "models": models,
        "tensor_file": str(model_path.relative_to(ROOT)),
        "tensor_sha256": tensor_sha256,
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
