"""Optional 2-D picture: PCA-2 of the whitened routine windows (coloured by surface form)
with the CODE and OTHER-DRIFT windows overlaid.  Diagnostic only."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import torch

from research_v2 import io as rio
from research_v2.scorers.wgm import WGMScorer
from nn_geometry_neighbours import form_label

REPO = Path(__file__).resolve().parents[4]
OUT = REPO / "artifacts" / "agent_v2" / "research_v2" / "zoom_code_blindspot"
LABELS = REPO / "docs" / "research_v2" / "labels" / "product_onset_v1_adjudicated.jsonl"
torch.set_num_threads(32)


def main(band="middle_late", anchor="product"):
    labels = {json.loads(l)["trace_id"]: json.loads(l) for l in LABELS.read_text().splitlines() if l.strip()}
    scorer = WGMScorer(window_width=8, layers=band, metric="g1")
    batches = rio.load_core()
    fig, axes = plt.subplots(1, 2, figsize=(15, 6.5))
    for ax, (batch, traces) in zip(axes, batches.items()):
        routine = [t for t in traces if rio.arm_class(t) in ("clean", "benign")]
        drift = [t for t in traces if rio.arm_class(t) == "drift"]
        state = scorer.fit(routine)
        R, rti, rend = [], [], []
        for ti, t in enumerate(routine):
            ends, win = scorer._windows(t)
            R.append(win); rti.append(torch.full((ends.numel(),), ti)); rend.append(ends)
        R = torch.cat(R).float(); rti = torch.cat(rti).long(); rend = torch.cat(rend)
        Rz = (R - state.mu) / state.sd - state.centre
        forms = [form_label(rio.decode_text(routine[int(rti[g])].token_ids[int(rend[g]) - 7 : int(rend[g]) + 1]))
                 for g in range(rti.numel())]
        cov = (Rz.T @ Rz) / (Rz.shape[0] - 1)
        val, vec = torch.linalg.eigh(cov.double())
        comp = vec[:, torch.argsort(val, descending=True)[:2]].float()
        P = Rz @ comp
        colours = {"prose": "#b9c6d6", "json_tool_call": "#f2b134", "json_field": "#f7d9a0",
                   "kb_recitation": "#cbd6c0", "list_markdown": "#d9c2e0", "sign_off": "#c9c9c9",
                   "ids_dates_numbers": "#a9d6d6"}
        for f, c in colours.items():
            m = torch.tensor([x == f for x in forms])
            ax.scatter(P[m, 0], P[m, 1], s=3, c=c, alpha=0.5, label=f"routine {f}", linewidths=0)
        for t in drift:
            lab = labels[t.trace_id]
            onset = int(lab[f"{anchor}_onset"])
            ends, win = scorer._windows(t)
            stop = min(onset + 48, t.token_count)
            sel = torch.nonzero((ends - 7 >= onset) & (ends <= stop - 1)).flatten()
            if not sel.numel():
                continue
            Q = ((win[sel].float() - state.mu) / state.sd - state.centre) @ comp
            if lab["domain"] == "programming":
                ax.scatter(Q[:, 0], Q[:, 1], s=16, c="#c0392b", marker="x", linewidths=1.1)
            elif lab["product_class"].startswith("P3"):
                ax.scatter(Q[:, 0], Q[:, 1], s=16, c="#2980b9", marker="+", linewidths=1.1)
            else:
                ax.scatter(Q[:, 0], Q[:, 1], s=8, c="#5b5b5b", marker=".", alpha=0.55, linewidths=0)
        ax.set_title(f"{batch}: routine (colour = surface form), x = CODE, + = P3 tool-call, . = other drift")
        ax.set_xlabel("PC1 (whitened w=8 selection-rate, layers 5-15)")
        ax.set_ylabel("PC2")
    axes[0].legend(markerscale=4, fontsize=7, loc="upper left")
    fig.tight_layout()
    fig.savefig(OUT / f"pca2_{band}_{anchor}.png", dpi=130)
    print("wrote", OUT / f"pca2_{band}_{anchor}.png")


if __name__ == "__main__":
    import sys
    main(*(sys.argv[1:] or []))
