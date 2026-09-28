#!/usr/bin/env python
"""EXPLORATORY / DEVELOPMENT: alarm anatomy from a descriptive V1 outputs dump.

Streams outputs.jsonl (never loads it whole), and joins with the frozen
a2_verify stage-2 result.json episode metadata.  Produces:
  * channel distribution of alarm ONSETS and of alarm endpoints
  * attribution top-3 (layer, expert) coordinate census
  * per-episode first CONFIRMED look with its channel (false-alarm anatomy)
  * hysteresis / p_inst state counts by arm
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

DUMP = Path("artifacts/agent_v2/dataset_g/v3_2_dev_descriptive/desc_V1_channel_dump/outputs.jsonl")
A2 = Path("artifacts/agent_v2/dataset_g/v3_2_a2_verify/stage2/result.json")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", type=Path, default=DUMP)
    ap.add_argument("--onsets-out", type=Path, default=None)
    args = ap.parse_args()

    with A2.open() as fh:
        res = json.load(fh)
    meta = {r["key"]: r for r in res["cells"]["S"]["metrics"]["summaries"]}
    fold_of = res["calibration_design"]["fold_assignment"]
    cut = res["calibration_design"]["length_tertiles"]["cutpoints"]

    prev_state: dict[str, str] = {}
    onset_channel = Counter()
    onset_channel_by_arm = defaultdict(Counter)
    endpoint_channel = Counter()
    alarm_endpoint_channel = Counter()
    coord = Counter()
    coord_by_arm = defaultdict(Counter)
    layer_c = Counter()
    first_onset: dict[str, dict] = {}
    onsets: list[dict] = []
    state_counts = Counter()
    hyst_counts = Counter()
    hyst_by_arm = defaultdict(Counter)
    rows = 0

    with args.dump.open() as fh:
        for line in fh:
            r = json.loads(line)
            rows += 1
            key = r["key"]
            arm = r["arm"]
            ch = r.get("channel")
            endpoint_channel[ch] += 1
            state_counts[r["state"]] += 1
            hyst_counts[r["hysteresis_state"]] += 1
            hyst_by_arm[arm][r["hysteresis_state"]] += 1
            if r["state"] == "CONFIRMED":
                alarm_endpoint_channel[ch] += 1
                if prev_state.get(key) != "CONFIRMED":
                    onset_channel[ch] += 1
                    onset_channel_by_arm[arm][ch] += 1
                    rec = {
                        "key": key, "arm": arm, "channel": ch, "end": r["end"],
                        "fold": r["fold"], "class": r["class"],
                        "evidence_window": r["evidence_window"],
                        "p_S": r["p_S"], "hysteresis_state": r["hysteresis_state"],
                        "temporal_state": r["temporal_state"],
                        "top_coordinates": r.get("top_coordinates"),
                    }
                    onsets.append(rec)
                    first_onset.setdefault(key, rec)
                    for c in r.get("top_coordinates") or ():
                        coord[(c["layer"], c["expert"])] += 1
                        coord_by_arm[arm][(c["layer"], c["expert"])] += 1
                        layer_c[c["layer"]] += 1
            prev_state[key] = r["state"]

    print("rows streamed:", rows, " episodes:", len(prev_state))
    print("endpoint state counts:", dict(state_counts))
    print("look-level hysteresis states:", dict(hyst_counts))
    print()
    print("### CHANNEL DISTRIBUTION")
    print("all emitted endpoints by channel:", dict(endpoint_channel))
    print("CONFIRMED endpoints by channel  :", dict(alarm_endpoint_channel))
    print("alarm ONSETS by channel         :", dict(onset_channel), " total", sum(onset_channel.values()))
    for arm in sorted(onset_channel_by_arm):
        print(f"   onsets[{arm}] :", dict(onset_channel_by_arm[arm]),
              "total", sum(onset_channel_by_arm[arm].values()))
    print()
    print("### ATTRIBUTION top-3 coordinates over alarm onsets")
    print("distinct (layer,expert) seen:", len(coord), " total slots:", sum(coord.values()))
    for (l, e), n in coord.most_common(15):
        print(f"   layer {l:2d} expert {e:2d}: {n}")
    print("by layer:", dict(sorted(layer_c.items())))
    print("normal-arm only top coordinates:")
    normal = Counter()
    for arm in ("clean", "benign_control", "benign_lexical"):
        normal.update(coord_by_arm.get(arm, Counter()))
    for (l, e), n in normal.most_common(10):
        print(f"   layer {l:2d} expert {e:2d}: {n}")
    print("attack-arm only top coordinates:")
    for (l, e), n in coord_by_arm.get("attack", Counter()).most_common(10):
        print(f"   layer {l:2d} expert {e:2d}: {n}")
    print()

    def tertile(n):
        return "short" if n <= cut[0] else ("medium" if n <= cut[1] else "long")

    print("### FIRST ALARM ONSET on NORMAL arms (false-alarm anatomy)")
    fa = [v for k, v in first_onset.items()
          if meta[k]["arm"] in ("clean", "benign_control", "benign_lexical")]
    print("normal episodes with an onset:", len(fa))
    print("by channel of the FIRST CONFIRMED look:",
          dict(Counter(v["channel"] for v in fa)))
    print("by arm:", dict(Counter(meta[v['key']]['arm'] for v in fa)))
    print("by workflow:", dict(Counter(meta[v["key"]]["workflow"] for v in fa)))
    print("by domain:", dict(Counter(meta[v["key"]]["domain"] for v in fa)))
    print("by tertile:", dict(Counter(tertile(meta[v["key"]]["token_count"]) for v in fa)))
    print("by fold:", dict(Counter(v["fold"] for v in fa)))
    print()
    print("key | arm | workflow | domain | tokens | tertile | fold | first_end | channel | p_S | evidence_window | temporal")
    for v in sorted(fa, key=lambda v: v["key"]):
        m = meta[v["key"]]
        print(f"{v['key']:44s} | {m['arm']:15s} | {m['workflow']:26s} | {m['domain']:20s} | "
              f"{m['token_count']:4d} | {tertile(m['token_count']):6s} | {v['fold']} | {v['end']:4d} | "
              f"{v['channel']:10s} | {v['p_S']:.5f} | {v['evidence_window']} | {v['temporal_state']}")
    print()
    print("### FIRST ALARM ONSET on ATTACK arms, channel split")
    at = [v for k, v in first_onset.items() if meta[k]["arm"] == "attack"]
    print("attack episodes with an onset:", len(at),
          " by channel:", dict(Counter(v["channel"] for v in at)))
    if args.onsets_out:
        with args.onsets_out.open("w") as fh:
            for rec in onsets:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print("wrote", args.onsets_out, len(onsets), "onset rows")


if __name__ == "__main__":
    main()
