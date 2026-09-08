"""Additive model-config options used by the model pilot runs."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import torch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from phase_a import find_channel_boundaries  # noqa: E402
from run_agent_v2 import (  # noqa: E402
    HARMONY_CHANNEL_MARKERS,
    _channel_markers,
    _chat_template_kwargs,
    _quantization_config,
)


class _MarkerTokenizer:
    """Decodes a token id list by concatenating fixed pieces."""

    def __init__(self, pieces: dict[int, str]) -> None:
        self._pieces = pieces

    def decode(self, token_ids, **kwargs) -> str:  # noqa: ANN001
        return "".join(self._pieces[int(value)] for value in token_ids)


class QuantizationBlockTest(unittest.TestCase):
    def test_absent_or_null_block_keeps_the_unquantized_path(self) -> None:
        self.assertIsNone(_quantization_config({}))
        self.assertIsNone(_quantization_config({"quantization": None}))
        self.assertIsNone(_quantization_config({"quantization": {"method": None}}))
        self.assertIsNone(_quantization_config({"quantization": {"method": "none"}}))

    def test_bnb_nf4_block(self) -> None:
        config = _quantization_config(
            {
                "quantization": {
                    "method": "bnb_nf4",
                    "modules_to_not_convert": ["lm_head", "mlp.gate"],
                }
            }
        )
        self.assertTrue(config.load_in_4bit)
        self.assertEqual(config.bnb_4bit_quant_type, "nf4")
        self.assertEqual(config.bnb_4bit_compute_dtype, torch.bfloat16)
        self.assertTrue(config.bnb_4bit_use_double_quant)
        self.assertEqual(config.llm_int8_skip_modules, ["lm_head", "mlp.gate"])

    def test_mxfp4_block_does_not_dequantize(self) -> None:
        config = _quantization_config({"quantization": {"method": "mxfp4"}})
        self.assertFalse(config.dequantize)
        self.assertEqual(config.quant_method.value, "mxfp4")

    def test_unsupported_method_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported quantization method"):
            _quantization_config({"quantization": {"method": "awq"}})


class ChatTemplateAndChannelTest(unittest.TestCase):
    def test_chat_template_kwargs_default_to_empty(self) -> None:
        self.assertEqual(_chat_template_kwargs({}), {})
        self.assertEqual(
            _chat_template_kwargs({"chat_template_kwargs": {"reasoning_effort": "low"}}),
            {"reasoning_effort": "low"},
        )

    def test_generation_channels_flag(self) -> None:
        self.assertEqual(_channel_markers({}), {})
        self.assertEqual(_channel_markers({"generation_channels": False}), {})
        self.assertEqual(_channel_markers({"generation_channels": True}), HARMONY_CHANNEL_MARKERS)
        self.assertEqual(
            _channel_markers({"generation_channels": {"final": "<|start|>final"}}),
            {"final": "<|start|>final"},
        )

    def test_channel_boundaries_locate_the_final_channel(self) -> None:
        pieces = {
            1: "<|channel|>",
            2: "analysis",
            3: "<|message|>",
            4: "thinking",
            5: "<|end|>",
            6: "<|channel|>",
            7: "final",
            8: "<|message|>",
            9: "answer",
        }
        tokenizer = _MarkerTokenizer(pieces)
        boundaries = find_channel_boundaries(
            tokenizer, list(pieces), HARMONY_CHANNEL_MARKERS
        )
        self.assertEqual(boundaries["analysis"], 1)
        self.assertEqual(boundaries["final"], 6)
        self.assertEqual(boundaries["commentary"], -1)


class PilotConfigFileTest(unittest.TestCase):
    def _load(self, name: str) -> dict:
        return json.loads((ROOT / "configs" / name).read_text(encoding="utf-8"))

    def test_qwen3_pilot_model_config(self) -> None:
        config = self._load("pilot_qwen3_30b_a3b_nf4.json")
        self.assertEqual(config["model_id"], "Qwen/Qwen3-30B-A3B-Instruct-2507")
        self.assertEqual(config["cache_dir"], "artifacts/hf_cache")
        self.assertEqual(config["dtype"], "bfloat16")
        self.assertEqual(config["device_map"], "cuda")
        self.assertEqual(config["attn_implementation"], "sdpa")
        self.assertEqual(config["router_adapter"], "qwen3_moe")
        self.assertEqual(config["quantization"]["method"], "bnb_nf4")
        self.assertIn("mlp.gate", config["quantization"]["modules_to_not_convert"])
        self.assertIsNotNone(_quantization_config(config))

    def test_gpt_oss_pilot_model_config(self) -> None:
        config = self._load("pilot_gpt_oss_20b_mxfp4.json")
        self.assertEqual(config["model_id"], "openai/gpt-oss-20b")
        self.assertEqual(config["attn_implementation"], "eager")
        self.assertEqual(config["router_adapter"], "gpt_oss")
        self.assertEqual(config["quantization"]["method"], "mxfp4")
        self.assertEqual(config["chat_template_kwargs"], {"reasoning_effort": "low"})
        self.assertTrue(config["generation_channels"])
        self.assertEqual(_channel_markers(config), HARMONY_CHANNEL_MARKERS)

    def test_both_configs_carry_main_or_a_pinned_commit_with_a_note(self) -> None:
        """Unpinned before a pilot runs; a full commit sha once one has run.

        The plan requires the pilot agent to resolve and pin the checkpoint sha
        into ``revision`` before generating, so a pinned config is the expected
        post-run state and only an abbreviated or branch-like value is a bug.
        """

        for name in ("pilot_qwen3_30b_a3b_nf4.json", "pilot_gpt_oss_20b_mxfp4.json"):
            config = self._load(name)
            revision = config["revision"]
            note = str(config["_revision_note"])
            self.assertTrue(note.strip(), f"{name}: _revision_note must not be empty")
            if revision == "main":
                self.assertIn("commit", note.lower())
            else:
                self.assertRegex(
                    revision,
                    r"^[0-9a-f]{40}$",
                    f"{name}: revision must be 'main' or a full commit sha",
                )


if __name__ == "__main__":
    unittest.main()
