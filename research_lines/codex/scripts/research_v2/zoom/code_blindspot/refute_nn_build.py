"""REFUTATION of LENS 'nearest-neighbour geometry in the WGM feature space'.

Independent re-implementation (does NOT import the lens scripts).  Builds:
  * routine (clean+benign_control) w=8 top-8 selection-rate windows, layers 5-15
    and all 16, per batch;
  * my own diagonal whitening z=(x-mu)/(std+1e-3) fitted on that batch's routine;
  * leave-one-trace-out routine d1/d10, plus d1 against sub-references
    (JSON-only / non-JSON-only routine windows);
  * target windows for every drift trace under the product and evidence anchors;
  * g1 = ||z-centre||^2 and the prose->JSON lobe axis projection.

Diagnostic only: onsets/domains are labels, used post-hoc.  No detector, no
threshold, no recall.  Read-only on traces/labels/frozen artefacts.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import torch

from research_v2 import io as rio
from research_v2.features import selection_rate_windows

REPO = Path(__file__).resolve().parents[4]
OUT = REPO / "artifacts" / "agent_v2" / "research_v2" / "zoom_code_blindspot"
LABELS = REPO / "docs" / "research_v2" / "labels" / "product_onset_v1_adjudicated.jsonl"
W = 8
SPAN = 48
K = 10
DEV = "cuda" if torch.cuda.is_available() else "cpu"
BANDS = {"middle_late": tuple(range(5, 16)), "all": tuple(range(16))}

# --- my own surface-form rule (deliberately different from the lens ladder) ---
# json_struct: the 8-token text carries JSON punctuation glued to a quote.
RE_JSON = re.compile(r'"\s*[:,}\]]|[:{,\[]\s*"|\{"|"\}')
RE_TOOL = re.compile(r'arguments|"type"|"action"|"name"|_id"|":\s*"')


def form(text: str) -> str:
    if RE_JSON.search(text) or RE_TOOL.search(text):
        return "json"
    return "nonjson"


def knn(q: torch.Tensor, r: torch.Tensor, k: int, qtrace=None, rtrace=None, block=2048):
    ds, idxs = [], []
    for s in range(0, q.shape[0], block):
        d = torch.cdist(q[s : s + block], r)
        if qtrace is not None:
            d = d.masked_fill(qtrace[s : s + block, None] == rtrace[None, :], float("inf"))
        v, i = torch.topk(d, min(k, r.shape[0]), dim=1, largest=False)
        ds.append(v.cpu())
        idxs.append(i.cpu())
    return torch.cat(ds), torch.cat(idxs)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    labels = {json.loads(l)["trace_id"]: json.loads(l) for l in LABELS.read_text().splitlines() if l.strip()}
    batches = rio.load_core()
    summary = {}
    for band_name, layers in BANDS.items():
        for batch, traces in batches.items():
            routine = [t for t in traces if rio.arm_class(t) in ("clean", "benign")]
            drift = [t for t in traces if rio.arm_class(t) == "drift"]

            rw, rt, re_, rtexts = [], [], [], []
            for ti, t in enumerate(routine):
                ends, win = selection_rate_windows(t.top_k_ids, W, layers)
                if not ends.numel():
                    continue
                rw.append(win)
                rt.append(torch.full((ends.numel(),), ti, dtype=torch.long))
                re_.append(ends)
                if band_name == "middle_late":
                    for e in ends.tolist():
                        rtexts.append(rio.decode_text(t.token_ids[e - W + 1 : e + 1]))
            R = torch.cat(rw).float()
            rt = torch.cat(rt)
            re_ = torch.cat(re_)
            mu = R.mean(0)
            sd = R.std(0) + 1e-3
            Rz = ((R - mu) / sd).to(DEV)
            centre = Rz.mean(0)

            if band_name == "middle_late":
                forms = [form(x) for x in rtexts]
                torch.save({"texts": rtexts, "forms": forms}, OUT / f"refute_rtext_{batch}.pt")
            else:
                forms = torch.load(OUT / f"refute_rtext_{batch}.pt", weights_only=False)["forms"]
            is_json = torch.tensor([f == "json" for f in forms])

            # routine leave-one-trace-out
            rd, ridx = knn(Rz, Rz, K, rt.to(DEV), rt.to(DEV))
            # sub-reference variants (leave-one-trace-out too)
            jz = Rz[is_json.to(DEV)]
            pz = Rz[~is_json.to(DEV)]
            jt = rt[is_json].to(DEV)
            pt = rt[~is_json].to(DEV)
            rd_j, _ = knn(Rz, jz, 1, rt.to(DEV), jt)
            rd_p, _ = knn(Rz, pz, 1, rt.to(DEV), pt)

            # targets
            qw, meta = [], []
            for t in drift:
                lab = labels[t.trace_id]
                ends, win = selection_rate_windows(t.top_k_ids, W, layers)
                if not ends.numel():
                    continue
                starts = ends - (W - 1)
                for anchor, onset in (("product", int(lab["product_onset"])), ("evidence", int(lab["evidence_onset"]))):
                    stop = min(onset + SPAN, t.token_count)
                    m = (starts >= onset) & (ends <= stop - 1)
                    sel = torch.nonzero(m).flatten()
                    if not sel.numel():
                        continue
                    qw.append(win[sel])
                    for j in sel.tolist():
                        meta.append({
                            "trace_id": t.trace_id, "domain": lab["domain"],
                            "product_class": lab["product_class"], "anchor": anchor,
                            "onset": onset, "end": int(ends[j]), "tokens": int(t.token_count),
                            "text": rio.decode_text(t.token_ids[int(ends[j]) - W + 1 : int(ends[j]) + 1]) if band_name == "middle_late" else "",
                        })
            Q = torch.cat(qw).float()
            Qz = ((Q - mu) / sd).to(DEV)
            qd, qidx = knn(Qz, Rz, K)
            qd_j, qidx_j = knn(Qz, jz, K)
            qd_p, qidx_p = knn(Qz, pz, K)

            # g1 and lobe axis
            cj = Rz[is_json.to(DEV)].mean(0)
            cp = Rz[~is_json.to(DEV)].mean(0)
            u = (cj - cp)
            sep = float(u.norm())
            u = u / u.norm()
            rproj = (Rz - centre) @ u
            qproj = (Qz - centre) @ u
            rg1 = ((Rz - centre) ** 2).sum(1)
            qg1 = ((Qz - centre) ** 2).sum(1)
            var_share = float((rproj.var()) / ((Rz - centre) ** 2).sum(1).mean())

            torch.save({
                "meta": meta,
                "routine_trace_ids": [t.trace_id for t in routine],
                "routine_workflow": [t.workflow for t in routine],
                "routine_pair": [t.pair_group_id for t in routine],
                "routine_trace_idx": rt, "routine_end": re_, "routine_is_json": is_json,
                "routine_d": rd, "routine_idx": ridx,
                "routine_d_json": rd_j, "routine_d_prose": rd_p,
                "target_d": qd, "target_idx": qidx,
                "target_d_json": qd_j, "target_idx_json": qidx_j,
                "target_d_prose": qd_p, "target_idx_prose": qidx_p,
                "routine_g1": rg1.cpu(), "target_g1": qg1.cpu(),
                "routine_proj": rproj.cpu(), "target_proj": qproj.cpu(),
                "lobe_sep": sep, "axis_var_share": var_share,
                "json_index": torch.nonzero(is_json).flatten(),
                "prose_index": torch.nonzero(~is_json).flatten(),
            }, OUT / f"refute_nn_{band_name}_{batch}.pt")
            summary[f"{band_name}|{batch}"] = {
                "routine_windows": int(R.shape[0]), "target_windows": int(Q.shape[0]),
                "dim": int(R.shape[1]), "json_frac": float(is_json.float().mean()),
                "lobe_sep": sep, "axis_var_share": var_share,
            }
            print(band_name, batch, summary[f"{band_name}|{batch}"], flush=True)
    (OUT / "refute_nn_build_summary.json").write_text(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
