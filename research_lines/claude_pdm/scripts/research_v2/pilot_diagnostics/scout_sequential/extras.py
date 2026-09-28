"""Extra post-hoc diagnostics (B2 frozen v1 route scores; development data; not results)."""
import json, math
from collections import defaultdict
import numpy as np
SRC = "/home/wzh/Agent-Moe-Research/artifacts/agent_v2/agent_v2_5_b2/sequential_evaluation.json"
B = "/home/wzh/Agent-Moe-Research/artifacts/agent_v2/agent_v2_5_b2"
OUT = "/tmp/claude-1000/-home-wzh-Agent-Moe-Research--claude-worktrees-algorithm-research-proposals-427363/7e87c1f8-78bb-4b9f-9189-12780e6e8833/scratchpad/scouts/sequential"
d = json.load(open(SRC))["trace_results"]
class T: pass
TR = []
for t in d:
    o = T(); o.id = t["trace_id"]; o.pg = t["pair_group_id"]; o.arm = t["arm"]; o.wf = t["workflow"]; o.dom = t["domain"]; o.ch = t["channel"]
    o.pos = bool(t["primary_positive"]); o.b = t["boundary"]; o.n = t["decode_token_count"]
    o.ends = np.array(t["models"]["route"]["window_ends"]); o.s = np.array(t["models"]["route"]["window_scores"]); TR.append(o)
NEG = [o for o in TR if not o.pos]; DRIFT = [o for o in TR if o.pos]

def decode_tokens(o):
    rows = [json.loads(l) for l in open(f"{B}/{o.pg}/{o.arm}/manifest.jsonl")]
    if o is TR[0]:
        print("manifest keys:", list(rows[0].keys()), "phases:", sorted({r.get('phase') for r in rows}))
    toks = []
    for r in rows:
        if r.get("phase") == "decode":
            toks.extend(r["token_texts"])
    return toks

res = {}
# 1. argmax window text for top non-drift traces
top = sorted(NEG, key=lambda o: -o.s.max())[:8]
res["top_non_drift_argmax_window_text"] = []
for o in top:
    toks = decode_tokens(o); e = int(o.ends[o.s.argmax()])
    txt = "".join(toks[max(0, e - 15):e + 1])
    res["top_non_drift_argmax_window_text"].append(dict(id=o.id, arm=o.arm, dom=o.dom, max=float(o.s.max()), end=e, n_tokens=len(toks), window_text=txt))
    print(f"{o.id} max={o.s.max():.3f} end={e} n={len(toks)} :: {txt!r}")

# 2. pre-boundary alarm characterization at raw max far10 threshold
def order_stat(maxima, alpha):
    n = len(maxima); k = min(n, math.ceil((n + 1) * (1 - alpha))); return float(np.sort(maxima)[k - 1])
thr10 = order_stat([o.s.max() for o in NEG], 0.10); thr5 = order_stat([o.s.max() for o in NEG], 0.05)
res["thresholds"] = dict(far10=thr10, far5=thr5)
res["pre_boundary_alarms_far10"] = []
print("\n--- pre-boundary alarms (raw max, FAR10 thr %.3f) ---" % thr10)
for o in DRIFT:
    st = o.s >= thr10; pre = st & (o.ends < o.b)
    if not pre.any(): continue
    toks = decode_tokens(o); e = int(o.ends[pre][0])
    txt = "".join(toks[max(0, e - 15):e + 1]); btxt = "".join(toks[o.b:o.b + 12])
    pre_max = float(o.s[o.ends < o.b].max()); post_max = float(o.s[o.ends >= o.b].max()) if (o.ends >= o.b).any() else None
    row = dict(id=o.id, dom=o.dom, ch=o.ch, boundary=o.b, first_pre_alarm_end=e, lead=o.b - e, pre_max=pre_max, post_max=post_max, alarm_window_text=txt, boundary_text=btxt)
    res["pre_boundary_alarms_far10"].append(row)
    print(f"{o.id} b={o.b} pre_alarm_end={e} lead={o.b-e} pre_max={pre_max:.2f} post_max={post_max} \n   window: {txt!r}\n   boundary+12: {btxt!r}")

