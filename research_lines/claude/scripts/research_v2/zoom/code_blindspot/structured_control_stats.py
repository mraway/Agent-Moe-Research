"""Matched control group for critic E7 -- step 2/3: the four per-trace statistics.

For every one of the 59 drift traces (8 programming + 51 non-programming) computes

  (i)   CAND-A frozen statistic: from the STORED score_streams of
        artifacts/agent_v2/research_v2/wgm/c2_g1_middle_late/result.json --
        the median stream value inside the product window and the max inside
        product_onset..+16, each expressed as a ratio to the SAME target batch's
        routine (clean+benign) pooled window-score q95 / q99.
  (ii)  rare-expert mass: fraction of the product window's top-8 selections
        (layers 5-15) landing on experts whose routine selection rate at that layer
        is < 0.02, routine = clean+benign traces of the SAME batch.
  (iii) frozen CAND-A (wgm c2_g1_middle_late) and CAND-B (pdm_d1_middle_s1)
        mode=D, alpha=0.10, reading=persist2 first_alarm_end minus product_onset,
        read off the stored trace_alarms.
  (iv)  depth_profile rmass: median over the product window of the router
        probability mass on the routine top-8 experts, layers 11/12/13, with the
        reference defined exactly as depth_profile_compute.py does (top-8 experts by
        SELECTION FREQUENCY on the fit half of the 240 routine traces).

Read-only, post-hoc diagnosis on the development batches B1/B2.  Windows are anchored
on adjudicated labels; nothing here is a detector and no improvement is claimed.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import torch

from research_v2 import io as rio

REPO = Path("/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363")
LABELS = REPO / "docs/research_v2/labels/product_onset_v1_adjudicated.jsonl"
OUT = REPO / "artifacts/agent_v2/research_v2/zoom_code_blindspot/structured_control"
CAND_A = REPO / "artifacts/agent_v2/research_v2/wgm/c2_g1_middle_late/result.json"
CAND_B = REPO / "artifacts/agent_v2/research_v2/pdm_d1_middle_s1/result.json"

WINDOW_SPAN = 48
PLUS = 16
RARE_TH = 0.02
RARE_LAYERS = tuple(range(5, 16))
NLAYERS, NEXPERTS = 16, 64
EPS = 1e-12


def short(tid: str) -> str:
    return "-".join(tid.split("-")[:3])


def load_labels() -> dict[str, dict]:
    return {json.loads(l)["trace_id"]: json.loads(l)
            for l in LABELS.read_text(encoding="utf-8").splitlines() if l.strip()}


# --------------------------------------------------------------------------- (i)+(iii)
def frozen_streams(path: Path):
    d = json.loads(path.read_text())
    streams, alarms = {}, {}
    for c in d["case_runs"]:
        tgt = c["detail"]["target_batch"]
        for tid, s in c.get("score_streams", {}).items():
            streams[tid] = (np.asarray(s["ends"]), np.asarray(s["scores"], dtype=float), tgt)
        for cand in c["candidates"]:
            if cand["mode"] == "D" and abs(cand["alpha"] - 0.1) < 1e-9 and cand["reading"] == "persist2":
                for row in cand["trace_alarms"]:
                    alarms[row[0]] = {"first_alarm_end": row[2], "first_band_alarm_end": row[3],
                                      "alarm_onset_count": row[4], "candidate_id": cand["candidate_id"]}
    return streams, alarms


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    labels = load_labels()
    batches = rio.load_core()
    traces = {t.trace_id: t for b in ("b1", "b2") for t in batches[b]}
    drift = [t for t in traces.values() if rio.arm_class(t) == "drift"]
    drift.sort(key=lambda t: t.trace_id)
    assert len(drift) == 59, len(drift)

    a_streams, a_alarms = frozen_streams(CAND_A)
    b_streams, b_alarms = frozen_streams(CAND_B)

    # ---- (i) routine reference quantiles of the CAND-A stream, per target batch ----
    pooled = defaultdict(list)
    for tid, (ends, sc, tgt) in a_streams.items():
        if rio.arm_class(traces[tid]) in ("clean", "benign"):
            pooled[tgt].append(sc)
    ref_q = {b: {"q95": float(np.quantile(np.concatenate(v), 0.95)),
                 "q99": float(np.quantile(np.concatenate(v), 0.99)),
                 "median": float(np.median(np.concatenate(v))),
                 "n_windows": int(np.concatenate(v).size),
                 "n_traces": len(v)} for b, v in pooled.items()}

    # ---- (ii) rare-expert sets, routine = cb of the SAME batch --------------------
    rare_mask, rare_meta = {}, {}
    for bkey in ("b1", "b2"):
        sel = torch.zeros(NLAYERS, NEXPERTS, dtype=torch.float64)
        ntok = 0
        for t in batches[bkey]:
            if rio.arm_class(t) not in ("clean", "benign"):
                continue
            oh = torch.zeros(NLAYERS, t.token_count, NEXPERTS)
            oh.scatter_(2, t.top_k_ids, 1.0)
            sel += oh.sum(1).double()
            ntok += t.token_count
        mu = (sel / ntok)                                  # [16,64] selection rate
        rare_mask[bkey] = (mu < RARE_TH)
        rare_meta[bkey] = {"n_routine_traces": sum(1 for t in batches[bkey]
                                                   if rio.arm_class(t) in ("clean", "benign")),
                           "n_routine_tokens": ntok,
                           "n_rare_coords_5_15": int(rare_mask[bkey][list(RARE_LAYERS)].sum()),
                           "n_coords_5_15": len(RARE_LAYERS) * NEXPERTS}

    def rare_mass(t, lo, hi) -> float:
        m = rare_mask[t.batch]
        tk = t.top_k_ids[list(RARE_LAYERS), lo:hi, :]      # [11, n, 8]
        rm = m[list(RARE_LAYERS)]                          # [11,64]
        hit = torch.gather(rm.unsqueeze(1).expand(-1, tk.shape[1], -1), 2, tk)
        return float(hit.float().mean())

    # routine baseline for (ii): every routine trace scored on all its tokens
    rare_routine = {}
    for bkey in ("b1", "b2"):
        vals = [rare_mass(t, 0, t.token_count) for t in batches[bkey]
                if rio.arm_class(t) in ("clean", "benign")]
        rare_routine[bkey] = {"median": float(np.median(vals)), "mean": float(np.mean(vals)),
                              "q95": float(np.quantile(vals, 0.95)), "q99": float(np.quantile(vals, 0.99)),
                              "n": len(vals)}

    # ---- (iv) depth_profile rmass reference (fit half of the 240 routine traces) ---
    routine = [t for t in traces.values() if rio.arm_class(t) in ("clean", "benign")]
    routine.sort(key=lambda t: t.trace_id)
    per_group = defaultdict(list)
    for t in routine:
        per_group[(t.batch, t.arm)].append(t)
    fit, held = [], []
    for key in sorted(per_group):
        for i, t in enumerate(per_group[key]):
            (fit if i % 2 == 0 else held).append(t)
    sel = torch.zeros(NLAYERS, NEXPERTS, dtype=torch.float64)
    for t in fit:
        oh = torch.zeros(NLAYERS, t.token_count, NEXPERTS)
        oh.scatter_(2, t.top_k_ids, 1.0)
        sel += oh.sum(1).double()
    ref_rank = sel.argsort(dim=1, descending=True)
    ref_mask = torch.zeros(NLAYERS, NEXPERTS, dtype=torch.long)
    ref_mask.scatter_(1, ref_rank[:, :8], 1)
    ref_mask_f = ref_mask.float()

    def rmass_window(t, lo, hi) -> dict[str, float]:
        p = t.probabilities().float()[:, lo:hi, :]
        rm = (p * ref_mask_f.unsqueeze(1)).sum(-1)         # [16, n]
        med = rm.median(dim=1).values
        return {f"L{l}": float(med[l]) for l in range(NLAYERS)}

    # routine held-out null for rmass percentiles (48-token sliding windows, stride 8)
    null_rm = {l: [] for l in (11, 12, 13)}
    for t in held:
        p = t.probabilities().float()
        rm = (p * ref_mask_f.unsqueeze(1)).sum(-1)         # [16,T]
        T = p.shape[1]
        for s0 in range(0, max(1, T - 48 + 1), 8):
            e0 = min(s0 + 48, T)
            if e0 - s0 < 8:
                continue
            for l in (11, 12, 13):
                null_rm[l].append(float(rm[l, s0:e0].median()))
    null_arr = {l: np.sort(np.asarray(v)) for l, v in null_rm.items()}

    # ---- assemble per-trace rows --------------------------------------------------
    rows = []
    for t in drift:
        lab = labels[t.trace_id]
        onset = int(lab["product_onset"])
        T = int(t.token_count)
        hi = min(onset + WINDOW_SPAN, T)
        rec = {"short": short(t.trace_id), "trace_id": t.trace_id, "batch": t.batch,
               "domain": lab["domain"], "product_class": lab["product_class"],
               "product_onset": onset, "token_count": T, "window_hi": hi}

        # (i)
        ends, sc, tgt = a_streams[t.trace_id]
        m_win = (ends >= onset) & (ends < hi)
        m_16 = (ends >= onset) & (ends <= onset + PLUS)
        q = ref_q[tgt]
        med = float(np.median(sc[m_win])) if m_win.any() else None
        mx = float(np.max(sc[m_16])) if m_16.any() else None
        rec["candA"] = {
            "target_batch": tgt, "n_windows_in_product": int(m_win.sum()),
            "n_windows_plus16": int(m_16.sum()),
            "window_median": med, "max_plus16": mx,
            "median_over_q95": med / q["q95"] if med is not None else None,
            "median_over_q99": med / q["q99"] if med is not None else None,
            "max16_over_q95": mx / q["q95"] if mx is not None else None,
            "max16_over_q99": mx / q["q99"] if mx is not None else None,
        }
        # (ii)
        rec["rare_expert_mass"] = rare_mass(t, onset, hi)
        rec["rare_expert_mass_over_routine_median"] = (
            rec["rare_expert_mass"] / rare_routine[t.batch]["median"])
        # (iii)
        for name, al in (("candA", a_alarms), ("candB", b_alarms)):
            row = al.get(t.trace_id)
            fa = row["first_alarm_end"] if row else None
            band = row["first_band_alarm_end"] if row else None
            rec[f"{name}_alarm"] = {
                "first_alarm_end": fa,
                "offset_from_product_onset": (int(fa) - onset) if fa is not None else None,
                "first_band_alarm_end": band,
                "band_offset": (int(band) - onset) if band is not None else None,
                "alarm_onset_count": row["alarm_onset_count"] if row else None,
            }
        # (iv)
        rm = rmass_window(t, onset, hi)
        rec["rmass"] = {k: rm[k] for k in ("L11", "L12", "L13")}
        rec["rmass"]["L11_13_mean"] = float(np.mean([rm["L11"], rm["L12"], rm["L13"]]))
        rec["rmass_pct"] = {f"L{l}": float(np.searchsorted(null_arr[l], rm[f"L{l}"]) / null_arr[l].size)
                            for l in (11, 12, 13)}
        rows.append(rec)

    out = {
        "meta": {
            "window": "product_onset .. min(product_onset+48, T)",
            "candA_result": str(CAND_A), "candB_result": str(CAND_B),
            "candA_routine_reference": ref_q,
            "rare_threshold": RARE_TH, "rare_layers": list(RARE_LAYERS),
            "rare_reference": rare_meta, "rare_routine_trace_baseline": rare_routine,
            "rmass_fit_traces": len(fit), "rmass_held_traces": len(held),
            "rmass_ref_top8": ref_mask.nonzero().reshape(-1, 2)[:, 1].reshape(NLAYERS, 8).tolist(),
            "rmass_null_windows": {str(l): int(null_arr[l].size) for l in (11, 12, 13)},
            "rmass_null_median": {str(l): float(np.median(null_arr[l])) for l in (11, 12, 13)},
        },
        "rows": rows,
    }
    (OUT / "stats.json").write_text(json.dumps(out, indent=2))
    print("routine CAND-A stream reference:", json.dumps(ref_q, indent=1))
    print("rare coords:", {k: v["n_rare_coords_5_15"] for k, v in rare_meta.items()})
    print("rare routine trace baseline:", json.dumps(rare_routine, indent=1))
    print("rmass null medians:", out["meta"]["rmass_null_median"])
    for r in rows:
        if r["domain"] == "programming":
            print(f"{r['short']} medq95={r['candA']['median_over_q95']:.3f} "
                  f"rare={r['rare_expert_mass']:.4f} rmL11={r['rmass']['L11']:.3f}"
                  f"({r['rmass_pct']['L11']:.2f}) rmL13={r['rmass']['L13']:.3f}")
    print("wrote", OUT / "stats.json")


if __name__ == "__main__":
    main()
