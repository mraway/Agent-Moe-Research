"""Routing-blind automatic pre-checks and length statistics for dataset G.

Nothing here is a behaviour judgement and nothing here is a routing statistic:
every column is a count, a stop reason, a literal substring match or a token
length, exactly as in the P0 run log.  Three groups:

1. :func:`precheck_trace` -- per-episode mechanical columns: steps, tool calls
   split into legal / restricted / malformed / unknown, per-channel token counts,
   total episode tokens, stop reason, the Unicode-normalised completion-evidence
   hit (design section 1.4: diagnostic only), JSON leaking into the user-visible
   final channel, and ``escalate_to_human`` use.
2. :func:`token_axis_report` -- the independent token-axis re-derivation of P0
   run log section 6.3, written without using ``routing.validate_trace``.
3. :func:`length_report` -- the design section 6.4 / 15.1 gate inputs: per-R-type
   episode token distributions, length tertile cutpoints, and the survival curve
   of episode length, both in raw tokens and in detector looks of width w.

The Unicode normalisation is the one frozen in ``docs/research_v4/g_bridge_run_log.md``
section 5: NFKC, every Unicode hyphen/dash folded to ASCII ``-``, NBSP and narrow
spaces folded to a plain space, quotes folded to ASCII, whitespace collapsed,
casefold.  gpt-oss writes U+2011 in identifiers, so an un-normalised matcher
systematically under-counts completed routine answers.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Mapping, Sequence

from . import build


# ---------------------------------------------------------------------------
# Unicode normalisation (g_bridge_run_log section 5)
# ---------------------------------------------------------------------------

_HYPHENS = dict.fromkeys(
    [
        0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015, 0x2212, 0xFE58, 0xFE63, 0xFF0D,
    ],
    "-",
)
_SPACES = dict.fromkeys(
    [0x00A0, 0x2002, 0x2003, 0x2007, 0x2008, 0x2009, 0x200A, 0x202F, 0x205F, 0x3000],
    " ",
)
_QUOTES = {
    0x2018: "'", 0x2019: "'", 0x201A: "'", 0x201B: "'",
    0x201C: '"', 0x201D: '"', 0x201E: '"', 0x201F: '"',
    0x2032: "'", 0x2033: '"',
}
_FOLD = {**_HYPHENS, **_SPACES, **_QUOTES}

#: monitored codepoints (g_bridge_run_log section 5.2, P0 run log section 15.1)
WATCHED_CODEPOINTS = {"U+2011": "‑", "U+202F": " ", "U+2019": "’", "U+2013": "–"}


def normalise(text: str) -> str:
    folded = unicodedata.normalize("NFKC", str(text)).translate(_FOLD)
    return re.sub(r"\s+", " ", folded).strip().casefold()


#: markdown emphasis the model inserts inside otherwise verbatim policy phrases
#: ("more than **4 calendar days** past ..."), which breaks a literal match the
#: same way U+2011 breaks an identifier match
_MARKDOWN = str.maketrans("", "", "*_`")


def normalise_markdown(text: str) -> str:
    """:func:`normalise` plus markdown emphasis removal (looser diagnostic)."""

    folded = unicodedata.normalize("NFKC", str(text)).translate(_FOLD).translate(_MARKDOWN)
    return re.sub(r"\s+", " ", folded).strip().casefold()


def contains_all(text: str, evidence: Sequence[Any], strip_markdown: bool = False) -> bool:
    """All-of over items, any-of over an item's alternatives, after normalisation."""

    if not evidence:
        return False
    fold = normalise_markdown if strip_markdown else normalise
    haystack = fold(text)
    for item in evidence:
        alternatives = (item,) if isinstance(item, str) else tuple(item)
        if not any(fold(alternative) in haystack for alternative in alternatives):
            return False
    return True


# ---------------------------------------------------------------------------
# JSON / protocol leaks into the user-visible final channel
# ---------------------------------------------------------------------------

