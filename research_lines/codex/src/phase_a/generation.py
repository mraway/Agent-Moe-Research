"""Model-turn generation with full prefill and decode router capture."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import torch

from routing import RouterTraceRecorder, annotate_chat_prompt

from .protocol import parse_assistant_output


@dataclass(frozen=True)
class GenerationResult:
    text: str
    output_token_ids: list[int]
    prompt_token_ids: list[int]
    prompt_token_count: int
    output_token_count: int
    stop_reason: str
    rendered_prompt: str
    channel_boundaries: dict[str, int] = field(default_factory=dict)


def _token_pieces(tokenizer: Any, token_ids: torch.Tensor) -> tuple[str, ...]:
    return tuple(
        tokenizer.decode(
            [int(token_id)],
            clean_up_tokenization_spaces=False,
        )
        for token_id in token_ids.detach().cpu().reshape(-1).tolist()
    )


def find_channel_boundaries(
    tokenizer: Any,
    output_token_ids: Sequence[int],
    markers: Mapping[str, str],
) -> dict[str, int]:
    """First generated-token index at which each channel marker is complete.

    Used for harmony-style multi-channel outputs (gpt-oss analysis / final):
    the returned index is the token whose arrival completes the marker, so the
    channel body starts at ``index + 1``. ``-1`` means the marker never
    appeared. Routing is recorded for every generated token regardless.
    """

    boundaries = {str(name): -1 for name in markers}
    remaining = {str(name): str(marker) for name, marker in markers.items()}
    token_ids = list(int(value) for value in output_token_ids)
    for index in range(len(token_ids)):
        if not remaining:
            break
        prefix = tokenizer.decode(
            token_ids[: index + 1],
            skip_special_tokens=False,
            clean_up_tokenization_spaces=False,
        )
        for name in [name for name, marker in remaining.items() if marker in prefix]:
            boundaries[name] = index
            del remaining[name]
    return boundaries


def select_next_token(
    logits: torch.Tensor,
    *,
    strategy: str,
    temperature: float = 1.0,
    top_p: float = 1.0,
) -> torch.Tensor:
    """Select one token per batch with greedy or nucleus decoding."""

    if logits.ndim != 2:
        raise ValueError("logits must have shape [batch, vocabulary]")
    if strategy == "greedy":
        return logits.argmax(dim=-1, keepdim=True)
    if strategy != "sample":
        raise ValueError(f"unsupported decoding strategy: {strategy}")
    if temperature <= 0:
        raise ValueError("temperature must be positive for sampling")
    if not 0 < top_p <= 1:
        raise ValueError("top_p must be in (0, 1]")

    scaled = logits / temperature
    if top_p < 1:
        sorted_logits, sorted_indices = torch.sort(scaled, descending=True, dim=-1)
        sorted_probabilities = torch.softmax(sorted_logits, dim=-1)
        remove = torch.cumsum(sorted_probabilities, dim=-1) > top_p
        remove[..., 1:] = remove[..., :-1].clone()
        remove[..., 0] = False
        sorted_logits = sorted_logits.masked_fill(remove, float("-inf"))
        probabilities = torch.zeros_like(scaled).scatter(
            dim=-1,
            index=sorted_indices,
            src=torch.softmax(sorted_logits, dim=-1),
        )
    else:
        probabilities = torch.softmax(scaled, dim=-1)
    return torch.multinomial(probabilities, num_samples=1)


def generate_routed_turn(
    *,
    model: torch.nn.Module,
    tokenizer: Any,
    messages: list[dict[str, Any]],
    recorder: RouterTraceRecorder,
    conversation_turn: int,
    agent_step: int,
    max_new_tokens: int,
    stop_on_complete_protocol_object: bool = False,
    decoding_strategy: str = "greedy",
    temperature: float = 1.0,
    top_p: float = 1.0,
    assistant_protocol: str = "json_object",
    chat_template_kwargs: Mapping[str, Any] | None = None,
    channel_markers: Mapping[str, str] | None = None,
    extra_stop_token_ids: Sequence[int] = (),
) -> GenerationResult:
    """Generate one natural model turn and record routing for every processed token."""

    if max_new_tokens <= 0:
        raise ValueError("max_new_tokens must be positive")
    if assistant_protocol not in {"json_object", "json_action_or_text"}:
        raise ValueError(f"unsupported assistant protocol: {assistant_protocol}")
    template_messages = [
        {"role": message["role"], "content": message["content"]} for message in messages
    ]
    template_kwargs = dict(chat_template_kwargs or {})
    stop_token_ids = {int(value) for value in extra_stop_token_ids}
    encoded = tokenizer.apply_chat_template(
        template_messages,
        add_generation_prompt=True,
        return_tensors="pt",
        **template_kwargs,
    )
    prompt_ids = encoded if isinstance(encoded, torch.Tensor) else encoded["input_ids"]
    prompt_ids = prompt_ids.to(model.device)
    annotations = annotate_chat_prompt(
        tokenizer,
        messages,
        prompt_ids,
        generation_agent_step=agent_step,
        chat_template_kwargs=template_kwargs,
    )

    with recorder.record_step(
        phase="prefill",
        input_ids=prompt_ids,
        positions=range(prompt_ids.shape[1]),
        token_texts=_token_pieces(tokenizer, prompt_ids),
        token_roles=annotations.roles,
        conversation_turns=annotations.conversation_turns,
        agent_steps=annotations.agent_steps,
        tool_boundaries=annotations.tool_boundaries,
    ):
        with torch.inference_mode():
            output = model(
                input_ids=prompt_ids,
                use_cache=True,
                logits_to_keep=1,
                return_dict=True,
            )

    next_token = select_next_token(
        output.logits[:, -1, :],
        strategy=decoding_strategy,
        temperature=temperature,
        top_p=top_p,
    )
    generated: list[torch.Tensor] = []
    stop_reason = "length"
    for decode_index in range(max_new_tokens):
        generated.append(next_token.detach().cpu())
        absolute_position = prompt_ids.shape[1] + decode_index
        with recorder.record_step(
            phase="decode",
            input_ids=next_token,
            positions=(absolute_position,),
            token_texts=_token_pieces(tokenizer, next_token),
            token_roles=("assistant",),
            conversation_turns=(conversation_turn,),
            agent_steps=(agent_step,),
            tool_boundaries=(False,),
        ):
            with torch.inference_mode():
                output = model(
                    input_ids=next_token,
                    past_key_values=output.past_key_values,
                    use_cache=True,
                    logits_to_keep=1,
                    return_dict=True,
                )
        if int(next_token.item()) == tokenizer.eos_token_id:
            stop_reason = "eos"
            break
        if int(next_token.item()) in stop_token_ids:
            stop_reason = "stop_token"
            break
        if stop_on_complete_protocol_object:
            partial_ids = torch.cat(generated, dim=1)[0]
            partial_text = tokenizer.decode(
                partial_ids,
                skip_special_tokens=True,
                clean_up_tokenization_spaces=False,
            )
            parsed_partial = parse_assistant_output(
                partial_text,
                allow_natural_message=assistant_protocol == "json_action_or_text",
            )
            should_stop = (
                parsed_partial.kind != "invalid"
                if assistant_protocol == "json_object"
                else parsed_partial.kind == "action"
            )
            if should_stop:
                stop_reason = "protocol_complete"
                break
        next_token = select_next_token(
            output.logits[:, -1, :],
            strategy=decoding_strategy,
            temperature=temperature,
            top_p=top_p,
        )

    generated_ids = torch.cat(generated, dim=1)
    output_ids = [int(value) for value in generated_ids[0].tolist()]
    prompt_values = [int(value) for value in prompt_ids.detach().cpu()[0].tolist()]
    rendered_prompt = tokenizer.apply_chat_template(
        template_messages,
        add_generation_prompt=True,
        tokenize=False,
        **template_kwargs,
    )
    channel_boundaries = (
        find_channel_boundaries(tokenizer, output_ids, channel_markers)
        if channel_markers
        else {}
    )
    return GenerationResult(
        text=tokenizer.decode(
            generated_ids[0],
            skip_special_tokens=True,
            clean_up_tokenization_spaces=False,
        ),
        output_token_ids=output_ids,
        prompt_token_ids=prompt_values,
        prompt_token_count=len(prompt_values),
        output_token_count=len(output_ids),
        stop_reason=stop_reason,
        rendered_prompt=str(rendered_prompt),
        channel_boundaries=channel_boundaries,
    )
