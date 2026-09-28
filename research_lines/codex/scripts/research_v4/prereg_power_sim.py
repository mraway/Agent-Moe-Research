#!/usr/bin/env python3
"""Reproduce the preregistration's power tables (sections 8.2 / 8.4) for the ACTUAL rule.

Freeze review, blocking-adjacent item POWER SIMULATOR.  The statistics lens showed that
section 8.2's table is the power of the TWO-CONDITION rule while section 1.2 also carried a
point-estimate gate ``Delta_hat >= 0.15``; the lead has ruled that **H1 has no
point-estimate gate** -- ``Delta = 0.15`` is the powered ALTERNATIVE, not an observed
threshold.  The decision rule this script powers is therefore exactly

    cluster-bootstrap 95% percentile CI lower bound > 0   AND   exact McNemar p < 0.05

with the CI clustered on ``attack_family_id`` and McNemar two-sided on the discordant pairs
-- the same two statistics ``trm3_g.cluster_bootstrap_paired`` computes on real data.

Two things the prereg's own simulation got wrong are fixed here:

* **family sizes are UNEQUAL.**  ``configs/dataset_g/g_dev.json`` gives 8 attack families
  19 attack-bearing episodes each and 8 families 14 each (264 in total, not 16 x 16.5); the
  positives of a run of size N are allocated to families in those proportions, largest
  remainder, so the bootstrap resamples the clustering the data actually has.
* **the D5 denominator is the attack-BEARING episode count**, so the reachable ceiling is
  264, not 352.

Model (unchanged from prereg 8.2 otherwise): per episode the pair (S, P) is concordant with
probability ``1 - psi`` and otherwise discordant, with ``P(S hits, P misses)`` chosen so the
expected paired difference is ``Delta``; within-family correlation is the exchangeable
"copy the family prototype with probability rho" model.

Outputs ``power_sim.json`` and ``power_sim.md`` under
``artifacts/agent_v2/dataset_g/prereg_power/``.  Reads no data of any kind -- only the
subset config -- so it runs while every batch is sealed.

Usage::

    python scripts/research_v4/prereg_power_sim.py
    python scripts/research_v4/prereg_power_sim.py --replicates 2000 --bootstrap 800
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any, Sequence

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from research_v2 import io_g  # noqa: E402

DEFAULT_CONFIG = ROOT / "configs" / "dataset_g" / "g_dev.json"
DEFAULT_OUTPUT = ROOT / "artifacts" / "agent_v2" / "dataset_g" / "prereg_power"

#: prereg 8.2: the discordance rate of the two statistics on one episode
PSI = 0.25
#: prereg 8.2 / 8.4 alternatives; 0.0 is the null cell of section 8.3
DELTAS = (0.0, 0.10, 0.125, 0.15, 0.20)
#: within-family correlation; the lead's list for this run
RHOS = (0.15, 0.30)
#: N = A-type E positives reaching the paired comparison.  150 = data gate D1's threshold,
#: 177 = design 15.1's expectation (264 x 0.67), 264 = the reachable ceiling (every
#: attack-BEARING episode is an E positive), 107 = the G-conf cell of prereg 8.4.
DEFAULT_NS = (107, 150, 177, 264)
#: fixed everywhere: this script must be reproducible bit for bit
SEED = 20260907
#: the decision rule
CI_LEVEL = 0.95
MCNEMAR_ALPHA = 0.05

CONCORDANT, ONLY_A, ONLY_B = 0, 1, 2


# ---------------------------------------------------------------------------
# the clustering the data actually has
# ---------------------------------------------------------------------------


def attack_family_sizes(config_path: Path | str = DEFAULT_CONFIG) -> dict[str, int]:
    """``{attack_family_id: attack-BEARING episodes}`` from a subset config.

    An attack trace contributes one episode per user turn, but a ``multi_turn_user``
    injection arrives in the SECOND turn, so ``episode_index == 0`` of those traces carries
    no attack content and is not a candidate positive (the D5 denominator fix).  G-dev:
    8 families x 19 + 8 families x 14 = 264.
    """

    payload = json.loads(Path(config_path).read_text(encoding="utf-8"))
    sizes: dict[str, int] = {}
    for scenario in payload.get("scenarios", ()) or ():
        factory = scenario.get("factory") or {}
        if "attack" not in (factory.get("collected_arms") or ()):
            continue
        arm = (scenario.get("arms") or {}).get("attack") or {}
        family = str(arm.get("attack_family_id") or factory.get("attack_family") or "")
        if not family:
            continue
        turns = 2 if str(arm.get("channel", "")) == io_g.MULTI_TURN_CHANNEL else 1
        # every turn but the pre-injection one of a multi_turn trace bears the attack
        sizes[family] = sizes.get(family, 0) + (turns - 1 if turns == 2 else 1)
    return dict(sorted(sizes.items()))


def allocate(total: int, weights: Sequence[int]) -> list[int]:
    """Split ``total`` across ``weights`` proportionally, largest remainder, all >= 1."""

    if total < len(weights):
        raise ValueError(f"cannot give {len(weights)} families at least one of {total}")
    mass = float(sum(weights))
    exact = [total * w / mass for w in weights]
    out = [max(1, int(math.floor(v))) for v in exact]
    short = total - sum(out)
    order = sorted(range(len(weights)), key=lambda i: (-(exact[i] - math.floor(exact[i])), i))
    index = 0
    while short > 0:
        out[order[index % len(order)]] += 1
        short -= 1
        index += 1
    while short < 0:
        candidate = order[index % len(order)]
        if out[candidate] > 1:
            out[candidate] -= 1
            short += 1
        index += 1
    return out


# ---------------------------------------------------------------------------
# the two statistics of the decision rule
# ---------------------------------------------------------------------------


def mcnemar_p(only_a: int, only_b: int) -> float:
    """Exact two-sided binomial p on the discordant pairs; same as ``trm3.paired_mcnemar``."""

    n = int(only_a) + int(only_b)
    if n == 0:
        return 1.0
    smaller = min(int(only_a), int(only_b))
    tail = sum(math.comb(n, i) for i in range(smaller + 1)) / (2.0**n)
    return min(1.0, 2.0 * tail)


def bootstrap_ci_lower(
    per_family_diff: np.ndarray,
    per_family_size: np.ndarray,
    *,
    replicates: int,
    rng: np.random.Generator,
    level: float = CI_LEVEL,
) -> np.ndarray:
    """Lower percentile bound of the family-clustered bootstrap, vectorised over runs.

    ``per_family_diff[r, f]`` is ``(#only_a - #only_b)`` in family ``f`` of run ``r`` and
    ``per_family_size[r, f]`` its episode count.  Resampling families with replacement and
    recomputing ``Delta = sum(diff) / sum(size)`` is exactly
    ``trm3_g.cluster_bootstrap_paired`` -- which draws ``len(families)`` families and pools
    their episodes -- so the interval this powers is the interval the harness reports.
    """

    runs, families = per_family_diff.shape
    picks = rng.integers(0, families, size=(runs, replicates, families))
    rows = np.arange(runs)[:, None, None]
    diff = per_family_diff[rows, picks].sum(axis=2)
    size = per_family_size[rows, picks].sum(axis=2)
    draws = np.where(size > 0, diff / np.maximum(size, 1), 0.0)
    draws.sort(axis=1)
    lower_q = (1.0 - float(level)) / 2.0
    index = max(0, math.floor(lower_q * replicates))
    return draws[:, index]


def simulate_cell(
    *,
    n: int,
    delta: float,
    rho: float,
    family_sizes: Sequence[int],
    replicates: int,
    bootstrap: int,
    psi: float = PSI,
    seed: int = SEED,
    chunk: int = 100,
) -> dict[str, Any]:
    """Rejection rate of the two-condition rule in one (N, Delta, rho) cell."""

    counts = allocate(int(n), list(family_sizes))
    family_of = np.repeat(np.arange(len(counts)), counts)
    indicator = np.zeros((len(family_of), len(counts)), dtype=np.float64)
    indicator[np.arange(len(family_of)), family_of] = 1.0
    q = 0.5 * (1.0 + (float(delta) / float(psi)))
    if not 0.0 <= q <= 1.0:
        raise ValueError(f"delta={delta} is not attainable at psi={psi}")
    probabilities = np.array([1.0 - psi, psi * q, psi * (1.0 - q)])
    rng = np.random.default_rng(int(seed))

    rejects = ci_only = mcnemar_only = mcnemar_one_sided = 0
    point_estimates: list[float] = []
    discordant_total = 0
    done = 0
    while done < replicates:
        size = min(int(chunk), replicates - done)
        proto = rng.choice(3, size=(size, len(counts)), p=probabilities)
        own = rng.choice(3, size=(size, len(family_of)), p=probabilities)
        copied = rng.random((size, len(family_of))) < float(rho)
        outcome = np.where(copied, proto[:, family_of], own)
        only_a = (outcome == ONLY_A).astype(np.float64)
        only_b = (outcome == ONLY_B).astype(np.float64)
        a_by_family = only_a @ indicator
        b_by_family = only_b @ indicator
        sizes = np.tile(np.asarray(counts, dtype=np.float64), (size, 1))
        lower = bootstrap_ci_lower(
            a_by_family - b_by_family, sizes, replicates=bootstrap, rng=rng
        )
        a_total = only_a.sum(axis=1).astype(int)
        b_total = only_b.sum(axis=1).astype(int)
        p_values = np.array([mcnemar_p(int(a), int(b)) for a, b in zip(a_total, b_total)])
        ci_pass = lower > 0.0
        mc_pass = p_values < MCNEMAR_ALPHA
        rejects += int(np.sum(ci_pass & mc_pass))
        ci_only += int(np.sum(ci_pass))
        mcnemar_only += int(np.sum(mc_pass))
        # prereg 8.3 reports the ONE-SIDED rate (nominal 0.025); the exact two-sided test
        # restricted to the S > P direction is that same quantity, and is what the
        # conjunction's CI condition already restricts to.
        mcnemar_one_sided += int(np.sum(mc_pass & (a_total > b_total)))
        point_estimates.extend(((a_total - b_total) / float(n)).tolist())
        discordant_total += int(np.sum(a_total + b_total))
        done += size
    return {
        "n": int(n),
        "delta": float(delta),
        "rho": float(rho),
        "psi": float(psi),
        "families": len(counts),
        "family_sizes": [int(v) for v in counts],
        "replicates": int(replicates),
        "bootstrap_replicates": int(bootstrap),
        "power": rejects / replicates,
        "power_ci_only": ci_only / replicates,
        "power_mcnemar_only": mcnemar_only / replicates,
        "power_mcnemar_one_sided": mcnemar_one_sided / replicates,
        "mean_point_estimate": float(np.mean(point_estimates)),
        "mean_discordant_pairs": discordant_total / replicates,
    }


# ---------------------------------------------------------------------------
# the tables
# ---------------------------------------------------------------------------


def run_grid(
    *,
    family_sizes: Sequence[int],
    ns: Sequence[int],
    deltas: Sequence[float] = DELTAS,
    rhos: Sequence[float] = RHOS,
    psis: Sequence[float] = (PSI,),
    replicates: int,
    bootstrap: int,
    seed: int = SEED,
) -> list[dict[str, Any]]:
    """The full (psi, N, rho, Delta) grid.

    v3.2 design note 5.2 / change list item 8: ``psi`` used to be the module constant
    ``PSI = 0.25``, but on the X anchor the two statistics disagree on at least 35/125 =
    0.28 of the positives, so the real discordance rate is about 0.30-0.35 and the
    committed MDE table was an optimistic lower bound.  ``psi`` is therefore a grid axis;
    ``offset_psi = 0`` keeps the seed of every pre-existing cell unchanged, so the
    ``psi = 0.25`` table reproduces bit for bit.
    """

    rows: list[dict[str, Any]] = []
    for offset_psi, psi in enumerate(psis):
        for offset_n, n in enumerate(ns):
            for offset_rho, rho in enumerate(rhos):
                for offset_delta, delta in enumerate(deltas):
                    rows.append(
                        simulate_cell(
                            n=int(n),
                            delta=float(delta),
                            rho=float(rho),
                            psi=float(psi),
                            family_sizes=family_sizes,
                            replicates=replicates,
                            bootstrap=bootstrap,
                            # a distinct but FIXED stream per cell
                            seed=(
                                seed
                                + 100003 * offset_psi
                                + 1009 * offset_n
                                + 101 * offset_rho
                                + offset_delta
                            ),
                        )
                    )
    return rows


def markdown_table(rows: Sequence[dict[str, Any]], deltas: Sequence[float]) -> str:
    psis = sorted({row["psi"] for row in rows})
    multi = len(psis) > 1
    header = (
        ("| psi | N | rho | " if multi else "| N | rho | ")
        + " | ".join(f"Delta = {d:g}" for d in deltas)
        + " |"
    )
    rule = ("|---:|---:|---:|" if multi else "|---:|---:|") + "---:|" * len(deltas)
    lines = [header, rule]
    for psi in psis:
        for n in sorted({row["n"] for row in rows}):
            for rho in sorted({row["rho"] for row in rows}):
                cells = []
                for delta in deltas:
                    match = [
                        row
                        for row in rows
                        if row["n"] == n
                        and row["rho"] == rho
                        and row["delta"] == delta
                        and row["psi"] == psi
                    ]
                    cells.append(f"{match[0]['power']:.3f}" if match else "--")
                head = f"| {psi:g} | {n} | {rho:g} | " if multi else f"| {n} | {rho:g} | "
                lines.append(head + " | ".join(cells) + " |")
    return "\n".join(lines)


def null_table(rows: Sequence[dict[str, Any]]) -> str:
    multi = len({row["psi"] for row in rows}) > 1
    lines = [
        ("| psi | N | rho | " if multi else "| N | rho | ")
        + "McNemar two-sided (nom. 0.05) | McNemar one-sided (nom. 0.025) | "
        "CI lower > 0 alone | **conjunction = the rule** |",
        ("|---:|---:|---:|---:|---:|---:|---:|" if multi else "|---:|---:|---:|---:|---:|---:|"),
    ]
    for row in sorted(rows, key=lambda r: (r["psi"], r["n"], r["rho"])):
        if row["delta"] != 0.0:
            continue
        head = (
            f"| {row['psi']:g} | {row['n']} | {row['rho']:g} | "
            if multi
            else f"| {row['n']} | {row['rho']:g} | "
        )
        lines.append(
            head
            + f"{row['power_mcnemar_only']:.3f} | "
            f"{row['power_mcnemar_one_sided']:.3f} | {row['power_ci_only']:.3f} | "
            f"**{row['power']:.3f}** |"
        )
    return "\n".join(lines)


def mde_table(rows: Sequence[dict[str, Any]], target: float = 0.80) -> str:
    """Linear interpolation of the Delta at which the rule reaches ``target`` power."""

    psis = sorted({row["psi"] for row in rows})
    multi = len(psis) > 1
    lines = [
        ("| psi | N | rho | " if multi else "| N | rho | ")
        + f"Delta at power {target:g} (interpolated) |",
        ("|---:|---:|---:|---:|" if multi else "|---:|---:|---:|"),
    ]
    for psi in psis:
        for n in sorted({r["n"] for r in rows}):
            for rho in sorted({r["rho"] for r in rows}):
                grid = sorted(
                    (r["delta"], r["power"])
                    for r in rows
                    if r["n"] == n
                    and r["rho"] == rho
                    and r["psi"] == psi
                    and r["delta"] > 0
                )
                mde = None
                for (d0, p0), (d1, p1) in zip(grid, grid[1:]):
                    if p0 < target <= p1 and p1 > p0:
                        mde = d0 + (d1 - d0) * (target - p0) / (p1 - p0)
                        break
                if mde is None and grid and grid[0][1] >= target:
                    mde = grid[0][0]
                head = f"| {psi:g} | {n} | {rho:g} | " if multi else f"| {n} | {rho:g} | "
                lines.append(head + ("--" if mde is None else f"{mde:.3f}") + " |")
    return "\n".join(lines)


def report(payload: dict[str, Any]) -> str:
    rows = payload["cells"]
    deltas = [d for d in payload["deltas"] if d > 0]
    sizes = payload["attack_family_sizes"]
    return f"""# Preregistration power simulation (sections 8.2 / 8.4), recomputed

Generated by `scripts/research_v4/prereg_power_sim.py` on {payload["created_at"]}.
No data was read: the clustering comes from `{payload["config"]}` alone.

**The rule this powers** (lead ruling: H1 has NO point-estimate gate; Delta = 0.15 is the
powered alternative, not an observed threshold):

> cluster-bootstrap {int(payload["ci_level"] * 100)}% percentile CI lower bound > 0
> **and** exact McNemar two-sided p < {payload["mcnemar_alpha"]}

**Clustering**: {payload["family_count"]} attack families with **unequal** attack-bearing
episode counts {sorted(set(sizes.values()), reverse=True)} (8 x 19 + 8 x 14 = {sum(sizes.values())}),
not the equal split section 8.2 assumed.  psi = {payload["psis"]},
{payload["replicates"]} replicates x {payload["bootstrap_replicates"]} bootstrap draws per cell,
seed {payload["seed"]}.

## Power

{markdown_table(rows, deltas)}

## Smallest detectable effect (80% power)

{mde_table(rows)}

## Null (Delta = 0): false-positive rate of each rule under the same clustering

{null_table(rows)}

The conjunction is directional (a CI lower bound above 0 fixes the sign), so its column is
directly comparable to the prereg's one-sided 0.025.  It stays in a narrow band across every
cell, while the exact McNemar ALONE degrades badly as the family correlation and the sample
grow -- it treats the episodes of one injection text as independent observations -- and the
16-cluster bootstrap alone is mildly anti-conservative on its own.  Requiring both is what
holds the rate down, and that conclusion survives the move from equal to unequal families.

## Reading

* N grid actually run: {payload["ns"]}.  In the v3.1 grid N = 150 is data gate D1's
  threshold, N = 177 design 15.1's expectation (264 attack-BEARING episodes x 0.67),
  N = 264 the reachable ceiling and N = 107 the G-conf cell of section 8.4; in the v3.2
  X-anchored grid (design note 4.2) N = 62-80 is the projected G-conf yield and N = 126 the
  G-dev readout.
* psi grid actually run: {payload["psis"]}.  v3.2 design note 5.2: on the X anchor the two
  statistics disagree on at least 0.28 of the positives, so psi = 0.25 understates the
  discordance and every MDE computed at it is an optimistic lower bound.
* The 80% MDE of the rule is read off the table by interpolation; see
  `docs/research_v4/freeze_review_code_fixes.md`.
* `power_ci_only` and `power_mcnemar_only` are each condition on its own, so the cost of
  the conjunction is visible per cell in `power_sim.json`.
"""


def _args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    # prereg 8.2 allows 1500-4000 replicates x 800-1000 bootstrap; the defaults sit at
    # the top of that range so the bare command reproduces the committed artefact.
    parser.add_argument("--replicates", type=int, default=4000)
    parser.add_argument("--bootstrap", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument(
        "--n", type=int, action="append", default=None, help="repeatable; default 107/150/177/264"
    )
    parser.add_argument("--rho", type=float, action="append", default=None)
    parser.add_argument("--delta", type=float, action="append", default=None)
    parser.add_argument(
        "--psi",
        type=float,
        action="append",
        default=None,
        help="repeatable discordance rate (v3.2 design note 5.2 / change list item 8: on "
        f"the X anchor the real psi is about 0.30-0.35, not the {PSI} this used to hard-"
        "code as a module constant).  A cell with Delta > psi is unattainable and raises",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _args(argv)
    if int(args.replicates) < 2000:
        raise SystemExit("--replicates must be at least 2000 (freeze review item 6)")
    sizes = attack_family_sizes(args.config)
    if not sizes:
        raise SystemExit(f"{args.config} declares no attack families")
    ns = tuple(args.n or DEFAULT_NS)
    rhos = tuple(args.rho or RHOS)
    deltas = tuple(args.delta or DELTAS)
    psis = tuple(args.psi or (PSI,))
    unattainable = [
        (psi, delta) for psi in psis for delta in deltas if delta > psi
    ]
    if unattainable:
        raise SystemExit(
            "these (psi, Delta) cells are unattainable -- the paired difference cannot "
            f"exceed the discordance rate: {unattainable}"
        )
    started = time.time()
    rows = run_grid(
        family_sizes=list(sizes.values()),
        ns=ns,
        deltas=deltas,
        rhos=rhos,
        psis=psis,
        replicates=int(args.replicates),
        bootstrap=int(args.bootstrap),
        seed=int(args.seed),
    )
    payload = {
        "kind": "research_v4_prereg_power_sim",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "config": str(args.config),
        "attack_family_sizes": sizes,
        "family_count": len(sizes),
        "attack_bearing_episodes": sum(sizes.values()),
        "rule": (
            "cluster-bootstrap 95% percentile CI lower bound > 0 AND exact McNemar "
            "two-sided p < 0.05; there is NO point-estimate gate (lead ruling)"
        ),
        "psi": psis[0] if len(psis) == 1 else list(psis),
        "psis": list(psis),
        "ci_level": CI_LEVEL,
        "mcnemar_alpha": MCNEMAR_ALPHA,
        "ns": list(ns),
        "rhos": list(rhos),
        "deltas": list(deltas),
        "replicates": int(args.replicates),
        "bootstrap_replicates": int(args.bootstrap),
        "seed": int(args.seed),
        "elapsed_seconds": None,
        "cells": rows,
    }
    payload["elapsed_seconds"] = round(time.time() - started, 1)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "power_sim.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )
    text = report(payload)
    (out_dir / "power_sim.md").write_text(text, encoding="utf-8")
    print(text)
    print(f"wrote {out_dir / 'power_sim.json'} and {out_dir / 'power_sim.md'}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
