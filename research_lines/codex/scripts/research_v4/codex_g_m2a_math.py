"""Score-blind matching and paired descriptive arithmetic for G M2-A."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

import numpy as np

CHANNELS = ("analysis", "commentary", "final")
KINDS = ("E_level", "X_level", "E_did", "X_did", "EMX_complete")
QUALITIES = ("filtered", "all")
SCOPES = ("S", "F")


def look_geometry(tags: list[str], steps: list[dict], ends: np.ndarray, width: int = 8) -> list[tuple | None]:
    """Exact coordinates; deliberately accepts neither routing scores nor labels."""
    steps = sorted(steps, key=lambda s: int(s["agent_step"]))
    t = len(tags)
    step_indices = np.full(t, -1, dtype=int)
    cursor, previous = 0, -1
    for local_step, step in enumerate(steps):
        offset, count, raw_step = [int(step[k]) for k in ("global_token_offset", "output_token_count", "agent_step")]
        if offset != cursor or count < 0 or raw_step <= previous:
            raise ValueError("noncontiguous or unordered step metadata")
        step_indices[offset:offset + count] = local_step
        cursor, previous = offset + count, raw_step
    if cursor != t or (step_indices < 0).any():
        raise ValueError("steps do not cover the episode token axis")
    token_coordinates = []
    runs = Counter()
    start, run_number = 0, 0
    for token, tag in enumerate(tags):
        if token == 0 or tags[token - 1] != tag or step_indices[token - 1] != step_indices[token]:
            start = token
            run_number = runs[(int(step_indices[token]), tag)]
            runs[(int(step_indices[token]), tag)] += 1
        token_coordinates.append((tag, int(step_indices[token]), run_number, token - start))
    result = []
    for token in ends:
        if not 0 <= token < t:
            raise ValueError("look end outside token axis")
        coordinate = token_coordinates[int(token)]
        result.append(coordinate if coordinate[0] in CHANNELS and coordinate[-1] >= width - 1 else None)
    return result


def query_groups(row: dict, ends: np.ndarray, kind: str) -> dict:
    """Offline event footprint on the already H-truncated M1 grid."""
    e, x = row["anchor"]["anchor"], row["anchor"]["x"]
    if kind not in KINDS:
        raise ValueError("unknown query kind")
    if e is None or ((kind.startswith("X") or kind == "EMX_complete") and x is None):
        return {"status": "missing_event", "groups": {}, "bounds": {}, "horizon_cut": False}
    anchor = x if kind.startswith("X") else e
    if kind == "EMX_complete":
        bounds = {"E": (e, e + 16), "middle": (e + 17, x - 1), "X": (x, x + 16)}
    else:
        bounds = {"post": (anchor, anchor + 16)}
        if kind.endswith("did"):
            bounds = {"pre": (max(0, anchor - 16), anchor - 1), **bounds}
    groups = {name: np.flatnonzero((ends >= lo) & (ends <= hi)).tolist() for name, (lo, hi) in bounds.items()}
    status = "eligible"
    for name, (lo, hi) in bounds.items():
        if hi < lo:
            status = f"empty_interval:{name}"
            break
        if not groups[name]:
            status = f"no_look:{name}"
            break
    return {"status": status, "bounds": bounds, "groups": groups,
            "horizon_cut": any(hi > row["h_end"] for _, hi in bounds.values())}


def donor_candidates(target: dict, metadata: dict[str, dict], scope: str, quality: str) -> list[str]:
    if scope not in SCOPES or quality not in QUALITIES:
        raise ValueError("unknown matching policy")
    selected = []
    for key, donor in metadata.items():
        if donor["variant"] not in {"clean", "benign_control", "benign_lexical"}:
            continue
        if quality == "filtered" and donor["filter_pass"] is not True:
            continue
        if donor["fold"] != target["fold"] or donor["episode_index"] != target["episode_index"]:
            continue
        if scope == "S":
            same = donor["scenario"] == target["scenario"]
        else:
            same = bool(target["fixture"] and target["routine_template_id"]
                        and donor["fixture"] == target["fixture"]
                        and donor["routine_template_id"] == target["routine_template_id"])
        if same:
            selected.append(key)
    return sorted(selected)


def match_footprint(target: dict, query: dict, metadata: dict[str, dict], scope: str, quality: str) -> dict:
    """Choose every complete structural donor, never a partial or score-based match."""
    candidates = donor_candidates(target, metadata, scope, quality)
    result = {"scope": scope, "quality": quality, "candidate_donors": len(candidates),
              "status": query["status"], "donors": [], "donor_scenarios": 0}
    if query["status"] != "eligible":
        return result
    union = sorted({i for indices in query["groups"].values() for i in indices})
    coordinates = [target["coordinates"][i] for i in union]
    if any(c is None for c in coordinates):
        result["status"] = "target_cross_step_window"
        return result
    if not candidates:
        result["status"] = "no_candidate_donor"
        return result
    for key in candidates:
        donor = metadata[key]
        lookup = donor["coordinate_lookup"]
        mapped = [lookup.get(tuple(c)) for c in coordinates]
        if any(i is None for i in mapped):
            continue
        if any(b <= a for a, b in zip(mapped, mapped[1:])):
            continue
        mapping = dict(zip(union, mapped))
        result["donors"].append({"key": key, "groups": {
            name: [mapping[i] for i in indices] for name, indices in query["groups"].items()}})
    count = len({metadata[d["key"]]["scenario"] for d in result["donors"]})
    result["donor_scenarios"] = count
    result["status"] = ("no_complete_donor" if not result["donors"]
                        else "insufficient_donor_scenarios" if scope == "F" and count < 3 else "matched")
    return result


def paired_fields(target_groups: dict[str, np.ndarray], donor_groups: list[dict[str, np.ndarray]]) -> tuple[dict, list[dict]]:
    """Window -> donor episode -> target episode weighting; [R,band] matrices."""
    if not donor_groups:
        raise ValueError("paired readout needs a donor")
    target = {name: np.asarray(values).mean(0) for name, values in target_groups.items()}
    donors = [{name: np.asarray(values).mean(0) for name, values in group.items()} for group in donor_groups]
    fields = {}
    for name, vector in target.items():
        control = np.stack([donor[name] for donor in donors]).mean(0)
        fields[f"{name}_attack"] = vector
        fields[f"{name}_control"] = control
        fields[f"{name}_residual"] = vector - control
    if "pre" in target:
        fields["change_attack"] = fields["post_attack"] - fields["pre_attack"]
        fields["change_control"] = fields["post_control"] - fields["pre_control"]
        fields["did"] = fields["change_attack"] - fields["change_control"]
    if "middle" in target:
        fields["middle_minus_E_residual"] = fields["middle_residual"] - fields["E_residual"]
        fields["X_minus_middle_residual"] = fields["X_residual"] - fields["middle_residual"]
    return fields, donors


def cluster_grid(values: np.ndarray, clusters: list[str], *, seed: int = 20260908, replicates: int = 2000) -> dict:
    """Episode-equal means with family resampling, conditional on frozen donors."""
    values = np.asarray(values, dtype=float)
    if len(values) != len(clusters) or not np.isfinite(values).all():
        raise ValueError("invalid clustered observations")
    groups = defaultdict(list)
    for i, cluster in enumerate(clusters):
        groups[str(cluster)].append(i)
    names = sorted(groups)
    result: dict[str, Any] = {"n": len(values), "clusters": len(names),
                              "cluster_sizes": {c: len(groups[c]) for c in names},
                              "mean": values.mean(0).tolist() if len(values) else None, "ci": None}
    if len(names) > 1:
        sums = np.stack([values[groups[c]].sum(0) for c in names])
        sizes = np.array([len(groups[c]) for c in names])
        picks = np.random.default_rng(seed).integers(0, len(names), (replicates, len(names)))
        denominator = sizes[picks].sum(1).reshape((replicates,) + (1,) * (values.ndim - 1))
        samples = sums[picks].sum(1) / denominator
        result["ci"] = np.quantile(samples, [0.025, 0.975], axis=0).tolist()
    return result


def donor_influence(target_means: list[np.ndarray], donor_means: list[list[np.ndarray]], donor_scenarios: list[list[str]]) -> dict:
    """Delete-one-donor-scenario point ranges, not a confidence interval."""
    scenarios = sorted({s for group in donor_scenarios for s in group})
    readings = []
    for omitted in scenarios:
        residuals = []
        for target, donors, labels in zip(target_means, donor_means, donor_scenarios):
            keep = [value for value, label in zip(donors, labels) if label != omitted]
            if keep:
                residuals.append(target - np.stack(keep).mean(0))
        readings.append({"omitted": omitted, "n": len(residuals),
                         "mean": np.stack(residuals).mean(0).tolist() if residuals else None})
    effective = [r for r in readings if r["mean"] is not None]
    return {"interpretation": "Point influence only; target cohort may change if its sole donor is deleted.",
            "scenarios": len(scenarios), "n_range": [min(r["n"] for r in readings), max(r["n"] for r in readings)] if readings else None,
            "point_min": np.array([r["mean"] for r in effective]).min(0).tolist() if effective else None,
            "point_max": np.array([r["mean"] for r in effective]).max(0).tolist() if effective else None,
            "per_scenario": readings}
