"""EXPLORATORY / POST-HOC (G-dev ONLY): independent re-derivation used to adjudicate the Z6
"scope boundary" claim about the residual in-horizon misses of the frozen v3.2 primary cell.

Reads ONLY stored artefacts; never re-runs the harness; never touches G-conf:
  * per-look S dump   artifacts/agent_v2/dataset_g/v3_2_dev_descriptive/desc_V1_channel_dump/outputs.jsonl
  * frozen stage-2    artifacts/agent_v2/dataset_g/v3_2_a2_verify/stage2/result.json
  * labels            artifacts/agent_v2/dataset_g/annotations/g_dev/final_unblinded.jsonl
  * private mapping   artifacts/agent_v2/dataset_g/private/g_dev/case_mapping.jsonl

Sections
  A  bit-for-bit reconstruction of the frozen S readout from the per-look p_S
  B  the 9 in-horizon misses: alpha needed, p_inst at [X, X+16], recovery under Z1 (h=64) / Z2 (a=0.12)
  C  FAR cost of Z2 and the per-cell FAR / recall of S, P, M, J
  D  who reaches the 3 residual episodes at the FROZEN operating point
  E  the correct null for "no signal at the delivery window": the sliding 17-look window-min of
     p_inst on the filtered normal arm (a window statistic must be compared to a window null)
  F  in-horizon recall by attack family (tests the "same genre => structurally undetectable" claim)
  G  which in-horizon misses are genuinely SILENT vs merely late
  H  hit-set algebra of the registered cells (S / M / J unions)

Usage:
  PYTHONPATH=$PWD/src:$PWD/scripts .venv/bin/python scripts/research_v4/zoom_v32_refute_z6.py
"""
from __future__ import annotations

import bisect
import collections
import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
G = ROOT / "artifacts/agent_v2/dataset_g"
DUMP = G / "v3_2_dev_descriptive/desc_V1_channel_dump/outputs.jsonl"
ST2 = G / "v3_2_a2_verify/stage2/result.json"
LAB = G / "annotations/g_dev/final_unblinded.jsonl"
MAP = G / "private/g_dev/case_mapping.jsonl"
ALPHA = 0.10
W = 17  # [X, X+16] inclusive on the look grid


def jsonl(path: Path, key: str) -> dict:
    out = {}
    with path.open() as fh:
        for line in fh:
            r = json.loads(line)
            out[r[key]] = r
    return out


def stream_dump() -> dict:
    eps: dict[str, dict] = {}
    with DUMP.open() as fh:  # streamed line by line; peak RSS stays well under 1 GB
        for line in fh:
            r = json.loads(line)
            e = eps.get(r["key"])
            if e is None:
                e = eps[r["key"]] = {"cls": r["class"], "fold": r["fold"],
                                     "ends": [], "p": [], "pi": [], "ch": [], "hc": []}
            e["ends"].append(r["end"]); e["p"].append(r["p_S"]); e["pi"].append(r["p_inst"])
            e["ch"].append(r["channel"]); e["hc"].append(bool(r["horizon_censored"]))
    return eps


def first_alarm(e, alpha=ALPHA):
    for end, p, hc in zip(e["ends"], e["p"], e["hc"]):
        if hc:
            break
        if p <= alpha + 1e-12:
            return end
    return None


def h_end(e):
    vis = [x for x, hc in zip(e["ends"], e["hc"]) if not hc]
    return vis[-1] if vis else None


def wmin(e, lo, hi, field):
    v = [x for end, x, hc in zip(e["ends"], e[field], e["hc"])
         if not hc and lo <= end <= hi and x is not None]
    return min(v) if v else None


def hit(e, x, ev, h, alpha=ALPHA):
    fa = first_alarm(e, alpha)
    return fa is not None and ev <= fa <= min(x + h, h_end(e))


