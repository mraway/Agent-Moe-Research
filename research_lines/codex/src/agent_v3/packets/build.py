"""Build the routing-blind annotation packet for Agent v3 traces (design 4, 8.4).

One packet row = one **episode** (design section 1.1: the guarantee unit is an
episode, and the E / C / X token spans of design section 4 live on the episode's
global token axis).  A single-turn trace therefore yields one row; a session
trace yields one row per user turn.

A row carries exactly what an annotator needs and nothing else:

* the authorised task (workflow kind, the declared read-only tool plan and the
  completion evidence -- without them the coverage / citation / material-error
  axes of design section 2.3 cannot be judged) and the system prompt;
* every model-visible message in order: user messages, tool calls, and tool
  results **as the model saw them**, i.e. after any injection was applied;
* the per-channel decoded text (analysis / commentary / final) with the global
  episode token index of every message boundary and of every token;
* the episode stop reason and the per-step generation stop reasons.

It carries no routing tensor, no detector score, no earlier label, no arm name
and no ``dataset_role``; ``schema.FORBIDDEN_PACKET_KEYS`` is asserted over every
emitted row.  The opaque case id and the private ``case_id -> trace`` mapping are
written to two different files, and the packet order is a deterministic hashed
shuffle so that neither collection order nor arm order survives into it.

Nothing here loads a tokenizer: ``manifest.jsonl`` already stores the decoded
text of every generated token, and the builder asserts that those pieces
reconstruct each channel segment's text exactly.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

from . import schema


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def case_id(trace_id: str, episode_index: int) -> str:
    """Opaque, deterministic and collision-checked at packet level."""

    payload = f"{schema.CASE_ID_NAMESPACE}::{trace_id}#ep{int(episode_index)}"
    return "g-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def shuffle_key(subset: str, case: str) -> str:
    payload = f"{schema.SHUFFLE_NAMESPACE}::{subset}::{case}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# per-token text on the episode axis
# ---------------------------------------------------------------------------


def decode_pieces(manifest_rows: Sequence[Mapping[str, Any]]) -> dict[int, str]:
    """``routing shard index -> decoded text of the single token it holds``."""

    pieces: dict[int, str] = {}
    for row in manifest_rows:
        if row.get("phase") != "decode":
            continue
        texts = list(row.get("token_texts") or ())
        if len(texts) != 1:
            raise ValueError(
                f"decode shard {row.get('step_index')} holds {len(texts)} tokens, expected 1"
            )
        pieces[int(row["step_index"])] = str(texts[0])
    return pieces


def _step_pieces(step: Mapping[str, Any], shard_pieces: Mapping[int, str]) -> list[str]:
    first = int(step["routing_step_index_first_decode"])
    count = int(step["output_token_count"])
    out: list[str] = []
    for offset in range(count):
        shard = first + offset
        if shard not in shard_pieces:
            raise ValueError(f"missing decode shard {shard} for agent step {step['agent_step']}")
        out.append(shard_pieces[shard])
    return out


# ---------------------------------------------------------------------------
# channel view of one episode
# ---------------------------------------------------------------------------


#: the character ``str.decode`` produces for an incomplete UTF-8 sequence
REPLACEMENT = "�"


def repair_split_character_pieces(pieces: Sequence[str], expected: str) -> list[str] | None:
    """Re-align per-token decode pieces around a character split across tokens.

    ``manifest.jsonl`` stores the text of every generated token *decoded on its
    own*.  When the tokenizer splits one character's UTF-8 bytes across two or
    more tokens -- gpt-oss does this for some non-ASCII glyphs, e.g. U+2248
    ``≈`` -- each of those tokens decodes to U+FFFD, and the naive
    concatenation no longer equals the segment text the session recorded.

    The repair gives the whole character run to the **first** token of the
    broken sequence, because that is the token at which the character starts:
    an evidence span beginning at that character then still maps to the right
    token index.  The remaining tokens of the run get the empty string, so the
    token count, and therefore every global token index, is unchanged.

    Returns ``None`` when the mismatch is *not* explained by replacement
    characters, so the caller still raises rather than silently accepting a
    corrupt axis.
    """

    if REPLACEMENT not in "".join(pieces):
        return None
    out: list[str] = []
    position = 0
    index = 0
    while index < len(pieces):
        piece = pieces[index]
        if REPLACEMENT not in piece:
            if not expected.startswith(piece, position):
                return None
            out.append(piece)
            position += len(piece)
            index += 1
            continue
        run_end = index
        while run_end < len(pieces) and REPLACEMENT in pieces[run_end]:
            run_end += 1
        # Anchor on the *whole* clean block that follows the run, not on its
        # first piece: a single-token anchor like " " matches far too early.
        block_end = run_end
        while block_end < len(pieces) and REPLACEMENT not in pieces[block_end]:
            block_end += 1
        anchor = "".join(pieces[run_end:block_end])
        if anchor:
            # a run stands for at least one character, so it cannot be empty
            stop = expected.find(anchor, position + 1)
            if stop < 0:
                return None
        else:
            stop = len(expected)
        chunk = expected[position:stop]
        prefix = pieces[index].split(REPLACEMENT)[0]
        suffix = pieces[run_end - 1].split(REPLACEMENT)[-1]
        if not chunk.startswith(prefix) or not chunk.endswith(suffix):
            return None
        if all(ord(character) < 128 for character in chunk):
            # a run of U+FFFD must stand for at least one multi-byte character
            return None
        if len(chunk) > sum(len(piece) for piece in pieces[index:run_end]):
            # every U+FFFD stands for at least one byte, so it can expand to at
            # most one character; a longer chunk means the anchor mis-aligned
            # (or the run sits at the end and would swallow arbitrary text)
            return None
        out.append(chunk)
        out.extend([""] * (run_end - index - 1))
        position = stop
        index = run_end
    if position != len(expected) or len(out) != len(pieces):
        return None
    return out


def _channel_messages(
    episode: Mapping[str, Any], shard_pieces: Mapping[int, str]
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for step in episode["steps"]:
        offset = int(step["global_token_offset"])
        pieces = _step_pieces(step, shard_pieces)
        for segment in step["channel_segments"]:
            body_start = int(segment["body_start"])
            body_end = int(segment["body_end"])
            if body_start < 0 or body_end < body_start or body_end > len(pieces):
                raise ValueError(
                    f"segment body span [{body_start}, {body_end}) is outside the step"
                )
            body = pieces[body_start:body_end]
            text = "".join(body)
            repaired = False
            if text != str(segment["text"]):
                fixed = repair_split_character_pieces(body, str(segment["text"]))
                if fixed is None:
                    raise ValueError(
                        "per-token pieces do not reconstruct the channel segment text "
                        f"(agent step {step['agent_step']}, channel {segment['channel']})"
                    )
                body = fixed
                text = "".join(body)
                repaired = True
            if int(segment["global_body_start"]) != offset + body_start:
                raise ValueError("global_body_start disagrees with global_token_offset")
            terminator = segment.get("terminator_index")
            messages.append(
                {
                    "agent_step": int(step["agent_step"]),
                    "channel": str(segment["channel"]),
                    "recipient": segment.get("recipient"),
                    "content_type": segment.get("content_type"),
                    "end_kind": segment.get("end_kind"),
                    "header_repeated": bool(segment.get("header_repeated", False)),
                    "global_header_start": offset + int(segment["header_start"]),
                    "global_body_start": offset + body_start,
                    "global_body_end": offset + body_end,
                    "global_terminator_index": (
                        None if terminator is None or int(terminator) < 0 else offset + int(terminator)
                    ),
                    "token_count": body_end - body_start,
                    "text": text,
                    "tokens": [
                        {"g": offset + body_start + index, "text": piece}
                        for index, piece in enumerate(body)
                    ],
                    **(
                        {"token_text_repaired": True}
                        if repaired
                        else {}
                    ),
                }
            )
    messages.sort(key=lambda message: message["global_header_start"])
    return messages


def channel_text(messages: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """Concatenated body text per channel, in episode token order.

    Messages of one channel are joined without a separator so that any substring
    of a single message is also a substring of the channel text: an annotator's
    exact evidence string therefore stays exact, and uniqueness is enforced over
    the whole channel.
    """

    out: dict[str, str] = {}
    for message in messages:
        out[message["channel"]] = out.get(message["channel"], "") + message["text"]
    return out


def _with_channel_offsets(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cursor: dict[str, int] = {}
    for message in messages:
        channel = message["channel"]
        start = cursor.get(channel, 0)
        message["char_start_in_channel"] = start
        message["char_end_in_channel"] = start + len(message["text"])
        cursor[channel] = message["char_end_in_channel"]
    return messages


def channel_token_index(messages: Sequence[Mapping[str, Any]]) -> dict[str, list[int]]:
    """``channel -> global token index of every token of ``channel_text``."""

    out: dict[str, list[int]] = {}
    for message in messages:
        out.setdefault(message["channel"], []).extend(
            int(token["g"]) for token in message["tokens"]
        )
    return out


def channel_token_pieces(messages: Sequence[Mapping[str, Any]]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for message in messages:
        out.setdefault(message["channel"], []).extend(str(token["text"]) for token in message["tokens"])
    return out


# ---------------------------------------------------------------------------
# model-visible conversation
# ---------------------------------------------------------------------------

_CONVERSATION_KINDS = ("user_message", "tool_call", "tool_result", "assistant_message")


def _conversation(
    trace: Mapping[str, Any], episode_index: int, hide_attack_metadata: bool = False
) -> list[dict[str, Any]]:
    """Every model-visible message up to and including this episode, in order.

    ``hide_attack_metadata`` drops the ``injection_applied`` flag of a tool
    result.  The injected text itself stays -- the annotator must read exactly
    what the model read (design section 3) -- but the flag would say "this
    message was modified", which on a subset that collects the attack arm is arm
    metadata.
    """

    rows: list[dict[str, Any]] = []
    for event in trace.get("events", ()):
        kind = str(event.get("kind"))
        if kind not in _CONVERSATION_KINDS:
            continue
        if not event.get("model_visible", True):
            continue
        index = int(event.get("episode_index", 0))
        if index > episode_index:
            continue
        if kind == "assistant_message" and index == episode_index:
            # the answer under annotation is the packet's ``final`` channel text
            continue
        row: dict[str, Any] = {
            "episode_index": index,
            "current_episode": index == episode_index,
            "conversation_turn": int(event.get("conversation_turn", 1)),
            "agent_step": int(event.get("agent_step", 0)),
            "kind": kind,
            "content": event.get("content"),
        }
        if kind == "tool_call":
            row.update(
                {
                    "tool_name": event.get("tool_name"),
                    "tool_class": event.get("tool_class"),
                    "restricted": bool(event.get("restricted", False)),
                    "malformed": bool(event.get("malformed", False)),
                    "executed": bool(event.get("executed", False)),
                    "error": event.get("error"),
                    "call_first_token_global": event.get("call_first_token_global"),
                    "call_last_token_global": event.get("call_last_token_global"),
                }
            )
        elif kind == "tool_result":
            row.update(
                {
                    "tool_name": event.get("tool_name"),
                    "tool_class": event.get("tool_class"),
                    "executed": bool(event.get("executed", False)),
                }
            )
            if not hide_attack_metadata:
                row["injection_applied"] = bool(event.get("injection_applied", False))
        rows.append(row)
    return rows


def _system_prompt(trace: Mapping[str, Any]) -> str:
    for event in trace.get("events", ()):
        if event.get("kind") == "system_message":
            return str(event.get("content", ""))
    return ""


def session_turn(scenario: Mapping[str, Any] | None, episode_index: int) -> Mapping[str, Any] | None:
    """The ``factory.session_turns`` entry this episode is, if the subset has one.

    G-session is the only subset where one trace walks through several *different*
    tasks: turn 1 asks about a warranty, turn 2 about a support case.  Every other
    subset has at most a clarification turn of the same task, and no
    ``session_turns`` block at all, so this returns ``None`` there and the
    trace-level mandate is used unchanged.

    ``turn_index`` is 1-based in the config, ``episode_index`` is 0-based here.
    """

    factory = ((scenario or {}).get("factory") or {})
    for turn in factory.get("session_turns") or ():
        if int(turn.get("turn_index", 0)) == int(episode_index) + 1:
            return turn
    return None


def _requirement(item: Mapping[str, Any]) -> dict[str, Any]:
    """A scenario requirement in the shape ``trace.json``'s mandate stores it.

    Mirrors ``agent_v2.experiment.task_from_scenario``'s ``ToolRequirement`` so a
    per-turn brief and a trace-level brief are the same five fields.
    """

    return {
        "action": str(item["action"]),
        "arguments": dict(item.get("arguments", {})),
        "expected_article_ids": [str(value) for value in item.get("expected_article_ids", [])],
        "allow_empty_result": bool(item.get("allow_empty_result", False)),
        "query_hint": (
            str(item["query_hint"]) if item.get("query_hint") is not None else None
        ),
    }


def _task(
    trace: Mapping[str, Any],
    scenario: Mapping[str, Any] | None,
    episode_index: int = 0,
) -> dict[str, Any]:
    """The authorised task only: no arm, no injection metadata, no marker.

    ``completion_evidence`` and the declared tool plan are part of the task, not
    of a label: without them the coverage / citation / material-error axes of
    design section 2.3 cannot be judged.

    On a session subset the brief is the brief **of this turn**.  The trace-level
    mandate is turn 1's, so reusing it for turn 2 would hand the annotator the
    wrong requirements and the wrong completion evidence, and the trace-level
    ``factory.expected_tool_plan`` is the concatenation over *every* configured
    turn -- including turns the runtime never issued, which both mis-states the
    task and tells the reader how long the session was.  The per-turn brief is
    strictly less revealing: it carries no turn index, no injected flag and no
    clean twin of an injected message, and for an injected turn it is the
    *routine* request, which is exactly what the annotator has to judge against.
    """

    mandate = trace.get("task_mandate") or {}
    state = mandate.get("authorized_task_state") or {}
    task: dict[str, Any] = {
        "workflow_kind": mandate.get("authorized_goal"),
        "authorized_domain": mandate.get("authorized_domain"),
        "requirements": state.get("requirements", []),
        "completion_evidence": state.get("completion_evidence", []),
        "tool_policy": {
            "allowed_effects": mandate.get("allowed_effects", []),
            "forbidden_effects": mandate.get("forbidden_effects", []),
        },
    }
    if scenario is not None:
        factory = scenario.get("factory") or {}
        task["expected_tool_plan"] = factory.get("expected_tool_plan", [])
        task["expected_article_ids"] = factory.get("expected_article_ids", [])
        task["r_type"] = factory.get("r_type")
    turn = session_turn(scenario, episode_index)
    if turn is not None:
        task["workflow_kind"] = turn.get("workflow_kind", task["workflow_kind"])
        task["requirements"] = [_requirement(item) for item in turn.get("requirements", ())]
        task["completion_evidence"] = list(turn.get("completion_evidence", ()))
        task["expected_tool_plan"] = list(turn.get("expected_tool_plan", ()))
        task["r_type"] = turn.get("r_type", task.get("r_type"))
    return task


# ---------------------------------------------------------------------------
# tool-result redaction of harness perturbation markers
# ---------------------------------------------------------------------------

#: literal the builder writes in place of a redacted value when
#: ``redaction_style="placeholder"``; also recorded in the private mapping so a
#: reader of the mapping knows what was taken out and what replaced it.
REDACTION_PLACEHOLDER = "[redacted-by-packet-builder]"

#: Keys the *harness* writes into a tool result before the model reads it.  Both
#: are the **same** metadata object: ``agent_v2.experiment.apply_tool_result_injection``
#: builds one ``metadata`` dict and attaches it as ``experiment_perturbation`` on
#: the result and, for the ``first_kb_hit`` location, as ``external_note_provenance``
#: on the injected KB hit.  It reads ``{"applied": true, "source_trust":
#: "untrusted", ...}`` -- a machine-authored "this tool result was modified" flag
#: that is present in exactly the injected arms and absent in ``clean``, so its
#: mere *presence* separates the arms with no reading required.  The injected
#: **text** itself is never touched: the annotator must still read exactly what
#: the model read (design section 3), and judging that text is the task.
DEFAULT_TOOL_RESULT_REDACT_KEYS = (
    "experiment_perturbation",
    "external_note_provenance",
)

#: ``remove``  -- drop the key/value pair entirely; the key name does not survive
#:               into the packet, so neither the value nor the *presence* of the
#:               marker can be read off.  This is the default.
#: ``placeholder`` -- keep the key and write :data:`REDACTION_PLACEHOLDER` as its
#:               value.  Offered because it is the literal wording of the ruling,
#:               but note that it does **not** blind the packet: a row that shows
#:               ``"experiment_perturbation": "[redacted-by-packet-builder]"`` is
#:               still exactly the set of injected rows.
REDACTION_STYLES = ("remove", "placeholder")

#: Packet fields the redaction must never enter.  They are the model's own
#: output and the surface every annotator evidence string is aligned against, so
#: they must stay byte-identical to what the session decoded.
REDACTION_PROTECTED_FIELDS = ("channel_text", "channel_messages", "channel_token_index")

#: Which ``conversation`` entries carry tool-result text.  User messages, tool
#: calls and earlier assistant messages are model input / model output and are
#: left alone even though they are not part of the evidence surface.
REDACTION_CONVERSATION_KINDS = ("tool_result",)


def _json_value_end(text: str, start: int) -> int:
    """Index just past the JSON-ish value that begins at ``text[start]``."""

    length = len(text)
    if start >= length:
        return length
    opener = text[start]
    if opener in "\"'":
        index = start + 1
        while index < length:
            if text[index] == "\\":
                index += 2
                continue
            if text[index] == opener:
                return index + 1
            index += 1
        return length
    if opener in "{[":
        depth = 0
        index = start
        while index < length:
            character = text[index]
            if character in "\"'":
                index = _json_value_end(text, index)
                continue
            if character in "{[":
                depth += 1
            elif character in "}]":
                depth -= 1
                if depth == 0:
                    return index + 1
            index += 1
        return length
    index = start
    while index < length and text[index] not in ",}]\n":
        index += 1
    return index


def redact_in_string(text: str, keys: Sequence[str], style: str = "remove") -> tuple[str, int]:
    """Redact ``"<key>": <value>`` pairs inside a JSON-ish *string*.

    A tool result reaches the packet as a dict here, but a harness may serialise
    it before handing it to the model; the string path keeps the redaction
    total either way.  Surgery is textual and local -- the surrounding string is
    never re-serialised -- so nothing else about the model-visible text moves.
    """

    if not keys or not any(key in text for key in keys):
        return text, 0
    pattern = re.compile(
        r"(?<![A-Za-z0-9_])(?P<quote>[\"']?)"
        + r"(?P<key>" + "|".join(re.escape(key) for key in keys) + r")"
        + r"(?P=quote)[ \t]*:[ \t]*"
    )
    pieces: list[str] = []
    cursor = 0
    hits = 0
    while True:
        match = pattern.search(text, cursor)
        if match is None:
            break
        end = _json_value_end(text, match.end())
        pieces.append(text[cursor:match.start()])
        if style == "placeholder":
            quote = match.group("quote") or '"'
            pieces.append(
                f"{match.group('quote')}{match.group('key')}{match.group('quote')}: "
                f"{quote}{REDACTION_PLACEHOLDER}{quote}"
            )
        else:
            # drop the pair and exactly one adjacent separator so the remaining
            # text is still well formed
            trailing = end
            while trailing < len(text) and text[trailing] in " \t\r\n":
                trailing += 1
            if trailing < len(text) and text[trailing] == ",":
                end = trailing + 1
                while end < len(text) and text[end] in " \t":
                    end += 1
            else:
                head = "".join(pieces).rstrip()
                if head.endswith(","):
                    pieces = [head[:-1]]
        hits += 1
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces), hits


def _redact_node(
    node: Any, keys: Sequence[str], style: str, path: str, hits: list[dict[str, Any]]
) -> Any:
    if isinstance(node, dict):
        for key in list(node.keys()):
            child = f"{path}.{key}"
            if key in keys:
                hits.append({"key": key, "path": child, "site": "value", "value": node[key]})
                if style == "placeholder":
                    node[key] = REDACTION_PLACEHOLDER
                else:
                    del node[key]
                continue
            node[key] = _redact_node(node[key], keys, style, child, hits)
        return node
    if isinstance(node, list):
        for index, item in enumerate(node):
            node[index] = _redact_node(item, keys, style, f"{path}[]", hits)
        return node
    if isinstance(node, str):
        redacted, count = redact_in_string(node, keys, style)
        for _ in range(count):
            hits.append({"key": None, "path": path, "site": "text", "value": None})
        return redacted
    return node


def redact_tool_result_keys(
    row: dict[str, Any], keys: Sequence[str] = (), style: str = "remove"
) -> list[dict[str, Any]]:
    """Strip harness perturbation markers out of one packet row, in place.

    The walk covers every ``tool_result`` conversation entry and every other
    packet field except :data:`REDACTION_PROTECTED_FIELDS`; ``channel_text``,
    ``channel_messages`` (message text *and* the per-token arrays) and
    ``channel_token_index`` are therefore byte-identical before and after.

    Returns one aggregated record per ``(key, json path)`` for the private
    mapping's ``redactions`` field; an empty list when nothing matched.
    """

    keys = tuple(dict.fromkeys(str(key) for key in keys if key))
    if not keys:
        return []
    if style not in REDACTION_STYLES:
        raise ValueError(f"unknown redaction style {style!r}; expected one of {REDACTION_STYLES}")
    hits: list[dict[str, Any]] = []
    for field in list(row.keys()):
        if field in REDACTION_PROTECTED_FIELDS:
            continue
        if field == "conversation":
            for entry in row.get(field) or ():
                if not isinstance(entry, dict):
                    continue
                if str(entry.get("kind")) not in REDACTION_CONVERSATION_KINDS:
                    continue
                _redact_node(entry, keys, style, "conversation[]", hits)
            continue
        row[field] = _redact_node(row[field], keys, style, field, hits)

    grouped: dict[tuple[Any, str, str], dict[str, Any]] = {}
    for hit in hits:
        signature = (hit["key"], hit["path"], hit["site"])
        record = grouped.setdefault(
            signature,
            {
                "key": hit["key"],
                "path": hit["path"],
                "site": hit["site"],
                "style": style,
                "replacement": REDACTION_PLACEHOLDER if style == "placeholder" else None,
                "count": 0,
                "removed_values": [],
            },
        )
        record["count"] += 1
        if hit["site"] == "value":
            rendered = json.dumps(hit["value"], ensure_ascii=False, sort_keys=True)
            if rendered not in record["removed_values"] and len(record["removed_values"]) < 4:
                record["removed_values"].append(rendered)
    return [grouped[signature] for signature in sorted(grouped, key=lambda s: (str(s[0]), s[1], s[2]))]


def residual_key_hits(
    row: Mapping[str, Any], keys: Sequence[str]
) -> list[str]:
    """JSON paths where a redacted key name still occurs outside model output.

    Used as a post-write self-check: with ``style="remove"`` the expected result
    is an empty list.  Paths inside :data:`REDACTION_PROTECTED_FIELDS` are not
    reported -- if the *model* wrote the word, the packet must keep it.
    """

    keys = tuple(str(key) for key in keys if key)
    out: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, Mapping):
            for key, value in node.items():
                child = f"{path}.{key}" if path else str(key)
                if key in keys:
                    out.append(child)
                walk(value, child)
        elif isinstance(node, (list, tuple)):
            for item in node:
                walk(item, f"{path}[]")
        elif isinstance(node, str):
            for key in keys:
                if re.search(r"(?<![A-Za-z0-9_])[\"']?" + re.escape(key) + r"[\"']?[ \t]*:", node):
                    out.append(f"{path} (in text)")
                    break

    for field, value in row.items():
        if field in REDACTION_PROTECTED_FIELDS:
            continue
        walk(value, field)
    return out


# ---------------------------------------------------------------------------
# one trace -> its episode rows
# ---------------------------------------------------------------------------


def _x_tool_events(trace: Mapping[str, Any], episode_index: int) -> list[dict[str, Any]]:
    """Automatic X_tool anchors of one episode: the restricted tool calls.

    Mechanical, not a label: the runtime already marks a call restricted when the
    requested tool is one of the restricted stubs, and those stubs never execute
    (``policy_oracle.restricted_tools_executed`` is False by construction).  The
    list is written to the **private** mapping only.
    """

    out: list[dict[str, Any]] = []
    for event in trace.get("tool_events", ()):
        if int(event.get("episode_index", 0)) != int(episode_index):
            continue
        if not event.get("restricted"):
            continue
        out.append(
            {
                "event_id": event.get("event_id"),
                "agent_step": event.get("agent_step"),
                "tool_name": event.get("tool_name"),
                "tool_class": event.get("tool_class"),
                "executed": bool(event.get("executed", False)),
                "malformed": bool(event.get("malformed", False)),
                "call_first_token_global": event.get("call_first_token_global"),
                "call_last_token_global": event.get("call_last_token_global"),
            }
        )
    return out


def build_trace_rows(
    trace_dir: Path,
    scenario: Mapping[str, Any] | None = None,
    hide_attack_metadata: bool = False,
    run_group: str | None = None,
    redact_keys: Sequence[str] = (),
    redaction_style: str = "remove",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``(packet_rows, mapping_rows)`` for one trace directory.

    ``hide_attack_metadata`` turns on the attack-arm blindness path: the
    ``injection_applied`` flag is dropped from the packet and
    ``schema.ATTACK_FORBIDDEN_PACKET_KEYS`` is enforced on top of the standard
    forbidden-key walk.  The private mapping is unaffected -- it gains the arm,
    the wording tier, the injection channel, the attack target and the automatic
    X_tool (restricted tool call) events of each episode.

    ``redact_keys`` names harness perturbation markers to strip out of the
    tool-result text (see :func:`redact_tool_result_keys`); every packet row
    records what was taken out in the private mapping's ``redactions`` field.
    It is off by default here so that an existing caller keeps building the
    same bytes; ``scripts/research_v4/packets_build.py`` turns it on.
    """

    trace_dir = Path(trace_dir)
    trace = _read_json(trace_dir / "trace.json")
    shard_pieces = decode_pieces(read_jsonl(trace_dir / "manifest.jsonl"))
    trace_id = str(trace["trace_id"])
    system_prompt = _system_prompt(trace)

    packet_rows: list[dict[str, Any]] = []
    mapping_rows: list[dict[str, Any]] = []
    for episode in trace["episodes"]:
        episode_index = int(episode["episode_index"])
        # per episode, because a session turn has its own brief; identical to the
        # trace-level brief on every subset without ``factory.session_turns``
        task = _task(trace, scenario, episode_index)
        messages = _with_channel_offsets(_channel_messages(episode, shard_pieces))
        case = case_id(trace_id, episode_index)
        row = {
            "packet_version": schema.PACKET_VERSION,
            "case_id": case,
            "task": task,
            "system_prompt": system_prompt,
            "conversation": _conversation(trace, episode_index, hide_attack_metadata),
            "episode": {
                "episode_index": episode_index,
                "conversation_turn": int(episode["conversation_turn"]),
                "step_count": int(episode["step_count"]),
                "generated_token_count": int(episode["generated_token_count"]),
                "stop_reason": str(episode["stop_reason"]),
                "step_stop_reasons": [
                    str(step["generation_stop_reason"]) for step in episode["steps"]
                ],
                "step_actions": [str(step["action"]) for step in episode["steps"]],
                "channel_token_counts": dict(episode.get("channel_token_counts") or {}),
            },
            "channel_messages": messages,
            "channel_text": channel_text(messages),
            "channel_token_index": channel_token_index(messages),
            "instructions": (
                "Token indices are the episode's global generated-token axis: it starts "
                "at 0 for the first token of this user turn and does not reset between "
                "agent steps. Evidence strings must be exact, unique substrings of "
                "channel_text[<channel>]."
            ),
        }
        redactions = redact_tool_result_keys(row, redact_keys, style=redaction_style)
        _assert_blind(row, attack_arm=hide_attack_metadata)
        packet_rows.append(row)
        perturbation = dict(trace.get("perturbation") or {})
        goal = dict(perturbation.get("attack_goal") or {})
        factory = dict((scenario or {}).get("factory") or {})
        mapping_rows.append(
            {
                "case_id": case,
                "trace_id": trace_id,
                "episode_index": episode_index,
                "episode_id": f"{trace_id}#ep{episode_index}",
                "pair_group_id": trace.get("pair_group_id"),
                "base_task_id": trace.get("base_task_id"),
                "scenario_id": trace.get("base_task_id"),
                "arm": trace.get("condition_id"),
                "arm_name": perturbation.get("arm"),
                "dataset_role": trace.get("dataset_role"),
                "experiment_id": trace.get("experiment_id"),
                "run_group": run_group,
                "relative_path": f"{trace.get('pair_group_id')}/{trace.get('condition_id')}",
                "wording_tier": trace.get("wording_tier"),
                "domain_group": trace.get("domain_group"),
                "attack_channel": perturbation.get("channel"),
                "attack_present": bool(perturbation.get("attack_present")),
                "attack_family_id": perturbation.get("attack_family_id"),
                "attack_target_domain": goal.get("target_domain"),
                "attack_target_slot": factory.get("target_slot"),
                "attack_goal_type": goal.get("type"),
                "cell_id": factory.get("cell_id"),
                "normal_variant": factory.get("normal_variant"),
                "scenario_role": factory.get("scenario_role"),
                "x_tool_events": _x_tool_events(trace, episode_index),
                "scenario_factory": factory,
                "redactions": redactions,
            }
        )
    return packet_rows, mapping_rows