# 3. tolerant recall: count a drift trace detected at +16 if first alarm end in [b - tol, b + 16], tolerating pre-boundary alarms within tol tokens (no alarm earlier than b - tol)
def tolerant(thr, tol, limit=16):
    det = 0; early = 0
    for o in DRIFT:
        st = o.s >= thr
        if (st & (o.ends < o.b - tol)).any(): early += 1; continue
        if (st & (o.ends >= o.b - tol) & (o.ends <= o.b + limit)).any(): det += 1
    return det, early
res["tolerant_recall_raw_max"] = {}
for name, thr in (("far10", thr10), ("far5", thr5)):
    for tol in (0, 4, 8, 16):
        det, early = tolerant(thr, tol)
        res["tolerant_recall_raw_max"][f"{name}_tol{tol}"] = dict(detected_16=det, early_alarm=early, recall_16=det / 35)
        print(f"raw max {name} tol={tol}: +16 detected {det}/35, early(> {tol} tokens before boundary) {early}")

# 4. per-workflow leave-one-scenario-out order-statistic threshold at alpha 0.10 and 0.05
res["per_workflow_loso_threshold"] = {}
for alpha in (0.10, 0.05):
    far = 0; pre = 0; det16 = 0; detf = 0; thrs = defaultdict(list)
    for o in TR:
        pool = [p.s.max() for p in NEG if p.wf == o.wf and p.pg != o.pg]
        thr = order_stat(pool, alpha); thrs[o.wf].append(thr)
        st = o.s >= thr
        if not o.pos:
            far += st.any()
        else:
            if (st & (o.ends < o.b)).any(): pre += 1; continue
            post = st & (o.ends >= o.b)
            if post.any():
                detf += 1
                if o.ends[post][0] <= o.b + 16: det16 += 1
    res["per_workflow_loso_threshold"][f"alpha{alpha}"] = dict(realized_far=far / 205, far_count=int(far), pre_boundary_alarms=pre, clean_recall_16=det16 / 35, clean_recall_final=detf / 35,
        n_per_workflow_pool_median=float(np.median([len([p for p in NEG if p.wf == o.wf and p.pg != o.pg]) for o in TR])),
        thr_by_workflow={k: float(np.median(v)) for k, v in thrs.items()})
    print(f"per-workflow LOSO alpha={alpha}: FAR {far}/205, pre {pre}/27, +16 {det16}/35, final {detf}/35")

# 5. raw max at far5 by domain
res["raw_max_far5_by_domain"] = {}
for o in DRIFT:
    st = o.s >= thr5; r = res["raw_max_far5_by_domain"].setdefault(o.dom, dict(n=0, clean16=0, cleanfinal=0, pre=0))
    r["n"] += 1
    if (st & (o.ends < o.b)).any(): r["pre"] += 1; continue
    post = st & (o.ends >= o.b)
    if post.any():
        r["cleanfinal"] += 1
        if o.ends[post][0] <= o.b + 16: r["clean16"] += 1
print(res["raw_max_far5_by_domain"])

# 6. resist-attack vs clean/benign non-drift max (is the routine reference contaminated by attack-arm 'mention' negatives?)
for arm in ("clean", "benign_control", "attack"):
    v = [o.s.max() for o in NEG if o.arm == arm]
    print(arm, "n", len(v), "share >= thr10:", np.mean(np.array(v) >= thr10), "share >= thr5:", np.mean(np.array(v) >= thr5))
res["non_drift_far_by_arm"] = {arm: dict(n=len([o for o in NEG if o.arm == arm]), far10=float(np.mean([o.s.max() >= thr10 for o in NEG if o.arm == arm])), far5=float(np.mean([o.s.max() >= thr5 for o in NEG if o.arm == arm]))) for arm in ("clean", "benign_control", "attack")}
# threshold calibrated on clean+benign only
thr10_cb = order_stat([o.s.max() for o in NEG if o.arm != "attack"], 0.10)
res["thr10_clean_benign_only"] = dict(threshold=thr10_cb, far_on_resist_attack=float(np.mean([o.s.max() >= thr10_cb for o in NEG if o.arm == "attack"])), far_on_clean_benign=float(np.mean([o.s.max() >= thr10_cb for o in NEG if o.arm != "attack"])))
print("thr10 from clean+benign only:", res["thr10_clean_benign_only"])
json.dump(res, open(f"{OUT}/extras_results.json", "w"), indent=1, default=str)
