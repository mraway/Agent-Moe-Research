"""EXPLORATORY (G-dev only): text audit of the v3.2 primary cell's normal-arm false alarms.

Pulls the verbatim ``channel_text[final]`` of each audited case out of the blind packet
renders (``packets/render/g_dev/batch_*.md``), keyed through the private case mapping, and
derives the text features the genre-switch question needs (markdown table, enumerated KB
rules, headings, KB article ids, how early the enumeration starts).  Reads no routing and
no G-conf.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RENDER = ROOT / "artifacts/agent_v2/dataset_g/packets/render/g_dev"
MAPPING = ROOT / "artifacts/agent_v2/dataset_g/private/g_dev/case_mapping.jsonl"

CASE = re.compile(r"^## CASE (\S+)\s", re.M)
CHAN = re.compile(r"^#### channel_text\[(\w+)\] \((\d+) chars\)\n```text\n(.*?)\n```", re.M | re.S)


def load_cases() -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for path in sorted(RENDER.glob("batch_*.md")):
        text = path.read_text()
        marks = [(m.group(1), m.start()) for m in CASE.finditer(text)]
        for i, (case_id, start) in enumerate(marks):
            stop = marks[i + 1][1] if i + 1 < len(marks) else len(text)
            block = text[start:stop]
            out[case_id] = {chan: body for chan, _n, body in CHAN.findall(block)}
    return out


def features(final: str) -> dict:
    lines = final.splitlines()
    enum = [i for i, ln in enumerate(lines) if re.match(r"^\s*(\d+[.)]|[-*])\s", ln)]
    first_enum_char = None
    if enum:
        first_enum_char = sum(len(ln) + 1 for ln in lines[: enum[0]])
    return {
        "chars": len(final),
        "table_rows": sum(1 for ln in lines if ln.count("|") >= 2),
        "enumerated_lines": len(enum),
        "headings": sum(1 for ln in lines if ln.lstrip().startswith("#")),
        "bold_headers": len(re.findall(r"^\s*\*\*[^*]+\*\*\s*$", final, re.M)),
        "kb_ids": sorted(set(re.findall(r"KB[‑–-][A-Z0-9‑–-]+", final))),
        "first_enum_char": first_enum_char,
        "first_120": final[:120].replace("\n", " / "),
    }


def main(episode_file: str, out_path: str) -> None:
    mapping = {}
    with MAPPING.open() as handle:
        for line in handle:
            d = json.loads(line)
            mapping[d["episode_id"]] = d
    cases = load_cases()
    eids = [ln.strip() for ln in open(episode_file) if ln.strip()]
    out = {}
    for eid in eids:
        mp = mapping.get(eid)
        if mp is None:
            out[eid] = {"error": "no mapping"}
            continue
        block = cases.get(mp["case_id"], {})
        fin = block.get("final", "")
        rec = features(fin)
        rec["case_id"] = mp["case_id"]
        rec["analysis_chars"] = len(block.get("analysis", ""))
        rec["commentary_chars"] = len(block.get("commentary", ""))
        rec["final_text"] = fin
        out[eid] = rec
    json.dump(out, open(out_path, "w"), indent=1)
    for eid, r in out.items():
        if "error" in r:
            print(eid, r)
            continue
        print(f"{eid:38s} chars={r['chars']:5d} tbl={r['table_rows']:3d} enum={r['enumerated_lines']:3d} "
              f"hdr={r['headings']:2d} bold={r['bold_headers']:2d} kb={len(r['kb_ids'])} "
              f"first_enum@{r['first_enum_char']} :: {r['first_120'][:100]}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
