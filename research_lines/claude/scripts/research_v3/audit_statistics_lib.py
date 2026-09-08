"""Read-only statistics helpers for the TRM-3 audit (LENS = statistics).

Nothing here writes to artifacts, labels or frozen sources; every function takes
already-loaded frozen result blocks and recomputes the reported quantity.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

ROOT = Path("/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363")
CELLS = ROOT / "artifacts/agent_v2/research_v3/trm3"


def load(cell: str) -> dict:
    return json.loads((CELLS / cell / "result.json").read_text())


# ---------------------------------------------------------------- Clopper-Pearson
def _log_binom_pmf(k, n, p):
    if p <= 0.0:
        return 0.0 if k == 0 else float("-inf")
    if p >= 1.0:
        return 0.0 if k == n else float("-inf")
    return (
        math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)
        + k * math.log(p) + (n - k) * math.log1p(-p)
    )


def _binom_sf_ge(k, n, p):
    """P(X >= k) for X ~ Bin(n, p) by exact summation in log space."""
    if k <= 0:
        return 1.0
    return sum(math.exp(_log_binom_pmf(i, n, p)) for i in range(k, n + 1))


def _binom_cdf_le(k, n, p):
    if k >= n:
        return 1.0
    return sum(math.exp(_log_binom_pmf(i, n, p)) for i in range(0, k + 1))


def clopper_pearson(k, n, conf=0.95):
    """Exact two-sided Clopper-Pearson interval for k successes out of n.

    Solved by bisection on the monotone exact binomial tails, so the k = 0 and k = n
    edges are handled without an incomplete-beta implementation.
    """
    if n == 0:
        return (float("nan"), float("nan"))
    a = (1.0 - conf) / 2.0
    if k == 0:
        lo = 0.0
    else:
        x, y = 0.0, 1.0
        for _ in range(200):
            m = 0.5 * (x + y)
            if _binom_sf_ge(k, n, m) < a:
                x = m
            else:
                y = m
        lo = 0.5 * (x + y)
    if k == n:
        hi = 1.0
    else:
        x, y = 0.0, 1.0
        for _ in range(200):
            m = 0.5 * (x + y)
            if _binom_cdf_le(k, n, m) > a:
                x = m
            else:
                y = m
        hi = 0.5 * (x + y)
    return (lo, hi)


def fmt_ci(k, n, conf=0.95):
    lo, hi = clopper_pearson(k, n, conf)
    return f"{k}/{n}={k/n:.4f} [{lo:.4f}, {hi:.4f}]"


# ---------------------------------------------------------------- exact McNemar
def mcnemar(only_a: int, only_b: int) -> float:
    n = only_a + only_b
    if n == 0:
        return 1.0
    smaller = min(only_a, only_b)
    tail = sum(math.comb(n, i) for i in range(smaller + 1)) / (2.0 ** n)
    return min(1.0, 2.0 * tail)


def mcnemar_from_hits(hits_a: dict, hits_b: dict) -> dict:
    keys = sorted(set(hits_a) & set(hits_b))
    only_a = sum(1 for k in keys if hits_a[k] and not hits_b[k])
    only_b = sum(1 for k in keys if hits_b[k] and not hits_a[k])
    both = sum(1 for k in keys if hits_a[k] and hits_b[k])
    neither = sum(1 for k in keys if not hits_a[k] and not hits_b[k])
    return {
        "pair_count": len(keys), "both": both, "neither": neither,
        "only_a": only_a, "only_b": only_b, "discordant": only_a + only_b,
        "net": only_a - only_b, "p": mcnemar(only_a, only_b),
        "unpaired": sorted((set(hits_a) | set(hits_b)) - set(keys)),
    }


def mcnemar_power(n_discordant: int, prob_a: float = 0.5, alpha: float = 0.05) -> float:
    """P(reject) of the exact two-sided sign test given the number of discordant pairs.

    ``prob_a`` = P(a discordant pair favours A).  With n fixed, the exact test rejects
    iff the smaller-tail doubled binomial p-value is < alpha.
    """
    n = int(n_discordant)
    if n == 0:
        return 0.0
    reject = [k for k in range(n + 1) if mcnemar(k, n - k) < alpha]
    return sum(math.comb(n, k) * prob_a ** k * (1 - prob_a) ** (n - k) for k in reject)


def min_discordant_for_significance(alpha: float = 0.05) -> int:
    n = 1
    while n < 200:
        if mcnemar(n, 0) < alpha:
            return n
        n += 1
    return -1


# ---------------------------------------------------------------- Holm
def holm(pvals: dict, alpha: float = 0.05, order: list | None = None) -> list:
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m = len(items)
    out, rejected = [], True
    for i, (name, p) in enumerate(items):
        thresh = alpha / (m - i)
        if rejected and p <= thresh:
            decision = "reject"
        else:
            rejected = False
            decision = "retain"
        out.append({"hypothesis": name, "p": p, "threshold": thresh, "decision": decision})
    return out
