"""Power / attainability of the preregistered P1 rule (LENS = statistics). Read-only."""
from __future__ import annotations

import sys
from math import comb
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit_statistics_lib as L  # noqa: E402

LEGS = {
    "LEG1 b2/D matched": (1, 3, 42, 3),
    "LEG1 b2/D nominal": (1, 4, 42, 3),
    "LEG2 b1/D matched": (0, 0, 31, 3),
    "LEG2 b1/D nominal": (0, 2, 31, 3),
    "LEG3 h384/C1 matched": (0, 1, 45, 4),
    "LEG3 h384/C1 nominal": (2, 2, 45, 4),
    "LEG3 h384/D matched": (0, 2, 45, 4),
}


def min_net_for_significance(alpha=0.05, max_n=40):
    best = None
    for n in range(1, max_n + 1):
        for a in range(n + 1):
            b = n - a
            if a > b and L.mcnemar(a, b) < alpha:
                if best is None or a - b < best[0]:
                    best = (a - b, a, b, L.mcnemar(a, b))
    return best


def rule_power(n_pairs, pi_only_a, pi_only_b, need_net, alpha=0.05):
    """P(net >= need_net AND exact two-sided McNemar p < alpha) under a trinomial model."""
    total = 0.0
    for a in range(n_pairs + 1):
        for b in range(n_pairs - a + 1):
            pr = (comb(n_pairs, a) * comb(n_pairs - a, b)
                  * pi_only_a ** a * pi_only_b ** b * (1 - pi_only_a - pi_only_b) ** (n_pairs - a - b))
            if pr < 1e-15:
                continue
            if a - b >= need_net and L.mcnemar(a, b) < alpha:
                total += pr
    return total


if __name__ == "__main__":
    print("minimum net gain compatible with exact two-sided McNemar p < 0.05:", min_net_for_significance())
    print()
    for name, (a, b, n, need) in LEGS.items():
        d = a + b
        print(f"{name:22s} discordant={d:2d} best-case p if all favoured TRM-3 = {L.mcnemar(d, 0):.4f} "
              f"-> {'reachable' if L.mcnemar(d, 0) < 0.05 else 'UNREACHABLE'}; "
              f"conditional power at alpha=0.05, pi_a=0.9: {L.mcnemar_power(d, 0.9):.3f}")
    print()
    print("prospective power under the prereg's own predicted effect (B-M 0.26 -> TRM-3 0.37, delta +0.11)")
    for label, pa, pb in (("perfect coupling", 0.11, 0.0), ("mild discord", 0.14, 0.03),
                          ("observed-style discord", 0.16, 0.05)):
        p1 = rule_power(42, pa, pb, 3)
        p2 = rule_power(31, pa, pb, 3)
        p3 = rule_power(45, pa, pb, 4)
        print(f"  {label:24s} LEG1 {p1:.3f}  LEG2 {p2:.3f}  LEG3 {p3:.3f}  joint(indep) {p1*p2*p3:.4f}")
