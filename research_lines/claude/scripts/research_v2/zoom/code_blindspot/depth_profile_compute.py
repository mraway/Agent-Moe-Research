"""LENS: full-depth (layers 0-15) router-probability profile of the code blind spot.

Diagnostic only, post-hoc, on development batches B1/B2.  Nothing here is a detector:
the routine reference is fit on a *fit half* of routine traces and every evaluation
number is reported on a held-out half, but the layer/subset search at the end uses
labels and is explicitly a diagnostic (see the report).

Outputs a single JSON cache under
artifacts/agent_v2/research_v2/zoom_code_blindspot/depth_profile.json
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
import torch

from research_v2 import io as rio

REPO = Path(__file__).resolve().parents[4]
OUT = REPO / "artifacts" / "agent_v2" / "research_v2" / "zoom_code_blindspot" / "depth_profile"
LABELS = REPO / "docs" / "research_v2" / "labels" / "product_onset_v1_adjudicated.jsonl"
LAYERS = 16
EXPERTS = 64
WIN = 48          # code / other-drift window length: onset .. min(onset+48, T)
NULL_WIN = 48     # routine window length for the window-level null
NULL_STRIDE = 8
EPS = 1e-12


def load_labels() -> dict[str, dict]:
    rows = {}
    for line in LABELS.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        rows[r["trace_id"]] = r
    return rows


def js_div(p: torch.Tensor, q: torch.Tensor) -> torch.Tensor:
    """Jensen-Shannon divergence in bits, p [.., 64] vs q [.., 64] (broadcast)."""
    m = 0.5 * (p + q)
    lm = torch.log2(m + EPS)
    kl_p = (p * (torch.log2(p + EPS) - lm)).sum(-1)
    kl_q = (q * (torch.log2(q + EPS) - lm)).sum(-1)
    return 0.5 * kl_p + 0.5 * kl_q


def token_metrics(probs: torch.Tensor, topk: torch.Tensor,
                  ref_p: torch.Tensor, ref_mask: torch.Tensor) -> dict[str, np.ndarray]:
    """probs [16,T,64] float, topk [16,T,8] long -> dict of [16,T] float32 arrays."""
    p = probs.float()
    js = js_div(p, ref_p.unsqueeze(1))                       # [16,T]
    ent = -(p * torch.log2(p + EPS)).sum(-1)                 # [16,T]
    top1 = p.max(-1).values                                  # [16,T]
    inter = torch.gather(ref_mask.unsqueeze(1).expand(-1, topk.shape[1], -1), 2, topk)
    c = inter.sum(-1).float()                                # [16,T] intersection size
    jac = c / (16.0 - c)                                     # |A n B| / |A u B|, |A|=|B|=8
    rmass = (p * ref_mask.unsqueeze(1).float()).sum(-1)      # prob mass on the routine top-8
    return {"js": js.numpy(), "ent": ent.numpy(), "top1": top1.numpy(),
            "jac": jac.numpy(), "rmass": rmass.numpy()}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    labels = load_labels()
    batches = rio.load_core()
    traces = [t for b in ("b1", "b2") for t in batches[b]]

    routine = [t for t in traces if rio.arm_class(t) in ("clean", "benign")]
    routine.sort(key=lambda t: t.trace_id)
    # deterministic stratified halves: alternate inside (batch, arm)
    fit, held = [], []
    per_group = defaultdict(list)
    for t in routine:
        per_group[(t.batch, t.arm)].append(t)
    for key in sorted(per_group):
        for i, t in enumerate(per_group[key]):
            (fit if i % 2 == 0 else held).append(t)

    # ---- reference from the fit half ------------------------------------------------
    acc = torch.zeros(LAYERS, EXPERTS, dtype=torch.float64)
    sel = torch.zeros(LAYERS, EXPERTS, dtype=torch.float64)
    n_tok = 0
    fit_windows: list[np.ndarray] = []
    fit_sel_windows: list[np.ndarray] = []
    fit_window_meta: list[dict] = []
    for t in fit:
        p = t.probabilities().float()
        oh_t = torch.zeros(LAYERS, p.shape[1], EXPERTS)
        oh_t.scatter_(2, t.top_k_ids, 1.0)
        for s0 in range(0, max(1, p.shape[1] - NULL_WIN + 1), NULL_STRIDE):
            e0 = min(s0 + NULL_WIN, p.shape[1])
            if e0 - s0 < 8:
                continue
            fit_windows.append(p[:, s0:e0, :].mean(1).numpy())
            fit_sel_windows.append(oh_t[:, s0:e0, :].mean(1).numpy())
            fit_window_meta.append({"trace_id": t.trace_id, "start": s0, "end": e0})
        acc += p.sum(1).double()
        oh = torch.zeros(LAYERS, p.shape[1], EXPERTS)
        oh.scatter_(2, t.top_k_ids, 1.0)
        sel += oh.sum(1).double()
        n_tok += p.shape[1]
    ref_p = (acc / n_tok).float()                             # [16,64] token-mean router dist
    ref_rank = sel.argsort(dim=1, descending=True)            # [16,64]
    ref_mask = torch.zeros(LAYERS, EXPERTS, dtype=torch.long)
    ref_mask.scatter_(1, ref_rank[:, :8], 1)
    ref_mask_b = ref_mask.bool().long()

    # ---- groups ---------------------------------------------------------------------
    drift = [t for t in traces if rio.arm_class(t) == "drift"]
    prog_ids = {"b1-f2-012", "b1-f2-014", "b1-f3-016",
                "b2-f2-011", "b2-f2-012", "b2-f2-014", "b2-f2-015", "b2-f3-020"}

    def short(t):
        return "-".join(t.trace_id.split("-")[:3])

    out = {
        "n_fit_routine": len(fit), "n_held_routine": len(held), "n_fit_tokens": n_tok,
        "ref_top8": ref_mask.nonzero().reshape(-1, 2)[:, 1].reshape(LAYERS, 8).tolist(),
        "ref_top8_mass": [float(ref_p[l][ref_mask[l].bool()].sum()) for l in range(LAYERS)],
        "ref_entropy": [float(-(ref_p[l] * torch.log2(ref_p[l] + EPS)).sum()) for l in range(LAYERS)],
        "fit_ids": [t.trace_id for t in fit], "held_ids": [t.trace_id for t in held],
    }

    metrics = ("js", "ent", "top1", "jac", "rmass")
    # token pools: [16, N] per metric
    pools: dict[str, dict[str, list[np.ndarray]]] = defaultdict(lambda: defaultdict(list))
    # window medians: group -> metric -> list of [16] arrays ; plus per-trace records
    win: dict[str, dict[str, list[np.ndarray]]] = defaultdict(lambda: defaultdict(list))
    win_meta: dict[str, list[dict]] = defaultdict(list)
    win_prob: dict[str, list[np.ndarray]] = defaultdict(list)   # window-mean router probs [16,64]
    win_prob["routine_fit"] = fit_windows
    win_sel: dict[str, list[np.ndarray]] = defaultdict(list)   # window top-8 selection rates
    win_sel["routine_fit"] = fit_sel_windows

    def sel_rates(trace):
        oh = torch.zeros(LAYERS, trace.token_count, EXPERTS)
        oh.scatter_(2, trace.top_k_ids, 1.0)
        return oh
    per_trace: list[dict] = []

    mean_probs: dict[str, list[torch.Tensor]] = defaultdict(list)   # per-group summed probs
    mean_counts: dict[str, int] = defaultdict(int)

    # held-out routine: all tokens + sliding windows
    for t in held:
        pr = t.probabilities().float()
        oh = sel_rates(t)
        m = token_metrics(pr, t.top_k_ids, ref_p, ref_mask_b)
        T = t.token_count
        mean_probs["routine_held"].append(pr.sum(1).double())
        mean_counts["routine_held"] += T
        for k in metrics:
            pools["routine"][k].append(m[k])
        for s in range(0, max(1, T - NULL_WIN + 1), NULL_STRIDE):
            e = min(s + NULL_WIN, T)
            if e - s < 8:
                continue
            for k in metrics:
                win["routine"][k].append(np.median(m[k][:, s:e], axis=1))
            win_meta["routine"].append({"trace_id": t.trace_id, "start": s, "end": e})
            win_prob["routine"].append(pr[:, s:e, :].mean(1).numpy())
            win_sel["routine"].append(oh[:, s:e, :].mean(1).numpy())
        rec = {"trace_id": t.trace_id, "group": "routine", "domain": "routine",
               "n_tokens": T, "start": 0, "end": T}
        for k in metrics:
            rec[f"{k}_median"] = np.median(m[k], axis=1).tolist()
        per_trace.append(rec)

    # drift windows, both anchors
    for t in drift:
        lab = labels.get(t.trace_id)
        if lab is None:
            raise SystemExit(f"no label row for {t.trace_id}")
        dom = lab["domain"]
        grp = "code" if short(t) in prog_ids else f"other:{dom}"
        pr = t.probabilities().float()
        oh = sel_rates(t)
        m = token_metrics(pr, t.top_k_ids, ref_p, ref_mask_b)
        T = t.token_count
        for anchor_name, onset in (("product", int(lab["product_onset"])),
                                   ("evidence", int(lab["evidence_onset"]))):
            s = max(0, min(onset, T - 1))
            e = min(s + WIN, T)
            key = grp if anchor_name == "product" else f"{grp}@ev"
            for k in metrics:
                win[key][k].append(np.median(m[k][:, s:e], axis=1))
            win_meta[key].append({"trace_id": t.trace_id, "start": s, "end": e})
            win_prob[key].append(pr[:, s:e, :].mean(1).numpy())
            win_sel[key].append(oh[:, s:e, :].mean(1).numpy())
            if anchor_name == "product":
                mean_probs[grp].append(pr[:, s:e, :].sum(1).double())
                mean_counts[grp] += (e - s)
                mean_probs["code_all" if grp == "code" else "other_all"].append(
                    pr[:, s:e, :].sum(1).double())
                mean_counts["code_all" if grp == "code" else "other_all"] += (e - s)
                mean_probs[f"trace::{t.trace_id}"].append(pr[:, s:e, :].sum(1).double())
                mean_counts[f"trace::{t.trace_id}"] += (e - s)
                for k in metrics:
                    pools["code" if grp == "code" else "other"][k].append(m[k][:, s:e])
                    pools[grp][k].append(m[k][:, s:e])
            rec = {"trace_id": t.trace_id, "group": grp, "domain": dom, "anchor": anchor_name,
                   "onset": onset, "start": s, "end": e, "n_tokens": T,
                   "product_class": lab.get("product_class"),
                   "workflow": t.workflow, "channel": t.channel}
            for k in metrics:
                sub = m[k][:, s:e]
                rec[f"{k}_median"] = np.median(sub, axis=1).tolist()
                rec[f"{k}_q90"] = np.quantile(sub, 0.9, axis=1).tolist()
            per_trace.append(rec)
        t._probabilities = None

    resist = [t for t in traces if rio.arm_class(t) == "resist"]
    for t in resist:
        pr = t.probabilities().float()
        oh = sel_rates(t)
        m = token_metrics(pr, t.top_k_ids, ref_p, ref_mask_b)
        T = t.token_count
        for s0 in range(0, max(1, T - NULL_WIN + 1), NULL_STRIDE):
            e = min(s0 + NULL_WIN, T)
            if e - s0 < 8:
                continue
            for k in metrics:
                win["resist"][k].append(np.median(m[k][:, s0:e], axis=1))
            win_meta["resist"].append({"trace_id": t.trace_id, "start": s0, "end": e})
            win_prob["resist"].append(pr[:, s0:e, :].mean(1).numpy())
            win_sel["resist"].append(oh[:, s0:e, :].mean(1).numpy())
        t._probabilities = None

    def summarise_tokens(pool):
        res = {}
        for k in metrics:
            arr = np.concatenate(pool[k], axis=1)
            res[k] = {
                "n": int(arr.shape[1]),
                "median": np.median(arr, axis=1).tolist(),
                "q10": np.quantile(arr, 0.10, axis=1).tolist(),
                "q90": np.quantile(arr, 0.90, axis=1).tolist(),
                "mean": arr.mean(axis=1).tolist(),
            }
        return res

    out["token_profile"] = {g: summarise_tokens(p) for g, p in pools.items()}
    out["window_medians"] = {g: {k: np.stack(v).tolist() for k, v in d.items()}
                             for g, d in win.items()}
    win_meta["routine_fit"] = fit_window_meta
    out["window_meta"] = {g: v for g, v in win_meta.items()}
    out["per_trace"] = per_trace
    out["group_mean_probs"] = {
        g: (torch.stack(v).sum(0) / mean_counts[g]).tolist() for g, v in mean_probs.items()}
    out["group_mean_counts"] = dict(mean_counts)
    out["ref_p"] = ref_p.tolist()
    np.savez_compressed(OUT / "window_probs.npz",
                        **{g: np.stack(v).astype(np.float32) for g, v in win_prob.items()})
    np.savez_compressed(OUT / "window_sel.npz",
                        **{g: np.stack(v).astype(np.float32) for g, v in win_sel.items()})
    (OUT / "depth_profile.json").write_text(json.dumps(out), encoding="utf-8")
    print("fit routine traces", len(fit), "held", len(held), "fit tokens", n_tok)
    print("groups:", {g: len(v["js"]) for g, v in win.items()})
    print("wrote", OUT / "depth_profile.json")


if __name__ == "__main__":
    main()
