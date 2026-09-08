"""Step 11: how many routine traces per calibration half are needed for a +/-0.03 stable FAR."""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import OUT  # noqa

TARGET_SD = 0.03 / 1.6449  # ~90% of draws inside +/-0.03 under a normal approximation


def main():
    exp = json.loads((OUT / "step2_experiments.json").read_text(encoding="utf-8"))
    sib = json.loads((OUT / "step7_sibling.json").read_text(encoding="utf-8"))
    out = {"target_sd": TARGET_SD, "fits": {}}
    # the draw at the full half size is degenerate (sd = 0) and is excluded from the fit
    exponents = []
    for key in exp:
        for case in exp[key]["directions"]:
            sub = exp[key]["directions"][case]["subsample"]
            half_n = exp[key]["directions"][case]["halves"]["0"]["n_cal"]
            pts = [(int(n), sub[n]["pooled"]["far"]["sd"]) for n in sub
                   if "pooled" in sub[n] and int(n) < half_n]
            pts.sort()
            if len(pts) >= 2:
                (n1, s1), (n2, s2) = pts[0], pts[-1]
                a = math.log(s1 / s2) / math.log(n2 / n1)
                exponents.append(a)
            elif len(pts) == 1 and exponents:
                a = sum(exponents) / len(exponents)
                n1, s1 = pts[0]
            else:
                continue
            c = s1 * n1 ** a
            need = (c / TARGET_SD) ** (1.0 / a)
            rho = max(sib[key][case][h]["pearson"] for h in ("0", "1"))
            out["fits"][f"{key}|{case}"] = {
                "points": pts, "exponent_a": a, "constant_c": c,
                "n_per_half_iid": need,
                "max_sibling_pearson_in_this_direction": rho,
                "n_per_half_inflated_by_sibling_correlation": need * (1.0 + max(0.0, rho)),
            }
            print(f"{key} {case}: sd(FAR) points {pts} -> sd ~ {c:.3f} n^-{a:.2f}; "
                  f"n/half for sd<= {TARGET_SD:.4f}: {need:.0f} (iid), "
                  f"{need * (1 + max(0.0, rho)):.0f} with sibling rho={rho:.2f}")
    (OUT / "step8_size_requirement.json").write_text(json.dumps(out, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
