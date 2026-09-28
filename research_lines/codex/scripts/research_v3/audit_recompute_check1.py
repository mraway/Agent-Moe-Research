"""Stage 1: contract consistency of outputs.jsonl (fusion, states, p lattice, n_ref)."""
import json, sys
from collections import Counter, defaultdict
sys.path.insert(0, "/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363/scripts/research_v3")
from audit_recompute_lib import *

RUNS = {"b2": "final_b2_both", "b1": "final_b1_both", "h384": "final_h384_both", "c1_heldout": "final_c1_heldout_C1"}

for target, run in RUNS.items():
    cells = load_cell(run)
    print(f"===== {target} ({run})")
    for (col, evalset), traces in sorted(cells.items()):
        n_end = sum(len(b["ends"]) for b in traces.values())
        # fusion identity
        bad_fuse = 0; bad_state = 0; bad_grid = 0; maxdev = 0.0
        for b in traces.values():
            for i in range(len(b["ends"])):
                vals = []
                for ch, key in (("S","pS"),("M","pM"),("J","pJ")):
                    v = b[key][i]
                    if v is not None:
                        vals.append(v / WEIGHTS[ch])
                pf = min(1.0, min(vals)) if vals else 1.0
                if abs(pf - b["pf"][i]) > 1e-12:
                    bad_fuse += 1; maxdev = max(maxdev, abs(pf - b["pf"][i]))
                st = "CONFIRMED" if b["pf"][i] <= 0.10 else ("PROVISIONAL" if b["pf"][i] <= 0.25 else "SILENT")
                if st != b["state"][i]:
                    bad_state += 1
            if b["ends"] != sorted(b["ends"]) or b["k"] != list(range(len(b["k"]))) or b["ends"][0] != 7:
                bad_grid += 1
        nref = {ch: infer_n_reference(cells, col, evalset, ch) for ch in "SMJ"}
        halves = Counter(b["half"] for b in traces.values())
        print(f"  [{col}|{evalset}] traces={len(traces)} endpoints={n_end} halves={dict(halves)}")
        print(f"      fusion mismatches={bad_fuse} (max dev {maxdev:g}) state mismatches={bad_state} grid anomalies={bad_grid}")
        print(f"      n_reference recovered from p lattice: {nref}")
        # censoring
        cens_tr = sum(1 for b in traces.values() if any(b["hc"]))
        cens_ep = sum(sum(b["hc"]) for b in traces.values())
        # censoring must be a suffix
        suffix_ok = all(b["hc"] == sorted(b["hc"]) for b in traces.values())
        # frozen decision past horizon?
        frozen_ok = True
        for b in traces.values():
            if any(b["hc"]):
                first = b["hc"].index(True)
                if first == 0: continue
                ref = b["pf"][first-1]
                if any(abs(b["pf"][i]-ref) > 1e-12 for i in range(first, len(b["pf"]))):
                    frozen_ok = False
        print(f"      horizon-censored traces={cens_tr} endpoints={cens_ep} suffix_only={suffix_ok} frozen_after_Kcal={frozen_ok}")
        cls = Counter(b["class"] for b in traces.values())
        print(f"      class counts={dict(cls)}")
