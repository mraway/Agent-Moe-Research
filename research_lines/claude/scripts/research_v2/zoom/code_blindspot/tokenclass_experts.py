"""Third pass: where do code tokens route, and why does whitening lose the offset?

(A) Class-conditional expert-usage profiles in routine (top-8 selection rate over
    layers 5-15) vs the code windows and the other-drift windows: which routine token
    class does code's expert profile look like?
(B) CAND-A geometry decomposition at the frozen width w=8: the raw selection-rate mean
    shift of each region, its norm in z-space (the space CAND-A's g1 lives in), and the
    effective routine standard deviation along that shift.

Post-hoc diagnosis.  Writes JSON only.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import torch

from research_v2 import io as rio
from research_v2.features import selection_rate_windows

REPO = Path(__file__).resolve().parents[4]
OUT = REPO / "artifacts" / "agent_v2" / "research_v2" / "zoom_code_blindspot"
WINDOW = 48
BAND = tuple(range(5, 16))
W8 = 8


def main() -> None:
    meta = json.loads((OUT / "token_cache_meta.json").read_text(encoding="utf-8"))
    cache = np.load(OUT / "token_cache.npz")
    classes = meta["classes"]
    tmeta = {t["trace_id"]: t for t in meta["traces"]}
    tclass_all = cache["tclass"]
    trace_ix_all = cache["trace_ix"]
    pos_all = cache["pos"]

    batches = rio.load_core()
    traces = {t.trace_id: t for b in batches.values() for t in b}

    routine_ids = sorted(
        [tid for tid, t in tmeta.items() if t["arm_class"] in ("clean", "benign")]
    )
    fit_ids = set(routine_ids[0::2])
    held_ids = set(routine_ids[1::2])
    prog_ids = [tid for tid, t in tmeta.items() if t["arm_class"] == "drift" and t["label_domain"] == "programming"]
    other_ids = [tid for tid, t in tmeta.items() if t["arm_class"] == "drift" and t["label_domain"] not in (None, "programming")]

    # ---- (A) class-conditional expert usage ------------------------------
    n_dims = len(BAND) * 64
    class_usage = np.zeros((len(classes), n_dims))
    class_count = np.zeros(len(classes))
    code_usage = np.zeros(n_dims)
    code_count = 0
    other_usage = defaultdict(lambda: np.zeros(n_dims))
    other_count = defaultdict(int)

    def onehot(top_k: torch.Tensor, idx: np.ndarray) -> np.ndarray:
        """[len(idx), dims] top-8 indicator over (band layer, expert)."""
        sel = top_k[list(BAND)][:, idx, :]  # [L, n, 8]
        out = np.zeros((len(idx), len(BAND), 64), dtype=np.float32)
        for li in range(len(BAND)):
            for j in range(len(idx)):
                out[j, li, sel[li, j].tolist()] = 1.0
        return out.reshape(len(idx), -1)

    for tid, t in tmeta.items():
        tr = traces[tid]
        tk = tr.top_k_ids
        rows = trace_ix_all == t["trace_ix"]
        cls = tclass_all[rows]
        pos = pos_all[rows]
        if tid in fit_ids:
            oh = onehot(tk, np.arange(t["token_count"]))
            for ci in range(len(classes)):
                m = cls == ci
                if m.any():
                    class_usage[ci] += oh[m].sum(0)
                    class_count[ci] += int(m.sum())
        if t["arm_class"] == "drift" and t["product_onset"] is not None:
            o = t["product_onset"]
            idx = np.arange(o, min(o + WINDOW, t["token_count"]))
            oh = onehot(tk, idx)
            if t["label_domain"] == "programming":
                code_usage += oh.sum(0)
                code_count += len(idx)
            elif t["label_domain"] is not None:
                other_usage[t["label_domain"]] += oh.sum(0)
                other_count[t["label_domain"]] += len(idx)

    class_profile = class_usage / np.maximum(1, class_count)[:, None]
    code_profile = code_usage / max(1, code_count)
    routine_profile = class_usage.sum(0) / max(1.0, class_count.sum())

    def cos(a, b):
        return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12))

    report = {
        "band": list(BAND),
        "window": WINDOW,
        "class_expert_profile_cosine": {
            "code_vs_routine_overall": cos(code_profile, routine_profile),
            "code_vs_class": {classes[i]: cos(code_profile, class_profile[i]) for i in range(len(classes))},
            "routine_class_vs_routine_overall": {
                classes[i]: cos(class_profile[i], routine_profile) for i in range(len(classes))
            },
            "other_drift_vs_routine_overall": {
                d: cos(other_usage[d] / max(1, other_count[d]), routine_profile) for d in sorted(other_usage)
            },
            "other_drift_vs_class": {
                d: {classes[i]: cos(other_usage[d] / max(1, other_count[d]), class_profile[i]) for i in range(len(classes))}
                for d in sorted(other_usage)
            },
        },
        "class_token_counts_fit": {classes[i]: int(class_count[i]) for i in range(len(classes))},
    }

    # ---- (B) CAND-A geometry at w=8 --------------------------------------
    def wins(tid):
        ends, w = selection_rate_windows(traces[tid].top_k_ids, W8, list(BAND))
        return ends.numpy(), w.numpy()

    fit_blocks = []
    for tid in sorted(fit_ids):
        _, w = wins(tid)
        if len(w):
            fit_blocks.append(w)
    fit_mat = np.concatenate(fit_blocks)
    mu = fit_mat.mean(0)
    sd = fit_mat.std(0) + 1e-3
    centre = ((fit_mat - mu) / sd).mean(0)
    fit_z = (fit_mat - mu) / sd - centre
    fit_g1 = (fit_z ** 2).sum(1)

    def region_windows(tid, lo, hi):
        ends, w = wins(tid)
        keep = (ends >= lo + W8 - 1) & (ends < hi)
        return w[keep]

    def region_stats(mat):
        if not len(mat):
            return None
        z = (mat - mu) / sd - centre
        d_raw = mat.mean(0) - mu
        delta = z.mean(0)
        return {
            "n_windows": int(len(mat)),
            "g1_median": float(np.median((z ** 2).sum(1))),
            "raw_shift_norm": float(np.linalg.norm(d_raw)),
            "z_shift_norm": float(np.linalg.norm(delta)),
            "effective_routine_sd_along_shift": float(np.linalg.norm(d_raw) / (np.linalg.norm(delta) + 1e-12)),
            "mean_routine_sd": float(sd.mean()),
        }

    held_mat = np.concatenate([wins(tid)[1] for tid in sorted(held_ids) if len(wins(tid)[1])])
    code_mat = np.concatenate(
        [region_windows(tid, tmeta[tid]["product_onset"], min(tmeta[tid]["product_onset"] + WINDOW, tmeta[tid]["token_count"])) for tid in sorted(prog_ids)]
    )
    other_mats = defaultdict(list)
    for tid in sorted(other_ids):
        t = tmeta[tid]
        if t["product_onset"] is None:
            continue
        m = region_windows(tid, t["product_onset"], min(t["product_onset"] + WINDOW, t["token_count"]))
        if len(m):
            other_mats[t["label_domain"]].append(m)

    report["candA_geometry_w8"] = {
        "routine_fit": {"n_windows": int(len(fit_mat)), "g1_median": float(np.median(fit_g1)), "g1_q90": float(np.quantile(fit_g1, 0.9))},
        "routine_held": region_stats(held_mat),
        "code": region_stats(code_mat),
        "other_drift_by_domain": {d: region_stats(np.concatenate(v)) for d, v in sorted(other_mats.items())},
        "per_code_trace": {
            tid: region_stats(region_windows(tid, tmeta[tid]["product_onset"], min(tmeta[tid]["product_onset"] + WINDOW, tmeta[tid]["token_count"])))
            for tid in sorted(prog_ids)
        },
    }

    # where does the code shift live?  top dimensions by |d_raw| and their routine sd
    d_raw_code = code_mat.mean(0) - mu
    order = np.argsort(-np.abs(d_raw_code))[:15]
    report["code_shift_top_dims"] = [
        {
            "layer": int(BAND[int(i) // 64]),
            "expert": int(int(i) % 64),
            "d_raw": float(d_raw_code[i]),
            "routine_sd": float(sd[i]),
            "z_shift": float(d_raw_code[i] / sd[i]),
            "routine_mean": float(mu[i]),
            "top_class_by_usage": classes[int(np.argmax(class_profile[:, i]))],
            "class_profile_at_dim": {classes[c]: float(class_profile[c, i]) for c in range(len(classes))},
        }
        for i in order
    ]
    # same for the strongest other-drift domain, as contrast
    dom = max(other_mats, key=lambda d: np.linalg.norm(np.concatenate(other_mats[d]).mean(0) - mu))
    d_raw_o = np.concatenate(other_mats[dom]).mean(0) - mu
    order_o = np.argsort(-np.abs(d_raw_o))[:15]
    report["contrast_domain"] = dom
    report["other_shift_top_dims"] = [
        {
            "layer": int(BAND[int(i) // 64]),
            "expert": int(int(i) % 64),
            "d_raw": float(d_raw_o[i]),
            "routine_sd": float(sd[i]),
            "z_shift": float(d_raw_o[i] / sd[i]),
            "top_class_by_usage": classes[int(np.argmax(class_profile[:, i]))],
        }
        for i in order_o
    ]

    (OUT / "token_identity_experts.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote", OUT / "token_identity_experts.json")


if __name__ == "__main__":
    main()