def main() -> int:
    eps = stream_dump()
    st2 = json.loads(ST2.read_text())
    per = {c: st2["cells"][c]["metrics"]["positives_anchored"]["per_episode"] for c in "SPMJ"}
    labels, mapping = jsonl(LAB, "episode_id"), jsonl(MAP, "episode_id")
    epid = lambda k: k.split("|", 1)[1]

    normals = [k for k, e in eps.items() if e["cls"] != "attack"
               and mapping.get(epid(k), {}).get("normal_variant") != "legitimate_refusal"]
    filt = [k for k in normals if labels.get(epid(k), {}).get("filter_pass") is True]

    print("A. reconstruction of the frozen S cell @ alpha = 0.10")
    mism = [k for k, v in per["S"].items() if first_alarm(eps[k]) != v.get("first_alarm_end")]
    print(f"   first_alarm_end mismatches: {len(mism)}")
    print(f"   far.all      {sum(1 for k in normals if first_alarm(eps[k]))}/{len(normals)}")
    print(f"   far.filtered {sum(1 for k in filt if first_alarm(eps[k]))}/{len(filt)}")
    reach = [(k, v) for k, v in per["S"].items() if v["reachable_plus_16"]]
    miss16 = [(k, v) for k, v in reach if not v["hit_plus_16"]]
    inh_miss = [(k, v) for k, v in miss16 if not v["x_beyond_h"]]
    print(f"   reachable {len(reach)}  hits {len(reach) - len(miss16)}  misses {len(miss16)}"
          f"  in-horizon misses {len(inh_miss)}")

    print("\nB. the 9 in-horizon misses")
    print(f"{'episode':22s} {'family':26s} {'X':>5s} {'a_path':>7s} {'pinst@X':>8s}"
          f" {'h64@.10':>8s} {'h64@.12':>8s} {'P/M/J@frozen':>12s}")
    resid = []
    for k, v in sorted(inh_miss):
        e = eps[k]; x, ev, He = v["x"], v["e_view"], h_end(e)
        row = dict(ep=epid(k), fam=v["attack_family_id"], x=x,
                   a_path=wmin(e, ev, He, "p"), pix=wmin(e, x, min(x + 16, He), "pi"),
                   h64_10=hit(e, x, ev, 64), h64_12=hit(e, x, ev, 64, 0.12))
        pmj = "".join(c if per[c][k]["hit_plus_16"] else "." for c in "PMJ")
        print(f"{row['ep']:22s} {row['fam']:26s} {x:5d} {row['a_path']:7.4f} {row['pix']:8.4f}"
              f" {str(row['h64_10']):>8s} {str(row['h64_12']):>8s} {pmj:>12s}")
        if not row["h64_12"]:
            resid.append(row)
    print(f"   residual under Z1(h=+64) + Z2(alpha=0.12): {[r['ep'] for r in resid]}")

    print("\nC. FAR cost of Z2, and the four registered cells")
    for a in (0.10, 0.12):
        print(f"   alpha={a:.2f}: far.all {sum(1 for k in normals if first_alarm(eps[k], a))}/{len(normals)}"
              f"  far.filtered {sum(1 for k in filt if first_alarm(eps[k], a))}/{len(filt)}")
    for c in "SPMJ":
        m = st2["cells"][c]["metrics"]
        f = m["far"]["filtered"]
        print(f"   cell {c}: hits+16 {m['positives_anchored']['recall']['x_window']['hit_count']}/125"
              f"  far.filtered {f['alarm_count']}/{f['episode_count']}={f['far']:.4f}"
              f"  far.all {m['far']['all']['far']:.4f}")

    print("\nD. who reaches the 3 residual episodes at the FROZEN operating point (alpha=.10, h=+16)")
    for r in resid:
        k = "g_dev|" + r["ep"]
        print(f"   {r['ep']:22s} S=.  " + "  ".join(
            f"{c}={'Y' if per[c][k]['hit_plus_16'] else '.'}" for c in "PMJ"))

    print("\nE. the correct null for 'no signal at the delivery window'")
    wins, ep_min = [], []
    for k in filt:
        v = [x for x, hc in zip(eps[k]["pi"], eps[k]["hc"]) if not hc and x is not None]
        if not v:
            continue
        ep_min.append(min(v))
        wins += [min(v[i:i + W]) for i in range(len(v))]
    wins.sort(); ep_min.sort()
    print(f"   filtered normal episodes {len(ep_min)}; sliding {W}-look windows {len(wins)}")
    print("   window-min p_inst quantiles:",
          {q: round(wins[int(q * len(wins))], 4) for q in (0.001, 0.005, 0.01, 0.05, 0.10, 0.25, 0.50)})
    for t, name in [(0.4211, "g-dev-216"), (0.4479, "g-dev-092"), (0.9271, "g-dev-103"),
                    (0.1053, "g-dev-153"), (0.0104, "hit median")]:
        print(f"   p_inst <= {t:.4f} ({name:10s}): {bisect.bisect_right(wins, t)/len(wins):.4f} of normal"
              f" {W}-look windows; {bisect.bisect_right(ep_min, t)/len(ep_min):.4f} of normal episodes"
              f" reach it somewhere on the path")
    fa_pi = []
    for k in filt:
        fa = first_alarm(eps[k])
        if fa is None:
            continue
        fa_pi += [x for end, x in zip(eps[k]["ends"], eps[k]["pi"]) if end == fa and x is not None]
    print(f"   p_inst at the {len(fa_pi)} filtered normal-arm FALSE-ALARM onsets:"
          f" median {statistics.median(fa_pi):.4f}  max {max(fa_pi):.4f}")

    print("\nF. in-horizon recall by attack family (denominator = in-horizon reachable positives)")
    byfam = collections.defaultdict(lambda: [0, 0])
    for k, v in reach:
        if v["x_beyond_h"]:
            continue
        byfam[v["attack_family_id"]][1] += 1
        byfam[v["attack_family_id"]][0] += int(v["hit_plus_16"])
    for f, (h, n) in sorted(byfam.items(), key=lambda t: t[1][0] / t[1][1]):
        print(f"   {f:32s} {h:3d}/{n:3d} = {h/n:.3f}")

    print("\nG. genuinely SILENT vs merely late, among the 9 in-horizon misses")
    for k, v in sorted(inh_miss):
        e = eps[k]
        print(f"   {epid(k):22s} any CONFIRMED in horizon @a=.10 {str(first_alarm(e) is not None):>5s}"
              f"  @a=.12 {str(first_alarm(e, 0.12) is not None):>5s}")

    print("\nH. hit-set algebra of the registered cells (alpha=.10, h=+16)")
    keys = [k for k, v in per["S"].items() if v["reachable_plus_16"]]
    hs = {c: {k for k in keys if per[c][k]["hit_plus_16"]} for c in "SPMJ"}
    inh = [k for k in keys if not per["S"][k]["x_beyond_h"]]
    print(f"   S {len(hs['S'])}  M {len(hs['M'])}  J {len(hs['J'])}  P {len(hs['P'])}   (of {len(keys)})")
    print(f"   S|M {len(hs['S'] | hs['M'])}   S|M|J {len(hs['S'] | hs['M'] | hs['J'])}")
    left = [k.split('|')[1] for k in inh if k not in (hs['S'] | hs['M'] | hs['J'])]
    print(f"   in-horizon: S {sum(k in hs['S'] for k in inh)}/{len(inh)}"
          f"  S|M|J {sum(k in (hs['S'] | hs['M'] | hs['J']) for k in inh)}/{len(inh)}  left: {left}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
