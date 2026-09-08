"""Render blind annotation packets into lean per-batch markdown files for LLM annotators.

The blind packet rows carry per-token text needed by the validator; annotators only need the
task brief, the conversation (user turn, tool calls, tool results) and the verbatim channel
texts.  This script drops the token arrays, writes one markdown file per batch of ``--batch-size``
cases in packet order (already hash-shuffled), and an index.  It never reads the private mapping.

Usage:
  packets_render.py --packet artifacts/agent_v2/dataset_g/packets/g_fit/packet.jsonl \
      --out artifacts/agent_v2/dataset_g/packets/render/g_fit --batch-size 15
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def _fmt_task(task: dict) -> str:
    keep = ["workflow_kind", "r_type", "authorized_domain", "expected_tool_plan", "expected_article_ids"]
    lines = []
    for k in keep:
        if k in task:
            lines.append(f"- {k}: {json.dumps(task[k], ensure_ascii=False)}")
    if task.get("requirements"):
        lines.append("- requirements: " + json.dumps(task["requirements"], ensure_ascii=False))
    if task.get("completion_evidence"):
        lines.append("- completion_evidence (source constraint only; do NOT string-match to label coverage): "
                     + json.dumps(task["completion_evidence"], ensure_ascii=False))
    pol = task.get("tool_policy") or {}
    if pol.get("forbidden_effects"):
        lines.append("- forbidden_effects (restricted tools): " + json.dumps(pol["forbidden_effects"], ensure_ascii=False))
    extra = {k: v for k, v in task.items() if k not in set(keep) | {"requirements", "completion_evidence", "tool_policy"}}
    if extra:
        lines.append("- other: " + json.dumps(extra, ensure_ascii=False))
    return "\n".join(lines)


def _fmt_conversation(conv: list[dict]) -> str:
    out = []
    for m in conv:
        kind = m.get("kind")
        tag = "" if m.get("current_episode", True) else " [PRIOR TURN]"
        if kind == "user_message":
            out.append(f"**USER{tag}** (turn {m.get('conversation_turn')}):\n{m.get('content','')}\n")
        elif kind == "tool_call":
            flags = []
            for f in ("executed", "malformed", "restricted"):
                if m.get(f) is True:
                    flags.append(f)
            if m.get("error"):
                flags.append(f"error={m['error']}")
            out.append(f"**TOOL_CALL{tag}** step {m.get('agent_step')} `{m.get('tool_name')}` "
                       f"[{m.get('tool_class')}; {' '.join(flags) or 'not executed'}]:\n{m.get('content','')}\n")
        elif kind == "tool_result":
            out.append(f"**TOOL_RESULT{tag}** step {m.get('agent_step')} `{m.get('tool_name')}`:\n```\n{m.get('content','')}\n```\n")
        else:
            out.append(f"**{kind}{tag}**: {json.dumps(m, ensure_ascii=False)[:2000]}\n")
    return "\n".join(out)


def render_case(c: dict) -> str:
    ep = c.get("episode", {})
    parts = [f"## CASE {c['case_id']}  (subset {c.get('subset')}, packet_order {c.get('packet_order')})", ""]
    parts.append("### Task brief")
    parts.append(_fmt_task(c.get("task", {})))
    parts.append("")
    parts.append("### Conversation")
    parts.append(_fmt_conversation(c.get("conversation", [])))
    parts.append("### Model output by channel (verbatim; evidence substrings must be copied exactly from here)")
    for ch in ("analysis", "commentary", "final"):
        txt = c.get("channel_text", {}).get(ch, "")
        parts.append(f"#### channel_text[{ch}] ({len(txt)} chars)")
        parts.append("```text")
        parts.append(txt if txt else "(empty)")
        parts.append("```")
    parts.append("### Episode facts")
    parts.append(f"- generated_token_count: {ep.get('generated_token_count')}; step_count: {ep.get('step_count')}; "
                 f"stop_reason: {ep.get('stop_reason')}; step_actions: {ep.get('step_actions')}; "
                 f"channel_token_counts: {ep.get('channel_token_counts')}")
    parts.append("")
    return "\n".join(parts)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--packet", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--batch-size", type=int, default=15)
    args = ap.parse_args()
    packet = Path(args.packet)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = [json.loads(l) for l in packet.open()]
    rows.sort(key=lambda r: r["packet_order"])
    sys_prompts = {hashlib.sha256(r["system_prompt"].encode()).hexdigest() for r in rows}
    (out / "system_prompt.md").write_text("# Agent system prompt (identical for every case)\n\n```text\n"
                                          + rows[0]["system_prompt"] + "\n```\n")
    index = []
    for b in range(0, len(rows), args.batch_size):
        batch = rows[b:b + args.batch_size]
        bid = f"batch_{b // args.batch_size:02d}"
        text = [f"# {bid}: {len(batch)} cases from {packet.name} (subset {batch[0].get('subset')})", "",
                "System prompt: see system_prompt.md in this directory.", ""]
        for c in batch:
            text.append(render_case(c))
            text.append("\n---\n")
        p = out / f"{bid}.md"
        p.write_text("\n".join(text))
        index.append({"batch": bid, "file": str(p), "case_ids": [c["case_id"] for c in batch],
                      "chars": p.stat().st_size})
    (out / "batches.json").write_text(json.dumps({"packet": str(packet), "packet_sha256": hashlib.sha256(packet.read_bytes()).hexdigest(),
                                                  "n_cases": len(rows), "batch_size": args.batch_size,
                                                  "distinct_system_prompts": len(sys_prompts), "batches": index},
                                                 ensure_ascii=False, indent=1))
    print(f"{len(rows)} cases -> {len(index)} batches in {out}; distinct system prompts {len(sys_prompts)}")


if __name__ == "__main__":
    main()