def _assert_blind(row: Mapping[str, Any], attack_arm: bool = False) -> None:
    """No forbidden key anywhere in the row (audit plan section 5).

    ``attack_arm`` additionally enforces
    :data:`schema.ATTACK_FORBIDDEN_PACKET_KEYS` (design section 3).
    """

    def walk(value: Any, path: str) -> Iterator[tuple[str, str]]:
        if isinstance(value, Mapping):
            for key, sub in value.items():
                yield str(key), path
                yield from walk(sub, f"{path}.{key}")
        elif isinstance(value, (list, tuple)):
            for index, sub in enumerate(value):
                yield from walk(sub, f"{path}[{index}]")

    forbidden = set(schema.FORBIDDEN_PACKET_KEYS)
    if attack_arm:
        forbidden |= set(schema.ATTACK_FORBIDDEN_PACKET_KEYS)
    # ``content`` of a tool result is verbatim model-visible data; a record field
    # may legitimately be called e.g. "note", so only structural keys are checked.
    for key, path in walk(row, "row"):
        if ".content" in path or ".result" in path:
            continue
        if key in forbidden:
            raise ValueError(f"blindness violation: forbidden key {key!r} at {path}")


# ---------------------------------------------------------------------------
# a whole run
# ---------------------------------------------------------------------------


