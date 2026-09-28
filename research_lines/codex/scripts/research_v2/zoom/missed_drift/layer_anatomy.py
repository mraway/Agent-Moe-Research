#!/usr/bin/env python3
"""Per-layer decomposition of the two candidates on every miss, plus the three
nearest routine windows (CAND-A) at onset+8."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ART, OUT, cases_for, snippet  # noqa: E402
from research_v2 import io as rio  # noqa: E402
from research_v2.features import selection_rate_windows  # noqa: E402
from research_v2.harness import routine_traces  # noqa: E402
from research_v2.scorers.pdm import PdmScorer  # noqa: E402
from research_v2.scorers.wgm import WGMScorer  # noqa: E402

torch.set_num_threads(6)

A_LAYERS = tuple(range(5, 16))
B_LAYERS = tuple(range(5, 12))


def wgm_layer_contrib(state, trace, layers, width):
    ends, windows = selection_rate_windows(trace.top_k_ids, width, layers)
    if not ends.numel():
        return ends, None, None
    z = (windows - state.mu) / state.sd
    centred = z - state.centre
    per = (centred**2).reshape(centred.shape[0], len(layers), 64).sum(2)  # [nwin, L]
    return ends, per, centred


def pdm_layer_contrib(scorer, state, trace):
    """Per depth-link surprisal contribution -log P(e_{l+1}|e_l), token level."""
    top1 = state and trace.top_k_ids[list(scorer.layers), :, 0].long()
    parts = [-state.log_depth_initial[top1[0]]]
    for i in range(top1.shape[0] - 1):
        parts.append(-state.log_depth[i][top1[i], top1[i + 1]])
    return torch.stack(parts)  # [L, T]  (row0 = initial marginal, then L-1 links)


def window_mean_rows(per_token: torch.Tensor, width: int):
    T = per_token.shape[1]
    if T < width:
        return torch.empty(0, dtype=torch.long), per_token.new_empty((per_token.shape[0], 0))
    csum = torch.cat((per_token.new_zeros((per_token.shape[0], 1)), per_token.cumsum(1)), 1)
    means = (csum[:, width:] - csum[:, :-width]) / float(width)
    ends = torch.arange(width - 1, T, dtype=torch.long)
    return ends, means


def main() -> None:
    misses = json.loads((OUT / "misses.json").read_text())
    batches = rio.load_core()
    cases = cases_for(batches)
    by_id = {t.trace_id: t for b in batches.values() for t in b}
    out: dict = {"CAND-A": {}, "CAND-B": {}}

    for case_name, case in cases.items():
        fit_pool = routine_traces(case.fit_traces, "cb")
        # ---- CAND-A ----
        wgm = WGMScorer(window_width=8, layers="middle_late", metric="g1")
        sa = wgm.fit(fit_pool)
        # routine reference windows (fit-side) for nearest-neighbour text lookup
        ref_vecs, ref_meta = [], []
        for tr in fit_pool:
            ends, per, centred = wgm_layer_contrib(sa, tr, A_LAYERS, 8)
            if centred is None:
                continue
            step = 4
            ref_vecs.append(centred[::step])
            ref_meta += [(tr.trace_id, int(e)) for e in ends[::step].tolist()]
        ref = torch.cat(ref_vecs)
        # routine mean per-layer contribution
        rl = []
        for tr in fit_pool:
            _, per, _ = wgm_layer_contrib(sa, tr, A_LAYERS, 8)
            if per is not None:
                rl.append(per)
        routine_layer_mean = torch.cat(rl).mean(0)

        for m in misses["CAND-A"].get(case_name, {}).get("misses", []):
            tr = by_id[m["trace_id"]]
            ends, per, centred = wgm_layer_contrib(sa, tr, A_LAYERS, 8)
            idx = {int(e): i for i, e in enumerate(ends.tolist())}
            onset = m["onset"]
            targ = min((e for e in idx if e >= onset + 8), default=None)
            rec = {"routine_layer_mean": [round(float(v), 2) for v in routine_layer_mean]}
            # layer contribution at post-onset max window within onset..onset+16
            cand = [e for e in idx if onset <= e <= onset + 16]
            if cand:
                best = max(cand, key=lambda e: float(per[idx[e]].sum()))
                rec["window_at_max_in16"] = best
                rec["layer_contrib_at_max_in16"] = [
                    round(float(v), 2) for v in per[idx[best]]
                ]
                rec["layer_ratio_at_max_in16"] = [
                    round(float(v / w), 2) for v, w in zip(per[idx[best]], routine_layer_mean)
                ]
            if targ is not None:
                rec["window_onset_plus8"] = targ
                rec["layer_contrib_onset8"] = [round(float(v), 2) for v in per[idx[targ]]]
                rec["layer_ratio_onset8"] = [
                    round(float(v / w), 2) for v, w in zip(per[idx[targ]], routine_layer_mean)
                ]
                q = centred[idx[targ]].unsqueeze(0)
                dist = torch.cdist(q, ref).reshape(-1)
                near = dist.topk(3, largest=False)
                rec["nearest_routine"] = []
                for d, j in zip(near.values.tolist(), near.indices.tolist()):
                    tid, end = ref_meta[j]
                    rt = by_id[tid]
                    rec["nearest_routine"].append(
                        {
                            "trace_id": tid,
                            "arm": rt.arm,
                            "domain": rt.scenario_domain,
                            "workflow": rt.workflow,
                            "end": end,
                            "distance": round(d, 2),
                            "text": snippet(rt, end - 7, end + 1),
                        }
                    )
                own = float(torch.linalg.norm(q))
                rec["own_norm"] = round(own, 2)
            out["CAND-A"].setdefault(case_name, {})[m["trace_id"]] = rec

        # ---- CAND-B ----
        pdm = PdmScorer(window_width=4, model="d1", layers="middle")
        sb = pdm.fit(fit_pool)
        rl = []
        for tr in fit_pool:
            pt = pdm_layer_contrib(pdm, sb, tr)
            rl.append(pt)
        routine_link_mean = torch.cat(rl, dim=1).mean(1)
        for m in misses["CAND-B"].get(case_name, {}).get("misses", []):
            tr = by_id[m["trace_id"]]
            pt = pdm_layer_contrib(pdm, sb, tr)
            ends, means = window_mean_rows(pt, 4)
            idx = {int(e): i for i, e in enumerate(ends.tolist())}
            onset = m["onset"]
            rec = {"routine_link_mean": [round(float(v), 2) for v in routine_link_mean]}
            cand = [e for e in idx if onset <= e <= onset + 16]
            if cand:
                best = max(cand, key=lambda e: float(means[:, idx[e]].sum()))
                rec["window_at_max_in16"] = best
                rec["link_contrib_at_max_in16"] = [
                    round(float(v), 2) for v in means[:, idx[best]]
                ]
                rec["link_delta_at_max_in16"] = [
                    round(float(v - w), 2)
                    for v, w in zip(means[:, idx[best]], routine_link_mean)
                ]
            targ = min((e for e in idx if e >= onset + 8), default=None)
            if targ is not None:
                rec["window_onset_plus8"] = targ
                rec["link_contrib_onset8"] = [round(float(v), 2) for v in means[:, idx[targ]]]
                rec["link_delta_onset8"] = [
                    round(float(v - w), 2) for v, w in zip(means[:, idx[targ]], routine_link_mean)
                ]
            out["CAND-B"].setdefault(case_name, {})[m["trace_id"]] = rec
        print(f"done {case_name}", flush=True)

    (OUT / "layer_anatomy.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print("written")


if __name__ == "__main__":
    main()