_JSON_OBJECT = re.compile(r'\{\s*"[^"\n]{1,64}"\s*:')
_LEAK_PATTERNS = {
    "json_object_with_quoted_key": _JSON_OBJECT,
    "tool_result_ok_field": re.compile(r'"ok"\s*:\s*(true|false)'),
    "provenance_block": re.compile(r'"provenance"\s*:'),
    "harmony_channel_marker": re.compile(r"<\|channel\|>|<\|message\|>|<\|start\|>|<\|call\|>"),
    "function_recipient": re.compile(r"to=functions\."),
}


def json_leaks(final_text: str) -> list[str]:
    return sorted(name for name, pattern in _LEAK_PATTERNS.items() if pattern.search(final_text))


# ---------------------------------------------------------------------------
# per-episode pre-check
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _channel_text(episode: Mapping[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for step in episode["steps"]:
        for segment in step["channel_segments"]:
            out[segment["channel"]] = out.get(segment["channel"], "") + str(segment["text"])
    return out


def precheck_trace(
    trace_dir: Path, scenario: Mapping[str, Any] | None = None
) -> list[dict[str, Any]]:
    """One row per episode.  Counts and literal matches only."""

    trace_dir = Path(trace_dir)
    trace = _read_json(trace_dir / "trace.json")
    factory = dict((scenario or {}).get("factory") or {})
    mandate = trace.get("task_mandate") or {}
    trace_evidence = (mandate.get("authorized_task_state") or {}).get("completion_evidence", [])
    trace_plan = factory.get("expected_tool_plan", [])

    rows: list[dict[str, Any]] = []
    for episode in trace["episodes"]:
        index = int(episode["episode_index"])
        # a session turn has its own completion evidence and its own tool plan;
        # the trace-level pair is turn 1's evidence and the concatenation over
        # every configured turn, so on G-session both would be wrong here.  No
        # session_turns block -> the trace-level pair, unchanged.
        turn = build.session_turn(scenario, index)
        evidence = list(turn.get("completion_evidence", ())) if turn else trace_evidence
        plan = list(turn.get("expected_tool_plan", ())) if turn else trace_plan
        events = [
            event for event in trace.get("tool_events", ()) if int(event["episode_index"]) == index
        ]
        text = _channel_text(episode)
        final_text = text.get("final", "")
        whole = "".join(text.get(channel, "") for channel in ("analysis", "commentary", "final"))
        steps = list(episode["steps"])
        rows.append(
            {
                "trace_id": trace["trace_id"],
                "arm": trace.get("condition_id"),
                "base_task_id": trace.get("base_task_id"),
                "pair_group_id": trace.get("pair_group_id"),
                "dataset_role": trace.get("dataset_role"),
                "episode_index": index,
                "r_type": factory.get("r_type"),
                "workflow_type": factory.get("workflow_type"),
                "topic": factory.get("topic"),
                "fixture_id": factory.get("fixture_id"),
                "normal_variant": factory.get("normal_variant"),
                "step_count": int(episode["step_count"]),
                "generated_token_count": int(episode["generated_token_count"]),
                "channel_token_counts": {
                    channel: int((episode.get("channel_token_counts") or {}).get(channel, 0))
                    for channel in ("analysis", "commentary", "final")
                },
                "stop_reason": str(episode["stop_reason"]),
                "step_stop_reasons": [str(step["generation_stop_reason"]) for step in steps],
                "hit_generation_length_cap": any(
                    str(step["generation_stop_reason"]) == "length" for step in steps
                ),
                "tool_call_count": len(events),
                "tool_call_names": [str(event["tool_name"]) for event in events],
                "legal_call_count": sum(
                    1
                    for event in events
                    if not event["restricted"] and not event["malformed"]
                ),
                "read_only_call_count": sum(
                    1 for event in events if event.get("tool_class") == "read_only"
                ),
                "escalation_call_count": sum(
                    1 for event in events if event.get("tool_class") == "escalation"
                ),
                "restricted_call_count": sum(1 for event in events if event["restricted"]),
                "malformed_call_count": sum(1 for event in events if event["malformed"]),
                "unknown_tool_call_count": sum(
                    1 for event in events if event.get("tool_class") in (None, "unknown")
                ),
                "header_repeated_count": sum(
                    1 for event in events if event.get("header_repeated")
                ),
                "injection_applied_count": sum(
                    1 for event in events if event.get("injection_applied")
                ),
                "completion_evidence_hit_final": contains_all(final_text, evidence),
                "completion_evidence_hit_output": contains_all(whole, evidence),
                "completion_evidence_hit_final_md_stripped": contains_all(
                    final_text, evidence, strip_markdown=True
                ),
                "completion_evidence_items_hit_final": sum(
                    1
                    for item in evidence
                    if contains_all(final_text, [item], strip_markdown=True)
                ),
                "completion_evidence_item_count": len(evidence),
                "expected_tool_plan": plan,
                "expected_tools_all_called": all(
                    name in {str(event["tool_name"]) for event in events}
                    for name in plan
                )
                if plan
                else None,
                "json_leaks_in_final": json_leaks(final_text),
                "final_is_empty": not final_text.strip(),
                "watched_codepoints": {
                    name: whole.count(char) for name, char in WATCHED_CODEPOINTS.items()
                },
            }
        )
    return rows


# ---------------------------------------------------------------------------
# the automatic pre-check verdict
# ---------------------------------------------------------------------------

#: A trace fails an automatic pre-check when the *runtime* misbehaved, never
#: because of what the model said: that judgement belongs to the annotated
#: quality axes of design section 2.3.  Completion-evidence matching is
#: explicitly diagnostic only (design section 1.4) and is therefore NOT a
#: pre-check reason.
def precheck_failures(row: Mapping[str, Any]) -> list[str]:
    reasons: list[str] = []
    if str(row["stop_reason"]) != "final_channel":
        reasons.append(f"stop_reason={row['stop_reason']}")
    if row["hit_generation_length_cap"]:
        reasons.append("generation_length_cap")
    if int(row["malformed_call_count"]):
        reasons.append("malformed_tool_call")
    if int(row["restricted_call_count"]):
        reasons.append("restricted_tool_call")
    if int(row["unknown_tool_call_count"]):
        reasons.append("unknown_tool_call")
    if row["final_is_empty"]:
        reasons.append("empty_final_channel")
    if row["json_leaks_in_final"]:
        reasons.append("json_leak_in_final:" + ",".join(row["json_leaks_in_final"]))
    return reasons


def precheck_pass(row: Mapping[str, Any]) -> bool:
    return not precheck_failures(row)


# ---------------------------------------------------------------------------
# independent token-axis check (P0 run log section 6.3)
# ---------------------------------------------------------------------------


def token_axis_report(trace_dir: Path, check_tensors: bool = True) -> dict[str, Any]:
    """Re-derive the episode -> routing-shard mapping from scratch.

    Deliberately does not call ``routing.validate_trace``: this is the second,
    independent pass of P0 run log section 6.3, checks (a) to (e).
    """

    trace_dir = Path(trace_dir)
    trace = _read_json(trace_dir / "trace.json")
    manifest = [
        json.loads(line)
        for line in (trace_dir / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    by_index = {int(row["step_index"]): row for row in manifest}
    problems: list[str] = []

    steps = list(trace["token_axis"]["steps"])
    expected_shards = sum(1 + int(step["output_token_count"]) for step in steps)
    if len(manifest) != expected_shards:
        problems.append(f"shard count {len(manifest)} != 1+tokens per step sum {expected_shards}")
    if sorted(by_index) != list(range(len(manifest))):
        problems.append("shard indices are not a dense 0..n-1 range")

    generations = {
        (int(event.get("episode_index", 0)), int(event.get("agent_step", 0))): event
        for event in trace.get("events", ())
        if event.get("kind") == "model_generation"
    }
    for step in steps:
        prefill = by_index.get(int(step["routing_step_index_prefill"]))
        if prefill is None or prefill["phase"] != "prefill":
            problems.append(f"step {step['agent_step']}: prefill shard missing")
            continue
        if len(prefill["token_ids"]) != int(step["prompt_token_count"]):
            problems.append(
                f"step {step['agent_step']}: prefill holds {len(prefill['token_ids'])} tokens, "
                f"prompt_token_count is {step['prompt_token_count']}"
            )
        event = generations.get((int(step["episode_index"]), int(step["agent_step"])))
        ids = list((event or {}).get("output_token_ids") or ())
        if len(ids) != int(step["output_token_count"]):
            problems.append(f"step {step['agent_step']}: output_token_ids length mismatch")
            continue
        first = int(step["routing_step_index_first_decode"])
        for offset, token_id in enumerate(ids):
            shard = by_index.get(first + offset)
            if shard is None or shard["phase"] != "decode":
                problems.append(f"step {step['agent_step']}: decode shard {first + offset} missing")
                break
            if list(shard["token_ids"]) != [int(token_id)]:
                problems.append(
                    f"step {step['agent_step']}: token {offset} id {token_id} != shard "
                    f"{first + offset} id {shard['token_ids']}"
                )
                break

    offsets = {
        (int(step["episode_index"]), int(step["agent_step"])): step for step in steps
    }
    for event in trace.get("tool_events", ()):
        key = (int(event["episode_index"]), int(event["agent_step"]))
        step = offsets.get(key)
        if step is None:
            problems.append(f"tool event {event['event_id']}: no matching step")
            continue
        base = int(step["global_token_offset"])
        first_decode = int(step["routing_step_index_first_decode"])
        for global_key, shard_key in (
            ("call_first_token_global", "routing_step_index_first_token"),
            ("call_last_token_global", "routing_step_index_last_token"),
        ):
            expected = first_decode + (int(event[global_key]) - base)
            if int(event[shard_key]) != expected:
                problems.append(
                    f"tool event {event['event_id']}: {shard_key} {event[shard_key]} != {expected}"
                )

    tensor_rows_checked = 0
    if check_tensors:
        from safetensors import safe_open

        for row in manifest:
            path = trace_dir / row["tensor_file"]
            if not path.exists():
                problems.append(f"missing tensor file {row['tensor_file']}")
                continue
            with safe_open(path, framework="pt") as handle:
                shape = handle.get_slice("top_k_ids").get_shape()
            if int(shape[1]) != len(row["token_ids"]):
                problems.append(
                    f"{row['tensor_file']}: tensor holds {shape[1]} tokens, manifest says "
                    f"{len(row['token_ids'])}"
                )
            tensor_rows_checked += 1

    return {
        "trace_id": trace["trace_id"],
        "path": str(trace_dir),
        "shard_count": len(manifest),
        "expected_shard_count": expected_shards,
        "tensor_files_checked": tensor_rows_checked,
        "passed": not problems,
        "problems": problems,
    }


# ---------------------------------------------------------------------------
# length statistics and the H rule (design sections 6.4 and 15.1)
# ---------------------------------------------------------------------------


def tertile_cutpoints(lengths: Sequence[int]) -> dict[str, Any]:
    """Cutpoints that split the sample into three (nearly) equal length groups."""

    ordered = sorted(int(value) for value in lengths)
    n = len(ordered)
    if n == 0:
        return {"n": 0, "low_high": None, "mid_high": None}
    low = ordered[max(0, (n + 2) // 3 - 1)]
    mid = ordered[max(0, (2 * n + 2) // 3 - 1)]
    sizes = (
        sum(1 for value in ordered if value <= low),
        sum(1 for value in ordered if low < value <= mid),
        sum(1 for value in ordered if value > mid),
    )
    return {
        "n": n,
        "low_high": low,
        "mid_high": mid,
        "strata": {"short": f"<= {low}", "medium": f"{low + 1}..{mid}", "long": f"> {mid}"},
        "stratum_sizes": list(sizes),
    }


def survival(lengths: Sequence[int], k: int) -> int:
    """How many episodes still have a token at index ``k - 1`` (i.e. length >= k)."""

    return sum(1 for value in lengths if int(value) >= k)


def largest_k_with_survivors(lengths: Sequence[int], minimum: int) -> int | None:
    """Largest k whose surviving-path count is at least ``minimum`` (the H rule)."""

    ordered = sorted((int(value) for value in lengths), reverse=True)
    if len(ordered) < minimum or minimum <= 0:
        return None
    return ordered[minimum - 1]


def survival_curve(lengths: Sequence[int], grid: Iterable[int]) -> list[dict[str, int]]:
    return [{"k": int(k), "survivors": survival(lengths, int(k))} for k in grid]


def look_survival(lengths: Sequence[int], width: int, minimum: int) -> dict[str, Any]:
    """Survival expressed in complete detector looks of ``width`` tokens.

    Episode of length L supports ``L // width`` complete looks; it survives look
    j (1-based) when ``L >= j * width``.  ``H`` in token units is therefore the
    largest multiple of ``width`` at or below the raw H.
    """

    raw = largest_k_with_survivors(lengths, minimum)
    looks = None if raw is None else raw // width
    return {
        "window": width,
        "min_surviving_paths": minimum,
        "largest_surviving_look": looks,
        "h_tokens": None if looks is None else looks * width,
        "curve": [
            {
                "look": j,
                "k_tokens": j * width,
                "survivors": survival(lengths, j * width),
            }
            for j in range(1, (max(lengths) // width) + 1 if lengths else 1)
        ],
    }


def describe(lengths: Sequence[int]) -> dict[str, Any]:
    ordered = sorted(int(value) for value in lengths)
    if not ordered:
        return {"n": 0}

    def percentile(fraction: float) -> int:
        index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
        return ordered[index]

    return {
        "n": len(ordered),
        "min": ordered[0],
        "p10": percentile(0.10),
        "p25": percentile(0.25),
        "median": int(median(ordered)),
        "p75": percentile(0.75),
        "p90": percentile(0.90),
        "max": ordered[-1],
        "mean": round(sum(ordered) / len(ordered), 1),
    }


def length_report(
    rows: Sequence[Mapping[str, Any]],
    minimum_surviving_paths: int = 90,
    windows: Sequence[int] = (8, 4),
    grid: Sequence[int] = (64, 96, 128, 160, 192, 224, 256, 320, 384, 512),
) -> dict[str, Any]:
    lengths = [int(row["generated_token_count"]) for row in rows]
    passing = [int(row["generated_token_count"]) for row in rows if precheck_pass(row)]
    by_r_type: dict[str, list[int]] = {}
    for row in rows:
        by_r_type.setdefault(str(row.get("r_type")), []).append(int(row["generated_token_count"]))
    return {
        "n_episodes": len(lengths),
        "n_episodes_passing_prechecks": len(passing),
        "overall": describe(lengths),
        "by_r_type": {key: describe(value) for key, value in sorted(by_r_type.items())},
        "tertiles": tertile_cutpoints(lengths),
        "tertiles_by_r_type": {
            key: tertile_cutpoints(value) for key, value in sorted(by_r_type.items())
        },
        "survival_grid": survival_curve(lengths, grid),
        "h_rule": {
            "min_surviving_paths": minimum_surviving_paths,
            "largest_k_raw_tokens": largest_k_with_survivors(lengths, minimum_surviving_paths),
            "note": (
                "Unfiltered input to the design section 15.1 H rule. The frozen H is "
                "computed on the quality-filtered G-cal pool, which needs annotation; "
                "'passing_prechecks' below is the same statistic restricted to traces "
                "that clear the automatic pre-checks, and is an upper bound on it."
            ),
        },
        "h_rule_passing_prechecks": {
            "min_surviving_paths": minimum_surviving_paths,
            "n": len(passing),
            "largest_k_raw_tokens": largest_k_with_survivors(passing, minimum_surviving_paths),
        },
        "looks": {
            str(width): look_survival(lengths, width, minimum_surviving_paths)
            for width in windows
        },
        "looks_passing_prechecks": {
            str(width): look_survival(passing, width, minimum_surviving_paths)
            for width in windows
        },
        "tertiles_passing_prechecks": tertile_cutpoints(passing),
    }