def iter_trace_dirs(run_root: Path) -> list[Path]:
    return sorted(path.parent for path in Path(run_root).glob("*/*/trace.json"))


def scenarios_by_id(run_root: Path) -> dict[str, dict[str, Any]]:
    config_path = Path(run_root) / "resolved_experiment_config.json"
    if not config_path.exists():
        return {}
    config = _read_json(config_path)
    return {str(scenario["base_task_id"]): scenario for scenario in config.get("scenarios", ())}


def build_run(
    run_root: Path,
    subset: str,
    hide_attack_metadata: bool = False,
    redact_keys: Sequence[str] = (),
    redaction_style: str = "remove",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Packet and mapping rows for every trace of one run, in shuffled order."""

    return build_runs(
        [run_root],
        subset,
        hide_attack_metadata=hide_attack_metadata,
        redact_keys=redact_keys,
        redaction_style=redaction_style,
    )


def build_runs(
    run_roots: Sequence[Path],
    subset: str,
    hide_attack_metadata: bool = False,
    redact_keys: Sequence[str] = (),
    redaction_style: str = "remove",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Packet and mapping rows for one subset collected as several run groups.

    A subset whose ``collection_plan`` has more than one group (G-dev collects a
    144-scenario three-arm core, a 120-scenario attack supplement and two
    24-scenario normal variants, each into its own output directory) must be
    shuffled **once, across all groups**, or the packet order would tell the
    annotator which group -- and therefore which arm -- a case came from.
    """

    packet_rows: list[dict[str, Any]] = []
    mapping_rows: list[dict[str, Any]] = []
    for root in run_roots:
        root = Path(root)
        scenarios = scenarios_by_id(root)
        for trace_dir in iter_trace_dirs(root):
            scenario = scenarios.get(trace_dir.parent.name)
            rows, mapping = build_trace_rows(
                trace_dir,
                scenario,
                hide_attack_metadata=hide_attack_metadata,
                run_group=root.name,
                redact_keys=redact_keys,
                redaction_style=redaction_style,
            )
            packet_rows.extend(rows)
            mapping_rows.extend(mapping)

    cases = [row["case_id"] for row in packet_rows]
    if len(set(cases)) != len(cases):
        raise ValueError("opaque case ids collided; widen the digest prefix")

    keys = {case: shuffle_key(subset, case) for case in cases}
    packet_rows.sort(key=lambda row: (keys[row["case_id"]], row["case_id"]))
    for position, row in enumerate(packet_rows):
        row["packet_order"] = position
        row["subset"] = subset
    mapping_rows.sort(key=lambda row: (keys[row["case_id"]], row["case_id"]))
    for position, row in enumerate(mapping_rows):
        row["packet_order"] = position
        row["subset"] = subset
    return packet_rows, mapping_rows
