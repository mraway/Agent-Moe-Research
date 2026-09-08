"""ADVERSARIAL RECOMPUTE of the 'per-layer marginal vs cross-layer joint' lens.

Independent implementation (no import of the colleague's scripts).  Diagnostic only:
nothing here is calibrated or evaluated as a detector.

Computed per routine-fit fold (fit on one half of routine, read every routine
reference value on the held-out half):

  M1  CAND-A g1 (w=8, layers 5-15, variance floor 1e-3, global centre) window median
  J1  adjacent-layer top-1 pair unseen rate  (l, l+1), l = 0..14
  J2  the same, split by whether BOTH endpoints are routine-common (>= 1/64 top-1
      marginal): 'novel combination of familiar experts' -- unconditional rate AND
      the rate CONDITIONAL on both endpoints being familiar
  J3  independence null: the unseen-pair rate predicted from the region's OWN
      per-layer top-1 marginals (p_l^T U_l p_{l+1}); observed / expected = the part
      of J1 that is genuinely cross-layer joint
  M2  per-layer top-1 marginal unseen / rare(<1/64) rate, and a BASE-RATE-MATCHED
      marginal rare rate (rare set per layer chosen so the routine rate equals the
      routine pair-unseen rate), plus mean marginal surprisal
  token classes of every region, for a class-reweighted routine baseline

Splits: (a) the colleague's alternating pair-group 2-fold, (b) a cross-batch split
(fit B1 routine -> score B2 drift and vice versa).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import torch

ROOT = Path("/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363")
sys.path.insert(0, str(ROOT / "src"))

from research_v2 import io as rio  # noqa: E402
from research_v2.features import selection_rate_windows  # noqa: E402

OUT = ROOT / "artifacts" / "agent_v2" / "research_v2" / "zoom_code_blindspot"
LABELS = ROOT / "docs" / "research_v2" / "labels" / "product_onset_v1_adjudicated.jsonl"
E = 64
WGM_LAYERS = tuple(range(5, 16))
W = 8
VAR_FLOOR = 1e-3
REGION = 48
UNIFORM = 1.0 / E


def token_class(text: str) -> str:
    s = text
    if s.strip() == "":
        return "space"
    core = s.strip()
    if any(ch.isalpha() for ch in core):
        return "word"
    if any(ch.isdigit() for ch in core):
        return "digit"
    return "punct"


class Ref:
    def __init__(self, traces):
        # --- WGM whitening
        blocks = []
        for t in traces:
            _, w = selection_rate_windows(t.top_k_ids, W, WGM_LAYERS)
            if w.shape[0]:
                blocks.append(w)
        m = torch.cat(blocks)
        self.mu = m.mean(0)
        self.sd = m.std(0) + VAR_FLOOR
        self.centre = ((m - self.mu) / self.sd).mean(0)

        # --- top-1 pair table and per-layer marginal
        pair = torch.zeros((15, E, E), dtype=torch.float64)
        marg = torch.zeros((16, E), dtype=torch.float64)
        n = 0
        for t in traces:
            top1 = t.top_k_ids.long()[:, :, 0]
            n += top1.shape[1]
            for l in range(16):
                marg[l] += torch.bincount(top1[l], minlength=E)
            for l in range(15):
                flat = top1[l] * E + top1[l + 1]
                pair[l] += torch.bincount(flat, minlength=E * E).reshape(E, E)
        self.n_tokens = n
        self.pair_count = pair
        self.seen = pair > 0
        self.unseen = ~self.seen
        self.marg = marg / n
        self.common = self.marg >= UNIFORM  # [16, 64]
        # routine held-out-free quantity: pair unseen base rate on the FIT half
        self.fit_pair_unseen = float(self.unseen.to(torch.float64).mean())
        self.surprisal = -(self.marg + 1e-9).log()

    def matched_rare_sets(self, target: float):
        """Per layer: smallest-frequency expert set whose routine mass ~ target."""
        sets = torch.zeros((16, E), dtype=torch.bool)
        for l in range(16):
            order = torch.argsort(self.marg[l])
            cum = 0.0
            for e in order.tolist():
                f = float(self.marg[l][e])
                if cum + f > target and cum > 0:
                    break
                sets[l, e] = True
                cum += f
        return sets


def wgm_g1(ref: Ref, trace, lo: int, hi: int):
    ends, w = selection_rate_windows(trace.top_k_ids, W, WGM_LAYERS)
    if not w.shape[0]:
        return []
    z = (w - ref.mu) / ref.sd - ref.centre
    g = (z**2).sum(1)
    keep = [i for i, e in enumerate(ends.tolist()) if (e - W + 1) >= lo and e < hi]
    return g[keep].tolist()


def region_stats(ref: Ref, matched: torch.Tensor, top1: torch.Tensor, lo: int, hi: int):
    seg = top1[:, lo:hi]
    T = seg.shape[1]
    tot = 0
    unseen_n = 0
    ff_n = 0
    ff_unseen = 0
    exp_unseen = 0.0
    # region marginals for the independence null
    rm = torch.zeros((16, E), dtype=torch.float64)
    for l in range(16):
        rm[l] = torch.bincount(seg[l], minlength=E).to(torch.float64) / T
    for l in range(15):
        a, b = seg[l], seg[l + 1]
        u = ref.unseen[l][a, b]
        c = ref.common[l][a] & ref.common[l + 1][b]
        unseen_n += int(u.sum())
        ff_n += int(c.sum())
        ff_unseen += int((u & c).sum())
        tot += T
        U = ref.unseen[l].to(torch.float64)
        exp_unseen += float(rm[l] @ U @ rm[l + 1]) * T
    marg_unseen = 0
    marg_rare = 0
    marg_matched = 0
    surp = 0.0
    mtot = 0
    for l in range(16):
        e = seg[l]
        marg_unseen += int((ref.marg[l][e] == 0).sum())
        marg_rare += int((ref.marg[l][e] < UNIFORM).sum())
        marg_matched += int(matched[l][e].sum())
        surp += float(ref.surprisal[l][e].sum())
        mtot += e.numel()
    return {
        "n_tokens": T,
        "pair_unseen_rate": unseen_n / tot,
        "ff_frac": ff_n / tot,
        "novel_comb_rate": ff_unseen / tot,
        "novel_comb_cond": ff_unseen / max(1, ff_n),
        "indep_expected_rate": exp_unseen / tot,
        "joint_excess": (unseen_n / tot) / max(1e-12, exp_unseen / tot),
        "marg_unseen_rate": marg_unseen / mtot,
        "marg_rare_rate": marg_rare / mtot,
        "marg_matched_rate": marg_matched / mtot,
        "marg_surprisal": surp / mtot,
    }


def main() -> None:
    labels = {}
    for line in LABELS.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            labels[r["trace_id"]] = r
    batches = rio.load_core()
    traces = [t for k in ("b1", "b2") for t in batches[k]]
    routine = [t for t in traces if rio.arm_class(t) in ("clean", "benign")]
    drift = [t for t in traces if t.positive]
    print(f"routine {len(routine)} traces {sum(t.token_count for t in routine)} tokens; drift {len(drift)}")

    groups = sorted({t.pair_group_id for t in routine})
    fold_of = {g: i % 2 for i, g in enumerate(groups)}
    splits = {
        "pairgroup": {
            0: [t for t in routine if fold_of[t.pair_group_id] == 0],
            1: [t for t in routine if fold_of[t.pair_group_id] == 1],
        },
        "batch": {
            0: [t for t in routine if t.batch == "b1"],
            1: [t for t in routine if t.batch == "b2"],
        },
    }
    # token class per region (anchor independent of fold)
    classes = {}
    for t in drift:
        lab = labels[t.trace_id]
        for anchor in ("product_onset", "evidence_onset"):
            o = lab.get(anchor)
            if o is None:
                continue
            lo = max(0, int(o))
            hi = min(lo + REGION, t.token_count)
            txt = rio.decode_token_texts(t.token_ids[lo:hi])
            key = f"{t.trace_id}|{anchor}"
            cl = {}
            for s in txt:
                c = token_class(s)
                cl[c] = cl.get(c, 0) + 1
            classes[key] = {"counts": cl, "lo": lo, "hi": hi}

    result = {"splits": {}, "region_classes": classes}
    for split_name, folds in splits.items():
        block = {}
        for f in (0, 1):
            fit = folds[f]
            hold = folds[1 - f]
            if split_name == "batch":
                # fit on one batch's routine, score the OTHER batch's traces only
                score_drift = [t for t in drift if t.batch != fit[0].batch]
            else:
                score_drift = drift
            ref = Ref(fit)
            # target for the base-rate-matched marginal rare set = the held-out routine
            # token-level pair-unseen rate (a routine-only quantity), computed first.
            probe = [region_stats(ref, torch.zeros((16, E), dtype=torch.bool), t.top_k_ids.long()[:, :, 0], 0, t.token_count)["pair_unseen_rate"] for t in hold]
            target = sum(probe) / len(probe)
            matched = ref.matched_rare_sets(target)
            fb = {"fit_tokens": ref.n_tokens, "routine": {}, "drift": {}, "wgm": {}}
            # routine held-out per trace, plus per token class
            cls_tot = {}
            cls_unseen = {}
            cls_ff_unseen = {}
            cls_ff = {}
            for t in hold:
                top1 = t.top_k_ids.long()[:, :, 0]
                st = region_stats(ref, matched, top1, 0, t.token_count)
                st["wgm_g1_median"] = float(torch.tensor(wgm_g1(ref, t, 0, t.token_count)).median()) if t.token_count >= W else None
                fb["routine"][t.trace_id] = st
                txt = rio.decode_token_texts(t.token_ids)
                cl = [token_class(s) for s in txt]
                for l in range(15):
                    a, b = top1[l], top1[l + 1]
                    u = ref.unseen[l][a, b]
                    c = ref.common[l][a] & ref.common[l + 1][b]
                    for i, cc in enumerate(cl):
                        cls_tot[cc] = cls_tot.get(cc, 0) + 1
                        if bool(u[i]):
                            cls_unseen[cc] = cls_unseen.get(cc, 0) + 1
                            if bool(c[i]):
                                cls_ff_unseen[cc] = cls_ff_unseen.get(cc, 0) + 1
                        if bool(c[i]):
                            cls_ff[cc] = cls_ff.get(cc, 0) + 1
            fb["routine_by_class"] = {
                c: {
                    "n": cls_tot[c],
                    "pair_unseen_rate": cls_unseen.get(c, 0) / cls_tot[c],
                    "novel_comb_rate": cls_ff_unseen.get(c, 0) / cls_tot[c],
                    "ff_frac": cls_ff.get(c, 0) / cls_tot[c],
                }
                for c in cls_tot
            }
            for t in score_drift:
                lab = labels[t.trace_id]
                top1 = t.top_k_ids.long()[:, :, 0]
                for anchor in ("product_onset", "evidence_onset"):
                    o = lab.get(anchor)
                    if o is None:
                        continue
                    lo = max(0, int(o))
                    hi = min(lo + REGION, t.token_count)
                    st = region_stats(ref, matched, top1, lo, hi)
                    g = wgm_g1(ref, t, lo, hi)
                    st["wgm_g1_median"] = float(torch.tensor(g).median()) if g else None
                    st["wgm_windows"] = len(g)
                    st["domain"] = t.domain
                    st["batch"] = t.batch
                    st["lo"] = lo
                    st["hi"] = hi
                    fb["drift"][f"{t.trace_id}|{anchor}"] = st
            block[str(f)] = fb
            fb["matched_target"] = target
            fb["matched_routine_mass"] = [float(ref.marg[l][matched[l]].sum()) for l in range(16)]
            print(f"{split_name} fold {f}: fit {ref.n_tokens} tokens, holdout pair-unseen={target:.5f}, matched mass mean={sum(fb['matched_routine_mass'])/16:.5f}", flush=True)
        result["splits"][split_name] = block
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "refute_joint_recompute.json").write_text(json.dumps(result), encoding="utf-8")
    print("written", OUT / "refute_joint_recompute.json")


if __name__ == "__main__":
    main()
