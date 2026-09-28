#!/usr/bin/env python
"""EXPLORATORY / DEVELOPMENT read-out of the frozen v3.2 two-stage G-dev runs.

Reads ONLY existing artefacts (no harness run, no parameter change) and prints every
number the v3.2 prereg section 11.2 / 7.3 / 9 / 14 development report has to carry.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

A2 = Path("artifacts/agent_v2/dataset_g/v3_2_a2_verify")
SM = Path("artifacts/agent_v2/dataset_g/v3_2_round2_smoke/stage2_S_vs_M")


def load(p: Path):
    with p.open() as fh:
        return json.load(fh)


def pct(x, n):
    return f"{x}/{n} = {x / n:.4f}" if n else f"{x}/{n} = n/a"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--section", default="all")
    args = ap.parse_args()
    want = args.section

    s1 = load(A2 / "stage1" / "result.json")
    mf = load(A2 / "stage1" / "threshold_manifest.json")
    s2 = load(A2 / "stage2" / "result.json")
    sm = load(SM / "result.json")

    def sec(name):
        return want in ("all", name)

    # ---------------- section 11.2 item 1 / 10: two-stage plumbing ----------
    if sec("stage"):
        print("### STAGE / MANIFEST")
        print("stage1.stage1_attack_traces_skipped =", s1["stage1_attack_traces_skipped"])
        print("stage2.attack_trace_census.count    =", s2["attack_trace_census"]["count"])
        print("stage1.attack_trace_census.sha256   =", s1["attack_trace_census"]["sha256"])
        print("stage2.attack_trace_census.sha256   =", s2["attack_trace_census"]["sha256"])
        print("manifest self sha256                =", mf["sha256"])
        print("stage2.inputs.threshold_manifest_sha256 =", s2["inputs"]["threshold_manifest_sha256"])
        print("manifest_version =", mf.get("manifest_version"), "fold_key =", mf.get("fold_key"))
        v = s2["threshold_manifest"]["verification"]
        print("verification ok =", v.get("ok"), "failed =", v.get("failed"), "checks =", len(v["checks"]))
        for c in v["checks"]:
            print("   ", c.get("check"), c.get("ok"), str(c.get("observed"))[:110])
        print("run_once_guard =", json.dumps(s2["run_once_guard"], ensure_ascii=False)[:400])
        print("seal stage1 =", json.dumps(s1["seal"], ensure_ascii=False)[:300])
        print("seal stage2 =", json.dumps(s2["seal"], ensure_ascii=False)[:300])
        print("restored_from_manifest:", {k: s2["cells"][k]["restored_from_manifest"] for k in s2["cells"]})
        print("assertions enforced/failed:", s2["assertions"]["enforced"], s2["assertions"]["failed"])
        for k in sorted(s2["assertions"]["by_statistic"]):
            rows = s2["assertions"]["by_statistic"][k]
            print("   ", k, "rows", len(rows), "all ok", all(r["ok"] for r in rows),
                  "checks", sorted({r["check"] for r in rows}))
        print()

    # ---------------- per-fold table --------------------------------------
    if sec("folds"):
        print("### PER-FOLD (per cell)")
        for cell in ("S", "P", "M", "J"):
            C = s2["cells"][cell]
            print(f"-- cell {cell}: alpha_eff_weighted =", C["fold_summary"]["alpha_eff_weighted"])
            for k in ("0", "1", "2"):
                f = C["folds"][k]
                z = mf["folds"][k]["cells"][cell]["alarm_threshold_z"]
                print(
                    f"   fold {k}: n_fit={f['n_fit']} n_cal={f['n_cal']} alpha_eff={f['alpha_eff']:.6f} "
                    f"H={f['H']} surv={f['survivors_at_H']} cens_paths={f['censored_paths']} "
                    f"cens_frac={f['horizon']['censoring_fraction']:.4f} "
                    f"ep1={f['far']['episode_index_1_share']:.4f} "
                    f"far_all={f['far']['all']['far']:.4f} far_filt={f['far']['filtered']['far']:.4f} "
                    f"x_beyond_h={f['x_beyond_h']} rank={f['attainable_rank']['rank']} "
                    f"attain_ok={f['attainability']['ok']}"
                )
                print(f"     threshold z = {json.dumps(z, ensure_ascii=False)[:200]}")
                print(f"     far counts all={f['far']['all']} filt={f['far']['filtered']}")
        print()

    # per-fold hit rate by the registered join
    if sec("foldhits"):
        print("### PER-FOLD HIT RATE (join per_episode -> scenario -> fold_assignment)")
        fa = s2["calibration_design"]["fold_assignment"]
        for cell in ("S", "P", "M", "J"):
            pe = s2["cells"][cell]["metrics"]["positives_anchored"]["per_episode"]
            agg = defaultdict(lambda: [0, 0, 0])  # reachable, hit, x_beyond_h
            for key, row in pe.items():
                scen = key.split("|", 1)[1].split("--", 1)[0]
                fold = fa.get(scen)
                if not row.get("reachable_plus_16"):
                    continue
                a = agg[fold]
                a[0] += 1
                a[1] += 1 if row.get("hit_plus_16") else 0
                a[2] += 1 if row.get("x_beyond_h") else 0
            tot = [0, 0, 0]
            for fold in sorted(agg):
                r, h, xb = agg[fold]
                tot = [tot[0] + r, tot[1] + h, tot[2] + xb]
                print(f"   {cell} fold {fold}: reachable={r} hit={h} recall={h / r:.4f} x_beyond_h={xb}")
            print(f"   {cell} TOTAL: reachable={tot[0]} hit={tot[1]} recall={tot[1] / tot[0]:.4f} x_beyond_h={tot[2]}")
        print()

    # ---------------- fold x fixture x arm ---------------------------------
    if sec("crosstab"):
        print("### FOLD x FIXTURE x ARM")
        ct = s2["calibration_design"]["fold_fixture_crosstab"]
        print(json.dumps(ct, ensure_ascii=False, indent=1))
        print("fixtures block:", json.dumps(s2["calibration_design"]["fixtures"], ensure_ascii=False)[:600])
        # held-out attack positives whose fixture appears in the reference folds
        print()

    # ---------------- primary readings -------------------------------------
    if sec("primary"):
        print("### PRIMARY CELL (S vs P, X anchor)")
        ca = s2["comparison_anchored"]
        b = ca["bootstrap"]
        print("recall_a(S) =", b["recall_a"], " recall_b(P) =", b["recall_b"], " delta =", b["point_estimate"])
        print("ci =", b["ci"], " pair_count =", b["pair_count"], " families =", b["family_count"])
        print("mcnemar =", b["mcnemar"])
        print("robust48 ci =", b["robustness_48_cluster"]["ci"], "families", b["robustness_48_cluster"]["family_count"])
        print("two_condition =", {k: ca["two_condition"][k] for k in
                                  ("ci_excludes_zero", "direction_positive", "ci_lower", "mcnemar_p")})
        print("matched_alpha =", ca["matched_alpha_secondary"]["alpha"],
              "measured_far =", ca["matched_alpha_secondary"]["measured_far"],
              "source =", ca["matched_alpha_secondary"]["source"])
        print("nominal row =", {k: v for k, v in ca["rows"]["nominal"].items() if k != "bootstrap"})
        print("nominal boot =", {k: ca["rows"]["nominal"]["bootstrap"][k] for k in
                                 ("recall_a", "recall_b", "point_estimate", "ci", "pair_count")})
        print("normal_denominator =", ca["normal_denominator"])
        print("primary_row =", ca["primary_row"])
        print()
        print("### ARCHIVAL E-anchored block comparison.* (NOT the decision column)")
        cb = s2["comparison"]["bootstrap"]
        print("recall_a", cb["recall_a"], "recall_b", cb["recall_b"], "delta", cb["point_estimate"],
              "pairs", cb["pair_count"], "ci", cb["ci"], "p", cb["mcnemar"]["p_value"])
        print()
        print("### S1 one-sample (per cell)")
        for k in ("S", "P", "M", "J"):
            h = s2["holm_s1_one_sample"][k]
            print(f"  {k}: rate={h['point_estimate']:.4f} ci={h['ci']} p={h['p_value']:.6g} n={h['n']} fam={h['family_count']}")
        print()
        print("### S2 (S vs M, round2_smoke/stage2_S_vs_M)")
        cm = sm["comparison_anchored"]
        print("delta", cm["bootstrap"]["point_estimate"], "ci", cm["bootstrap"]["ci"],
              "p", cm["bootstrap"]["mcnemar"]["p_value"], "pairs", cm["bootstrap"]["pair_count"],
              "recall_a", cm["bootstrap"]["recall_a"], "recall_b", cm["bootstrap"]["recall_b"])
        print("mcnemar", cm["bootstrap"]["mcnemar"])
        print("two_condition", {k: cm["two_condition"][k] for k in
                                ("ci_excludes_zero", "direction_positive", "ci_lower", "mcnemar_p")})
        print("matched alpha_M", cm["matched_alpha_secondary"]["alpha"])
        print("robust48", cm["bootstrap"]["robustness_48_cluster"]["ci"])
        print()
        print("### S-J injection pairing")
        for k in ("S", "P", "M", "J"):
            ip = s2["injection_pairing"][k]
            pu, sf = ip["primary_unfiltered"], ip["sensitivity_filtered_negatives"]
            print(f"  {k}: pairs={pu['pair_count']} delta={pu['point_estimate']:.4f} ci={pu['ci']} "
                  f"p={pu['mcnemar']['p_value']:.4g} | filtered pairs={sf['pair_count']} "
                  f"delta={sf['point_estimate']:.4f} ci={sf['ci']} p={sf['mcnemar']['p_value']:.4g}")
        print("pairing census (S):", json.dumps(s2["injection_pairing"]["S"]["pairing"], ensure_ascii=False))
        print()
        print("### positive families")
        for k in ("S", "P", "M", "J"):
            pf = s2["positive_families"][k]
            print(f"  {k}: family_count={pf['family_count']} min={pf['min_family_size']} dropped={pf['dropped_families']}")
        print("S positives_by_family:", json.dumps(s2["positive_families"]["S"]["positives_by_family"], ensure_ascii=False))
        print()

    # ---------------- per-cell recall / far summary ------------------------
    if sec("cells"):
        print("### PER-CELL SUMMARY")
        for k in ("S", "P", "M", "J"):
            m = s2["cells"][k]["metrics"]
            pa = m["positives_anchored"]
            xw = pa["recall"]["x_window"]
            print(f"-- {k}: x_window recall={xw['recall']:.4f} ({xw['hit_count']}/{xw['reachable_count']}) "
                  f"positives={pa['count']} unreachable={xw['unreachable_count']} x_beyond_h={pa['reachability']['x_beyond_h']}")
            print(f"   far all={m['far']['all']['far']:.4f} ({m['far']['all']['alarm_count']}/{m['far']['all']['episode_count']}) "
                  f"filtered={m['far']['filtered']['far']:.4f} ({m['far']['filtered']['alarm_count']}/{m['far']['filtered']['episode_count']})")
            print(f"   clean all={m['far']['clean']['all']['far']:.4f} bc all={m['far']['benign_control']['all']['far']:.4f} "
                  f"bc-clean={m['far']['benign_control_minus_clean']:.4f} bl-clean={m['far']['benign_lexical_minus_clean']:.4f}")
            print(f"   tertile FAR all: {{s:{m['far']['length_tertile']['short']['far']:.4f}, "
                  f"m:{m['far']['length_tertile']['medium']['far']:.4f}, l:{m['far']['length_tertile']['long']['far']:.4f}}} "
                  f"worst={m['far']['worst_length_tertile']}")
            lt = m['far']['length_tertile_filtered']
            print(f"   tertile FAR filtered: {{s:{lt['short']['far']:.4f} ({lt['short']['alarm_count']}/{lt['short']['episode_count']}), "
                  f"m:{lt['medium']['far']:.4f} ({lt['medium']['alarm_count']}/{lt['medium']['episode_count']}), "
                  f"l:{lt['long']['far']:.4f} ({lt['long']['alarm_count']}/{lt['long']['episode_count']})}}")
            sa = m["classes"]["silent_attack"]
            sall = m["classes"]["silent_all_attack_arm_episodes"]
            print(f"   silent_attack={sa['far']:.4f} ({sa['alarm_count']}/{sa['episode_count']}) "
                  f"excluded_pre_injection={sa['excluded_pre_injection_episodes']} | "
                  f"silent_all={sall['far']:.4f} ({sall['alarm_count']}/{sall['episode_count']})")
            print(f"   over_refusal={m['classes']['over_refusal']['far']:.4f} "
                  f"({m['classes']['over_refusal']['alarm_count']}/{m['classes']['over_refusal']['episode_count']}) "
                  f"legit_refusal={m['classes']['legitimate_refusal']['far']:.4f} "
                  f"({m['classes']['legitimate_refusal']['alarm_count']}/{m['classes']['legitimate_refusal']['episode_count']})")
            ep = m["endpoint"]
            print(f"   endpoints emitted={ep['emitted_endpoints']} eligible={ep['eligible_endpoints']} "
                  f"censored={ep['censored_endpoints']} alarm_endpoints={ep['alarm_endpoints']} "
                  f"alarm_onsets={ep['alarm_onsets']} per1000={ep['alarm_onsets_per_1000_eligible']:.4f} "
                  f"horizon_censored_eps={ep['horizon_censored_episodes']}")
            c = s2["cells"][k]["cost"]
            print(f"   cost fit_s={c['fit_seconds']:.4f} score_s={c['scoring_seconds']:.3f} "
                  f"total_s={c['total_seconds']:.3f} per1000={c['seconds_per_1000_endpoints']:.5f} "
                  f"scored_endpoints={c['scored_endpoints']}")
            print(f"   by_x_beyond_h={json.dumps(pa['by_x_beyond_h'], ensure_ascii=False)}")
            print(f"   by_channel={json.dumps(pa['by_channel'], ensure_ascii=False)}")
            print(f"   by_domain_group={json.dumps(pa['by_domain_group'], ensure_ascii=False)}")
            print(f"   by_wording_tier={json.dumps(pa['by_wording_tier'], ensure_ascii=False)}")
            print(f"   by_trajectory_class={json.dumps(pa['by_trajectory_class'], ensure_ascii=False)}")
            print(f"   early_than_anchor={json.dumps(pa['early_than_anchor'], ensure_ascii=False)} "
                  f"latency_median={pa['latency_median']} pre_window_alarm_rate={pa['pre_window_alarm_rate']}")
            print(f"   E-anchored positives: count={m['positives']['count']} "
                  f"recall={json.dumps({kk: vv['recall'] for kk, vv in m['positives']['recall'].items()}, ensure_ascii=False)} "
                  f"pre_onset={m['positives']['pre_onset_rate']} latency_median={m['positives']['latency_median']}")
            print(f"   E recall detail={json.dumps(m['positives']['recall'], ensure_ascii=False)[:600]}")
            print(f"   injection_presence: {json.dumps({kk: vv for kk, vv in m['injection_presence'].items() if kk not in ('positives','negatives')}, ensure_ascii=False)}")
            print(f"     positives={json.dumps({kk: vv for kk, vv in m['injection_presence']['positives'].items() if kk != 'per_episode'}, ensure_ascii=False)[:500]}")
            print(f"     negatives={json.dumps({kk: vv for kk, vv in m['injection_presence']['negatives'].items() if kk != 'per_episode'}, ensure_ascii=False)[:400]}")
            hy = m["hysteresis"]
            print(f"   hysteresis entries={hy['entries']} exits={hy['exits']} eps_with_entry={hy['episodes_with_entry']} "
                  f"eps_with_exit={hy['episodes_with_exit']} final_states={json.dumps(hy['final_states'], ensure_ascii=False)} "
                  f"earliest_decision_end_median={hy['earliest_decision_end_median']}")
            print(f"   hysteresis by_trajectory_class={json.dumps(hy['by_trajectory_class'], ensure_ascii=False)[:700]}")
            print(f"   temporal={json.dumps(m['temporal'], ensure_ascii=False)[:700]}")
            print(f"   session far={m['session']['session_far']} normal_sessions_with_alarm={m['session']['normal_sessions_with_alarm']} "
                  f"normal_session_count={m['session']['normal_session_count']} gate_f7={json.dumps(m['session']['gate_f7'], ensure_ascii=False)[:400]}")
            print(f"   pool={json.dumps(m['pool'], ensure_ascii=False)}")
            print(f"   anchor_sensitivity(x)={json.dumps(pa['anchor_sensitivity'], ensure_ascii=False)[:400]}")
            print(f"   positives.excluded={json.dumps(pa['excluded'], ensure_ascii=False)}")
        print()

    # ---------------- gates -------------------------------------------------
    if sec("gates"):
        print("### GATES (per cell, verbatim from result.json)")
        for k in ("S", "P", "M", "J"):
            print("--", k, "alpha_eff_weighted", s2["gates"][k]["alpha_eff_weighted"])
            for g in s2["gates"][k]["gates"]:
                print("   ", g["status"], g["gate"], "value=", g.get("value"), "threshold=", g.get("threshold"),
                      {kk: vv for kk, vv in g.items() if kk not in ("status", "gate", "value", "threshold", "rule", "note")})
        print()

    # ---------------- tertiles ---------------------------------------------
    if sec("tertiles"):
        print("### LENGTH TERTILES")
        print(json.dumps(s2["calibration_design"]["length_tertiles"], ensure_ascii=False, indent=1))
        print("manifest block:", json.dumps(mf["length_tertiles"], ensure_ascii=False)[:600])
        print("S far.length_tertile_definition:",
              json.dumps(s2["cells"]["S"]["metrics"]["far"]["length_tertile_definition"], ensure_ascii=False))
        print()

    # ---------------- alarm-set overlap ------------------------------------
    if sec("jaccard"):
        print("### ALARM-SET OVERLAP BETWEEN CELLS (episode level, from summaries)")
        alarms = {}
        for k in ("S", "P", "M", "J"):
            alarms[k] = {r["key"] for r in s2["cells"][k]["metrics"]["summaries"] if r["alarm"]}
        keys = list(alarms)
        for i, a in enumerate(keys):
            for b in keys[i + 1:]:
                inter = len(alarms[a] & alarms[b])
                union = len(alarms[a] | alarms[b])
                print(f"   {a} vs {b}: |A|={len(alarms[a])} |B|={len(alarms[b])} inter={inter} union={union} jaccard={inter / union:.4f}")
        # normal-arm only
        norm = {r["key"] for r in s2["cells"]["S"]["metrics"]["summaries"]
                if r["arm"] in ("clean", "benign_control", "benign_lexical")}
        for i, a in enumerate(keys):
            for b in keys[i + 1:]:
                A, B = alarms[a] & norm, alarms[b] & norm
                u = len(A | B)
                print(f"   NORMAL-ONLY {a} vs {b}: |A|={len(A)} |B|={len(B)} inter={len(A & B)} jaccard={(len(A & B) / u) if u else float('nan'):.4f}")
        print()

    # ---------------- false-alarm anatomy from summaries -------------------
    if sec("fa"):
        print("### FALSE-ALARM ANATOMY (S cell, normal arms, from metrics.summaries)")
        fa_assign = s2["calibration_design"]["fold_assignment"]
        cut = s2["calibration_design"]["length_tertiles"]["cutpoints"]
        rows = [r for r in s2["cells"]["S"]["metrics"]["summaries"]
                if r["arm"] in ("clean", "benign_control", "benign_lexical")]
        alarming = [r for r in rows if r["alarm"]]
        print("normal-arm episodes in summaries:", len(rows), " alarming:", len(alarming))

        def tertile(n):
            return "short" if n <= cut[0] else ("medium" if n <= cut[1] else "long")

        by_arm = Counter(r["arm"] for r in alarming)
        by_wf = Counter(r["workflow"] for r in alarming)
        by_dom = Counter(r["domain"] for r in alarming)
        by_ter = Counter(tertile(r["token_count"]) for r in alarming)
        by_fold = Counter(fa_assign.get(r["pair_group_id"]) for r in alarming)
        by_temporal = Counter(r["temporal_state"] for r in alarming)
        print("by arm:", dict(by_arm))
        print("by workflow:", dict(by_wf))
        print("by domain:", dict(by_dom))
        print("by tertile:", dict(by_ter))
        print("by fold:", dict(by_fold))
        print("by temporal_state:", dict(by_temporal))
        print("base rates -- all normal by workflow:", dict(Counter(r["workflow"] for r in rows)))
        print("base rates -- all normal by domain:", dict(Counter(r["domain"] for r in rows)))
        print("base rates -- all normal by tertile:", dict(Counter(tertile(r["token_count"]) for r in rows)))
        print()
        print("alarming normal episodes (key, arm, workflow, domain, tokens, tertile, first_alarm_end, onsets, temporal):")
        for r in sorted(alarming, key=lambda r: r["key"]):
            print(f"   {r['key']:44s} {r['arm']:15s} {r['workflow']:26s} {r['domain']:20s} "
                  f"tok={r['token_count']:4d} {tertile(r['token_count']):6s} first={r['first_alarm_end']} "
                  f"onsets={r['alarm_onsets']} ends={r['alarm_endpoints']} temporal={r['temporal_state']} "
                  f"fold={fa_assign.get(r['pair_group_id'])}")
        print()

    # ---------------- code stratum at X -----------------------------------
    if sec("code"):
        print("### CODE STRATUM @ X")
        for k in ("S", "P", "M", "J"):
            pa = s2["cells"][k]["metrics"]["positives_anchored"]
            print(f"  {k} by_domain_group: {json.dumps(pa['by_domain_group'], ensure_ascii=False)}")
        # code-domain normal FAR (proxy arm)
        rows = [r for r in s2["cells"]["S"]["metrics"]["summaries"]
                if r["arm"] in ("clean", "benign_control", "benign_lexical")]
        for dom in sorted({r["domain"] for r in rows}):
            sub = [r for r in rows if r["domain"] == dom]
            al = sum(1 for r in sub if r["alarm"])
            print(f"   normal-arm FAR(all) domain={dom}: {pct(al, len(sub))}")
        print()


if __name__ == "__main__":
    main()
