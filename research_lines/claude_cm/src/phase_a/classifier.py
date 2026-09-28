"""Small-sample classifier utilities for exploratory routing analyses."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import torch

from .routing_analysis import RoutingSequence, routing_profile, subset_routing_sequence


@dataclass(frozen=True)
class RidgeClassifier:
    """A centered, standardized ridge least-squares classifier."""

    feature_mean: torch.Tensor
    feature_scale: torch.Tensor
    weights: torch.Tensor
    target_mean: torch.Tensor

    def score(self, features: torch.Tensor) -> torch.Tensor:
        standardized = (
            features.to(torch.float64) - self.feature_mean
        ) / self.feature_scale
        return standardized @ self.weights + self.target_mean


@dataclass(frozen=True)
class NormalCentroidClassifier:
    """One-class cosine-distance detector fitted on negative observations."""

    centroid: torch.Tensor

    def score(self, features: torch.Tensor) -> torch.Tensor:
        features = features.to(torch.float64)
        numerator = features @ self.centroid
        denominator = features.norm(dim=1) * self.centroid.norm()
        return 1.0 - numerator / denominator.clamp_min(torch.finfo(torch.float64).eps)


def prefix_sequence(
    sequence: RoutingSequence, token_limit: int | None
) -> RoutingSequence:
    """Return the available prefix, or the entire sequence when the limit is None."""

    sequence.validate()
    if token_limit is None:
        return sequence
    if token_limit <= 0:
        raise ValueError("token limit must be positive")
    return subset_routing_sequence(
        sequence, tuple(range(min(token_limit, len(sequence.token_ids))))
    )


def extract_features(
    sequence: RoutingSequence,
    family: str,
    *,
    token_hash_dimension: int = 2048,
) -> torch.Tensor:
    """Extract one fixed feature family from a routing sequence."""

    sequence.validate()
    if family == "route_probability":
        return routing_profile(sequence)["mean_probability"].flatten().float()
    if family == "route_selection":
        return routing_profile(sequence)["selection_rate"].flatten().float()
    if family == "token_hash":
        if token_hash_dimension <= 0:
            raise ValueError("token hash dimension must be positive")
        features = torch.zeros(token_hash_dimension, dtype=torch.float32)
        for token_id in sequence.token_ids:
            mixed = (int(token_id) * 0x9E3779B1) & 0xFFFFFFFF
            bucket = mixed % token_hash_dimension
            sign = 1.0 if mixed & 0x80000000 else -1.0
            features[bucket] += sign
        norm = features.norm()
        return features / norm if float(norm.item()) else features
    if family == "length":
        return torch.tensor([len(sequence.token_ids)], dtype=torch.float32)
    raise ValueError(f"unknown feature family: {family}")


def fit_ridge_classifier(
    features: torch.Tensor,
    labels: torch.Tensor,
    *,
    penalty: float | None = None,
) -> RidgeClassifier:
    """Fit a fixed-penalty ridge classifier using a dual linear solve."""

    features = features.to(torch.float64)
    labels = labels.to(torch.float64).reshape(-1)
    if features.ndim != 2 or features.shape[0] != labels.numel():
        raise ValueError("ridge features and labels are not observation-aligned")
    if labels.numel() < 2:
        raise ValueError("ridge classifier needs at least two observations")
    feature_mean = features.mean(dim=0)
    feature_scale = features.std(dim=0, unbiased=False)
    feature_scale = torch.where(
        feature_scale > 1e-12, feature_scale, torch.ones_like(feature_scale)
    )
    standardized = (features - feature_mean) / feature_scale
    target_mean = labels.mean()
    centered_target = labels - target_mean
    effective_penalty = float(features.shape[1]) if penalty is None else float(penalty)
    if effective_penalty <= 0:
        raise ValueError("ridge penalty must be positive")
    gram = standardized @ standardized.T
    identity = torch.eye(gram.shape[0], dtype=torch.float64, device=gram.device)
    dual_weights = torch.linalg.solve(
        gram + effective_penalty * identity, centered_target
    )
    weights = standardized.T @ dual_weights
    return RidgeClassifier(feature_mean, feature_scale, weights, target_mean)


def fit_normal_centroid_classifier(
    features: torch.Tensor, labels: torch.Tensor
) -> NormalCentroidClassifier:
    """Fit a negative-only centroid used as an anomaly detector."""

    features = features.to(torch.float64)
    labels = labels.bool().reshape(-1)
    if features.ndim != 2 or features.shape[0] != labels.numel():
        raise ValueError("centroid features and labels are not observation-aligned")
    negatives = features[~labels]
    if negatives.shape[0] == 0:
        raise ValueError("normal centroid needs at least one negative observation")
    centroid = negatives.mean(dim=0)
    if float(centroid.norm().item()) == 0.0:
        raise ValueError("normal centroid has zero norm")
    return NormalCentroidClassifier(centroid)


def leave_one_group_out_scores(
    features: torch.Tensor,
    labels: torch.Tensor,
    groups: Sequence[str],
    *,
    classifier: str,
) -> torch.Tensor:
    """Return observation-aligned scores from leave-one-group-out fits."""

    features = features.float()
    labels = labels.bool().reshape(-1)
    if features.ndim != 2 or features.shape[0] != labels.numel():
        raise ValueError("features and labels are not observation-aligned")
    if len(groups) != labels.numel():
        raise ValueError("groups and labels are not observation-aligned")
    scores = torch.empty(labels.numel(), dtype=torch.float64)
    unique_groups = tuple(dict.fromkeys(groups))
    if len(unique_groups) < 2:
        raise ValueError("group cross-validation needs at least two groups")
    for held_out in unique_groups:
        test_mask = torch.tensor([group == held_out for group in groups])
        train_mask = ~test_mask
        train_labels = labels[train_mask]
        if not bool(train_labels.any()) or not bool((~train_labels).any()):
            raise ValueError(f"training fold for {held_out} does not contain both labels")
        if classifier == "ridge":
            model = fit_ridge_classifier(
                features[train_mask], train_labels.to(torch.float32) * 2.0 - 1.0
            )
        elif classifier == "normal_centroid_distance":
            model = fit_normal_centroid_classifier(features[train_mask], train_labels)
        else:
            raise ValueError(f"unknown classifier: {classifier}")
        scores[test_mask] = model.score(features[test_mask])
    return scores


def binary_auroc(scores: torch.Tensor, labels: torch.Tensor) -> float:
    """Compute AUROC from all positive-negative score pairs with tie credit."""

    scores = scores.to(torch.float64).reshape(-1)
    labels = labels.bool().reshape(-1)
    positives = scores[labels]
    negatives = scores[~labels]
    if positives.numel() == 0 or negatives.numel() == 0:
        raise ValueError("AUROC needs both positive and negative observations")
    comparisons = positives[:, None] - negatives[None, :]
    credit = (comparisons > 0).to(torch.float64)
    credit += 0.5 * (comparisons == 0).to(torch.float64)
    return float(credit.mean().item())


def average_precision(scores: torch.Tensor, labels: torch.Tensor) -> float:
    """Compute non-interpolated average precision, grouping tied scores."""

    scores = scores.to(torch.float64).reshape(-1)
    labels = labels.bool().reshape(-1)
    positive_count = int(labels.sum().item())
    if positive_count == 0:
        raise ValueError("average precision needs a positive observation")
    thresholds = torch.unique(scores).sort(descending=True).values
    previous_recall = 0.0
    result = 0.0
    for threshold in thresholds:
        predicted = scores >= threshold
        true_positives = int((predicted & labels).sum().item())
        predicted_count = int(predicted.sum().item())
        recall = true_positives / positive_count
        precision = true_positives / predicted_count
        result += (recall - previous_recall) * precision
        previous_recall = recall
    return result


def classification_metrics(
    scores: torch.Tensor, labels: torch.Tensor, groups: Sequence[str]
) -> dict[str, float | int]:
    """Summarize global ranking and within-group positive margins."""

    scores = scores.to(torch.float64).reshape(-1)
    labels = labels.bool().reshape(-1)
    if len(groups) != labels.numel():
        raise ValueError("groups and labels are not observation-aligned")
    margins: list[float] = []
    for group in dict.fromkeys(groups):
        mask = torch.tensor([candidate == group for candidate in groups])
        group_labels = labels[mask]
        group_scores = scores[mask]
        if int(group_labels.sum().item()) != 1 or int((~group_labels).sum().item()) < 1:
            raise ValueError(f"group {group} needs exactly one positive and negatives")
        margin = group_scores[group_labels][0] - group_scores[~group_labels].max()
        margins.append(float(margin.item()))
    return {
        "trace_count": labels.numel(),
        "positive_count": int(labels.sum().item()),
        "group_count": len(margins),
        "auroc": binary_auroc(scores, labels),
        "average_precision": average_precision(scores, labels),
        "group_top1_count": sum(margin > 0.0 for margin in margins),
        "group_top1_rate": sum(margin > 0.0 for margin in margins) / len(margins),
        "mean_group_margin": sum(margins) / len(margins),
    }


def balanced_accuracy_threshold(
    scores: torch.Tensor, labels: torch.Tensor
) -> dict[str, float]:
    """Choose an OOF threshold by balanced accuracy, preferring higher ties."""

    scores = scores.to(torch.float64).reshape(-1)
    labels = labels.bool().reshape(-1)
    if not bool(labels.any()) or not bool((~labels).any()):
        raise ValueError("threshold selection needs both labels")
    unique = torch.unique(scores)
    upper = torch.nextafter(unique.max(), torch.tensor(float("inf")))
    candidates = torch.cat((unique, upper.reshape(1)))
    best_threshold = float("-inf")
    best_balanced_accuracy = -1.0
    for candidate in candidates:
        predicted = scores >= candidate
        sensitivity = float((predicted[labels]).float().mean().item())
        specificity = float((~predicted[~labels]).float().mean().item())
        balanced_accuracy = 0.5 * (sensitivity + specificity)
        threshold = float(candidate.item())
        if balanced_accuracy > best_balanced_accuracy or (
            balanced_accuracy == best_balanced_accuracy and threshold > best_threshold
        ):
            best_balanced_accuracy = balanced_accuracy
            best_threshold = threshold
    return {
        "threshold": best_threshold,
        "balanced_accuracy": best_balanced_accuracy,
    }
