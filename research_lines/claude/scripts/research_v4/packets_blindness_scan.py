#!/usr/bin/env python3
"""Post-hoc blindness audit of a written blind annotation packet.

    packets_blindness_scan.py --packet artifacts/agent_v2/dataset_g/packets/g_dev/packet.jsonl \
        --config configs/dataset_g/g_dev.json --attack-arm \
        --out artifacts/agent_v2/dataset_g/packets/g_dev/blindness_scan.json

The builder already asserts blindness while it writes (``build._assert_blind``
over ``schema.FORBIDDEN_PACKET_KEYS`` and, for attack-bearing runs,
``schema.ATTACK_FORBIDDEN_PACKET_KEYS``).  This script is the independent second
pass over the *file that was actually written*:

1. the same forbidden-**key** walk, re-run on the parsed rows;
2. a literal-**string** scan for every value that would identify the arm or the
   attack design -- scenario ids, arm names, wording tiers, attack channels,
   attack target domains and families, and the words ``injection`` / ``attack``
   / ``benign_control`` / ``legitimate_refusal`` / ``benign_lexical``.

The string scan reports each hit with the JSON path it sits at, and splits the
hits into two classes:

``metadata``
    anywhere the packet speaks in its own voice -- field names, ids, task brief
    enumerations, episode facts.  **A hit here is a blindness failure**: the
    expected count is zero.

``model_visible_content``
    the text the model itself read or wrote: ``conversation[*].content`` and the
    tool-result subtree, ``system_prompt``, ``channel_text``, and the per-message
    and per-token text of ``channel_messages``.  Hits here are *not* leaks --
    the annotation guideline (section 1, A1) requires the injected text to be
    present verbatim as content, and an attack that says "write a short Python
    function" legitimately contains the target-domain word.  They are reported
    so the run log can account for every occurrence rather than hide it.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from agent_v3.packets import build, schema  # noqa: E402


#: JSON paths (with list indices collapsed to ``[]``) whose *subtree* is text the
#: model read or produced, and may therefore contain any string at all.
CONTENT_PREFIXES = (
    "system_prompt",
    "instructions",
    "channel_text",
    "channel_messages[].text",
    "channel_messages[].tokens[].text",
    "conversation[].content",
    "conversation[].result",
    "conversation[].arguments",
    "conversation[].raw_arguments",
    "task.requirements[].query_hint",
    "task.completion_evidence",
)


def literal_set(config: Mapping[str, Any], extra: Iterable[str] = ()) -> dict[str, list[str]]:
    """Every string whose presence outside model-visible content would unblind."""

    scenarios = list(config.get("scenarios", ()))

    def values(getter) -> list[str]:
        return sorted({str(v) for v in (getter(s) for s in scenarios) if v})

    groups: dict[str, list[str]] = {
        "scenario_id": values(lambda s: s.get("base_task_id"))
        + values(lambda s: s.get("pair_group_id")),
        "arm_name": ["clean", "benign_control", "attack"],
        "condition_id": ["agent-v3-clean", "agent-v3-benign_control", "agent-v3-attack"],
        "wording_tier": values(lambda s: s.get("wording_tier")),
        "attack_channel": values(lambda s: (s.get("arms", {}).get("attack") or {}).get("channel")),
        "attack_target_domain": values(lambda s: (s.get("factory") or {}).get("target_domain")),
        "attack_family": values(lambda s: (s.get("factory") or {}).get("attack_family"))
        + values(lambda s: (s.get("arms", {}).get("attack") or {}).get("attack_family_id")),
        "domain_group": values(lambda s: s.get("domain_group")),
        "scenario_role": values(lambda s: (s.get("factory") or {}).get("scenario_role")),
        "normal_variant": values(lambda s: (s.get("factory") or {}).get("normal_variant")),
        "cell_id": values(lambda s: (s.get("factory") or {}).get("cell_id")),
        "supplement_layer": values(lambda s: (s.get("factory") or {}).get("supplement_layer")),
        "analysis_group_id": values(lambda s: s.get("analysis_group_id")),
        "split_group_id": values(lambda s: s.get("split_group_id")),
        "named_word": [
            "injection",
            "attack",
            "benign_control",
            "legitimate_refusal",
            "benign_lexical",
        ],
        "subset_identity": [
            str(config.get("experiment_id") or ""),
            str(config.get("dataset_role") or ""),
        ],
        "extra": sorted(set(extra)),
    }
    return {name: sorted({v for v in vals if v}) for name, vals in groups.items() if any(vals)}


def walk(node: Any, path: str = "") -> Iterable[tuple[str, str]]:
    """Yield (collapsed json path, string value) for every string leaf."""

    if isinstance(node, Mapping):
        for key, value in node.items():
            child = f"{path}.{key}" if path else str(key)
            yield from walk(value, child)
    elif isinstance(node, (list, tuple)):
        for value in node:
            yield from walk(value, f"{path}[]")
    elif isinstance(node, str):
        yield path, node
    elif node is not None:
        yield path, str(node)


def is_content_path(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix + ".") or path.startswith(prefix + "[")
               for prefix in CONTENT_PREFIXES)


def scan_rows(
    rows: list[Mapping[str, Any]], literals: Mapping[str, list[str]], *, attack_arm: bool
) -> dict[str, Any]:
    key_failures: list[str] = []
    for row in rows:
        try:
            build._assert_blind(row, attack_arm=attack_arm)
        except Exception as error:  # noqa: BLE001 - the message is the finding
            key_failures.append(f"{row.get('case_id')}: {error}")

    metadata_hits: dict[str, Counter[str]] = defaultdict(Counter)
    content_hits: dict[str, Counter[str]] = defaultdict(Counter)
    metadata_examples: dict[str, list[dict[str, str]]] = defaultdict(list)
    content_paths: dict[str, Counter[str]] = defaultdict(Counter)

    flat = [
        (name, literal, literal.casefold())
        for name, values in literals.items()
        for literal in values
    ]
    for row in rows:
        for path, value in walk(row, ""):
            lowered = value.casefold()
            content = is_content_path(path)
            for name, literal, needle in flat:
                if needle not in lowered:
                    continue
                key = f"{name}::{literal}"
                if content:
                    content_hits[name][literal] += 1
                    content_paths[name][path] += 1
                else:
                    metadata_hits[name][literal] += 1
                    if len(metadata_examples[key]) < 3:
                        metadata_examples[key].append(
                            {
                                "case_id": str(row.get("case_id")),
                                "path": path,
                                "value": value[:200],
                            }
                        )

    return {
        "rows": len(rows),
        "forbidden_key_walk": {
            "keys_checked": len(schema.FORBIDDEN_PACKET_KEYS)
            + (len(schema.ATTACK_FORBIDDEN_PACKET_KEYS) if attack_arm else 0),
            "attack_arm_keys_enforced": bool(attack_arm),
            "failures": key_failures,
            "passed": not key_failures,
        },
        "literal_scan": {
            "literals_checked": len(flat),
            "metadata_position_hits": sum(sum(c.values()) for c in metadata_hits.values()),
            "metadata_position_by_group": {
                name: dict(counter.most_common()) for name, counter in sorted(metadata_hits.items())
            },
            "metadata_position_examples": {k: v for k, v in sorted(metadata_examples.items())},
            "model_visible_content_hits": sum(sum(c.values()) for c in content_hits.values()),
            "model_visible_content_by_group": {
                name: dict(counter.most_common()) for name, counter in sorted(content_hits.items())
            },
            "model_visible_content_paths": {
                name: dict(counter.most_common(8)) for name, counter in sorted(content_paths.items())
            },
            "passed": sum(sum(c.values()) for c in metadata_hits.values()) == 0,
        },
    }


#: Keys the *harness* writes into a tool result before the model reads it.  They
#: are model-visible input, so the packet must keep them verbatim (design
#: section 3: the annotator reads exactly what the model read), but they say
#: "this tool result was perturbed" and therefore separate the clean arm from
#: the two injected arms on the tool_output channel.  Counted and reported
#: rather than removed.
IN_CONTENT_PERTURBATION_KEYS = (
    "experiment_perturbation",
    "external_note_provenance",
)


def perturbation_markers(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    """How many rows carry a harness perturbation marker inside tool-result text."""

    per_key: Counter[str] = Counter()
    applied_true = 0
    rows_with_any = 0
    for row in rows:
        found = False
        applied = False
        for message in row.get("conversation", ()):
            content = message.get("content")
            if not isinstance(content, (dict, list)):
                continue
            for path, value in walk(content, "content"):
                for key in IN_CONTENT_PERTURBATION_KEYS:
                    if f".{key}." in path or path.endswith(f".{key}"):
                        per_key[key] += 1
                        found = True
                        if path.endswith(".applied") and str(value) == "True":
                            applied = True
        rows_with_any += int(found)
        applied_true += int(applied)
    return {
        "note": (
            "model-visible harness metadata inside the tool result: kept verbatim "
            "because the model read it, but it marks the tool result as perturbed"
        ),
        "keys": list(IN_CONTENT_PERTURBATION_KEYS),
        "rows_with_a_marker": rows_with_any,
        "rows_with_applied_true": applied_true,
        "leaf_hits_by_key": dict(per_key),
    }


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--attack-arm", action="store_true")
    parser.add_argument("--extra-string", action="append", default=[])
    parser.add_argument("--out", type=Path)
    return parser.parse_args()


def main() -> int:
    args = _args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    rows = [
        json.loads(line)
        for line in args.packet.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    literals = literal_set(config, args.extra_string)
    report = scan_rows(rows, literals, attack_arm=args.attack_arm)
    report["in_content_perturbation_markers"] = perturbation_markers(rows)
    report["packet"] = str(args.packet)
    report["literal_groups"] = {name: len(values) for name, values in literals.items()}
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    keys = report["forbidden_key_walk"]
    literal = report["literal_scan"]
    print(
        f"{args.packet}: {report['rows']} rows; forbidden-key walk "
        f"{'PASS' if keys['passed'] else 'FAIL'} ({len(keys['failures'])} failures over "
        f"{keys['keys_checked']} keys); literal scan {'PASS' if literal['passed'] else 'FAIL'} "
        f"({literal['metadata_position_hits']} metadata-position hits over "
        f"{literal['literals_checked']} literals; "
        f"{literal['model_visible_content_hits']} hits inside model-visible content)",
        flush=True,
    )
    for name, counter in literal["metadata_position_by_group"].items():
        print(f"  LEAK {name}: {counter}", flush=True)
    markers = report["in_content_perturbation_markers"]
    print(
        f"  model-visible perturbation markers: {markers['rows_with_a_marker']} rows carry one, "
        f"{markers['rows_with_applied_true']} with applied=true (kept verbatim by design)",
        flush=True,
    )
    return 0 if keys["passed"] and literal["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
