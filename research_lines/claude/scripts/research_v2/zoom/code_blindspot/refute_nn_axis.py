"""Is the prose/JSON axis really the routine manifold's dominant axis, and is the
'code projects inside the JSON lobe' position code-specific?  Diagnostic."""
from __future__ import annotations

import json
from pathlib import Path

import torch

from research_v2 import io as rio
from research_v2.features import selection_rate_windows

REPO = Path(__file__).resolve().parents[4]
OUT = REPO / "artifacts" / "agent_v2" / "research_v2" / "zoom_code_blindspot"
LAYERS = tuple(range(5, 16))


def main():
    res = {}
    for batch, traces in rio.load_core().items():
        c = torch.load(OUT / f"refute_nn_middle_late_{batch}.pt", weights_only=False)
        routine = [t for t in traces if rio.arm_class(t) in ("clean", "benign")]
        R = torch.cat([selection_rate_windows(t.top_k_ids, 8, LAYERS)[1] for t in routine]).float()
        mu, sd = R.mean(0), R.std(0) + 1e-3
        Z = (R - mu) / sd
        Zc = Z - Z.mean(0)
        cov = (Zc.T @ Zc) / (Zc.shape[0] - 1)
        ev, evec = torch.linalg.eigh(cov.double())
        order = torch.argsort(ev, descending=True)
        ev = ev[order]; evec = evec[:, order]
        tot = float(ev.sum())
        isj = c["routine_is_json"]
        u = (Z[isj].mean(0) - Z[~isj].mean(0)).double(); u = u / u.norm()
        cos = [round(abs(float(u @ evec[:, k])), 3) for k in range(5)]
        share = [round(float(ev[k] / tot), 4) for k in range(5)]
        proj_u = (Zc.double() @ u)
        # variance share carried by u (as the lens defines it): var(proj)/mean||z-c||^2
        share_u = float(proj_u.var() / (Zc.double() ** 2).sum(1).mean())
        pc1 = Zc.double() @ evec[:, 0]
        res[batch] = {"pc_share": share, "cos_u_pc": cos, "u_var_share": round(share_u, 4),
                      "pc1_var_share_same_metric": round(float(pc1.var() / (Zc.double() ** 2).sum(1).mean()), 4),
                      "dim": int(Z.shape[1]), "n": int(Z.shape[0])}
        print(batch, json.dumps(res[batch]))
    (OUT / "refute_axis.json").write_text(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
