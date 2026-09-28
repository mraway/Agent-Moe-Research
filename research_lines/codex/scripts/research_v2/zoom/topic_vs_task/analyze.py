"""Zoom audit: topic mention vs task execution -- mode-D cross-fitted analysis.

Everything here is a counterfactual diagnostic on frozen B1/B2 development data.
Protocol: mode D (target routine, scenario-disjoint halves), alpha=0.10, reading persist2,
comparison ">=", position buckets end//32 merged to >=30 calibration traces (harness default).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import torch

torch.set_num_threads(6)

import research_v2.io as rio
from research_v2.harness import (
    build_split_cases,
    conformal_threshold,
    fit_bucket_stats,
    routine_traces,
    scenario_halves,
)
from research_v2.readings import read_persist_m

OUT = Path("/home/wzh/Agent-Moe-Research/artifacts/agent_v2/research_v2/zoom/topic_vs_task")
BAND = list(range(5, 16))
MID_IDX = [i for i, L in enumerate(BAND) if L <= 11]
LATE_IDX = [i for i, L in enumerate(BAND) if L >= 12]
ALPHA = 0.10
TAIL_Q = 0.90

CANDS = {
    "A": {"agg": "agg_a", "pl": "pl_a", "ends": "ends_a", "w": 8,
          "label": "CAND-A wgm G1 whitened, layers 5-15, w=8"},
    "B": {"agg": "agg_b", "pl": "pl_bw", "ends": "ends_b", "w": 4,
          "label": "CAND-B pdm D1 depth-chain, layers 5-11, w=4 (per-layer feature from aux 5-15 chain)"},
}


def persist2(z):
    return read_persist_m(torch.as_tensor(z, dtype=torch.float64), 2).numpy()


def runs_of(mask):
    """[(start_idx, length)] contiguous True runs."""
    out, i, n = [], 0, len(mask)
    while i < n:
        if mask[i]:
            j = i
            while j + 1 < n and mask[j + 1]:
                j += 1
            out.append((i, j - i + 1))
            i = j + 1
        else:
            i += 1
    return out


def main() -> None:
    streams = np.load(OUT / "streams.npz")
    sqrt_streams = np.load(OUT / "streams_sqrt.npz")
    batches = rio.load_core()
    cases = build_split_cases(batches, "S1")
    by_id = {t.trace_id: t for b in batches.values() for t in b}

    records = []
    diagnostics = {}
    dump = {}
    for case in cases:
        cn = case.name
        target = list(case.target_traces)
        halves = scenario_halves(target)
        troutine = routine_traces(target, "cb")
        for ck, cfg in CANDS.items():
            w = cfg["w"]
            ends = {t.trace_id: streams[f"{cn}::{t.trace_id}::{cfg['ends']}"] for t in target}
            agg = {t.trace_id: streams[f"{cn}::{t.trace_id}::{cfg['agg']}"].astype(np.float64) for t in target}
            pl = {t.trace_id: streams[f"{cn}::{t.trace_id}::{cfg['pl']}"].astype(np.float64) for t in target}
            alt = {t.trace_id: sqrt_streams[f"{cn}::{t.trace_id}::agg_asqrt"].astype(np.float64)
                   for t in target} if ck == "A" else None

            for half in (0, 1):
                cal = [t for t in troutine if halves[t.pair_group_id] == half]
                ev = [t for t in target if halves[t.pair_group_id] != half]
                cal_streams = [(torch.as_tensor(agg[t.trace_id]), torch.as_tensor(ends[t.trace_id]).long())
                               for t in cal]
                bs = fit_bucket_stats(cal_streams, bucket_size=32, min_bucket_traces=30)
                zc = [bs.standardize(s, e).numpy() for s, e in cal_streams]
                thr = conformal_threshold([float(persist2(z).max()) for z in zc if z.size], ALPHA)["threshold"]

                # per-layer bucket stats + own routine tail (window level, 1-TAIL_Q)
                nL = pl[cal[0].trace_id].shape[1]
                lay_bs, lay_tail, lay_mu = [], [], []
                for j in range(nL):
                    ls = [(torch.as_tensor(pl[t.trace_id][:, j]), torch.as_tensor(ends[t.trace_id]).long())
                          for t in cal]
                    b = fit_bucket_stats(ls, bucket_size=32, min_bucket_traces=30)
                    lay_bs.append(b)
                    zz = np.concatenate([b.standardize(s, e).numpy() for s, e in ls])
                    lay_tail.append(float(np.quantile(zz, TAIL_Q)))
                    lay_mu.append(float(np.concatenate([pl[t.trace_id][:, j] for t in cal]).mean()))
                lay_mu = np.asarray(lay_mu)

                # alternative statistic: per-layer max z (fully recalibrated)
                def plmax(tid):
                    e = torch.as_tensor(ends[tid]).long()
                    zs = np.stack([lay_bs[j].standardize(torch.as_tensor(pl[tid][:, j]), e).numpy()
                                   for j in range(nL)], axis=1)
                    return zs
                thr_plmax = conformal_threshold(
                    [float(persist2(plmax(t.trace_id).max(1)).max()) for t in cal], ALPHA)["threshold"]
                # sqrt variant threshold (A only)
                if alt is not None:
                    alt_streams = [(torch.as_tensor(alt[t.trace_id]), torch.as_tensor(ends[t.trace_id]).long())
                                   for t in cal]
                    bs_alt = fit_bucket_stats(alt_streams, bucket_size=32, min_bucket_traces=30)
                    thr_alt = conformal_threshold(
                        [float(persist2(bs_alt.standardize(s, e).numpy()).max()) for s, e in alt_streams],
                        ALPHA)["threshold"]

                # feature thresholds from calibration windows
                lf_cal, lm_cal = [], []
                for t in cal:
                    zs = plmax(t.trace_id)
                    lf_cal.append((zs >= np.asarray(lay_tail)).mean(1))
                    raw = pl[t.trace_id] / lay_mu
                    lm_cal.append(raw[:, LATE_IDX].mean(1) / np.maximum(raw[:, MID_IDX].mean(1), 1e-9))
                lf_cal = np.concatenate(lf_cal); lm_cal = np.concatenate(lm_cal)
                lf_thr = float(np.quantile(lf_cal, TAIL_Q))
                lm_thr = float(np.quantile(lm_cal, TAIL_Q))
                clean_peaks = []

                pending = []
                for t in ev:
                    e = torch.as_tensor(ends[t.trace_id]).long()
                    if e.numel() == 0:
                        continue
                    z = bs.standardize(torch.as_tensor(agg[t.trace_id]), e).numpy()
                    st = persist2(z)
                    zs = plmax(t.trace_id)
                    lf = (zs >= np.asarray(lay_tail)).mean(1)
                    raw = pl[t.trace_id] / lay_mu
                    lm = raw[:, LATE_IDX].mean(1) / np.maximum(raw[:, MID_IDX].mean(1), 1e-9)
                    stpl = persist2(zs.max(1))
                    stalt = persist2(bs_alt.standardize(torch.as_tensor(alt[t.trace_id]), e).numpy()) \
                        if alt is not None else None
                    mask = st >= thr
                    rr = runs_of(mask)
                    ends_np = e.numpy()
                    peak_i = int(np.argmax(st)) if np.isfinite(st).any() else 0
                    rec = {
                        "case": cn, "cand": ck, "half": half, "trace_id": t.trace_id,
                        "arm": t.arm, "domain": t.scenario_domain, "channel": t.channel,
                        "workflow": t.workflow, "positive": bool(t.positive),
                        "token_count": int(t.token_count),
                        "evidence_onset": t.evidence_onset,
                        "completion_boundary": t.completion_boundary,
                        "threshold": float(thr), "thr_plmax": float(thr_plmax),
                        "thr_alt": float(thr_alt) if alt is not None else None,
                        "lf_thr": lf_thr, "lm_thr": lm_thr,
                        "peak_stat": float(np.nanmax(st[np.isfinite(st)])) if np.isfinite(st).any() else None,
                        "peak_end": int(ends_np[peak_i]),
                        "peak_layer_frac": float(lf[peak_i]), "peak_late_mid": float(lm[peak_i]),
                        "peak_plmax": float(zs[peak_i].max()),
                        "n_windows": int(e.numel()),
                        "alarm": bool(mask.any()),
                        "first_alarm_end": int(ends_np[mask][0]) if mask.any() else None,
                        "runs": [[int(ends_np[s]), int(k), int(np.ceil(k / w))] for s, k in rr],
                        "max_run_windows": int(max([k for _, k in rr], default=0)),
                        "max_run_blocks": int(max([int(np.ceil(k / w)) for _, k in rr], default=0)),
                    }
                    # excursion features at the first alarm window
                    if mask.any():
                        i0 = int(np.argmax(mask))
                        rec["alarm_layer_frac"] = float(lf[i0])
                        rec["alarm_late_mid"] = float(lm[i0])
                        rec["alarm_stat"] = float(st[i0])
                    # rule outcomes (causal first-alarm end for each rule)
                    anchor = t.evidence_onset if t.positive else None

                    def first_end(m):
                        m = np.asarray(m, dtype=bool)
                        if not m.any():
                            return [None, None]
                        ae = ends_np[m]
                        post = ae[ae >= anchor] if anchor is not None else ae
                        return [int(ae[0]), int(post[0]) if post.size else None]
                    # R1: require the current alarm run to have reached w+1 consecutive windows
                    runlen = np.zeros(len(mask), dtype=int)
                    c = 0
                    for i, mv in enumerate(mask):
                        c = c + 1 if mv else 0
                        runlen[i] = c
                    rec["rule_first_end"] = {
                        "R0_baseline": first_end(mask),
                        "R1_blocks2": first_end(runlen >= w + 1),
                        "R2_layerfrac": first_end(mask & (lf >= lf_thr)),
                        "R3_latemid": first_end(mask & (lm >= lm_thr)),
                        "R4_perlayer_max": first_end(stpl >= thr_plmax),
                        "R5_sqrt": first_end(stalt >= thr_alt) if stalt is not None else None,
                        "R6_lf_and_blocks2": first_end((runlen >= w + 1) & (lf >= lf_thr)),
                    }
                    dump[f"{cn}::{ck}::{t.trace_id}::stat"] = st.astype(np.float32)
                    dump[f"{cn}::{ck}::{t.trace_id}::ends"] = ends_np.astype(np.int32)
                    dump[f"{cn}::{ck}::{t.trace_id}::lf"] = lf.astype(np.float32)
                    dump[f"{cn}::{ck}::{t.trace_id}::lm"] = lm.astype(np.float32)
                    dump[f"{cn}::{ck}::{t.trace_id}::plmaxstat"] = stpl.astype(np.float32)
                    if not t.positive and t.arm == "clean":
                        clean_peaks.append(rec["peak_stat"])
                    pending.append(rec)
                med = float(np.median(clean_peaks)) if clean_peaks else None
                for rec in pending:
                    rec["clean_peak_median"] = med
                    records.append(rec)
                diagnostics[f"{cn}|{ck}|half{half}"] = {
                    "threshold": float(thr), "thr_plmax": float(thr_plmax),
                    "thr_alt": float(thr_alt) if alt is not None else None,
                    "lf_thr": lf_thr, "lm_thr": lm_thr,
                    "cal_traces": len(cal), "eval_traces": len(ev),
                    "bucket_cap": bs.cap, "bucket_trace_counts": bs.trace_counts,
                    "layer_tail_z": lay_tail, "clean_peak_median": med,
                }
    np.savez_compressed(OUT / "zstreams.npz", **dump)
    json.dump({"records": records, "diagnostics": diagnostics},
              open(OUT / "records.json", "w"), indent=1)
    print("records:", len(records))


if __name__ == "__main__":
    main()
