"""G-bridge (v2.5-layout gpt-oss traces) support in the dataset-G loader.

``docs/research_v4/detector_harness_g.md`` section 11 open item 6: the G-bridge layout had
only an untested fallback path.  These tests cover the pieces that make the fallback exact
-- the token axis rebuilt from ``manifest.jsonl``, the channel spans recomputed with
``agent_v3.harmony``, the arm read off the path -- on synthetic traces, plus a small
integration block against the real batch when it is present.

Nothing here touches attack-arm routing: the real-data block loads
``variants=io_g.NORMAL_VARIANTS`` only, which makes ``load_g`` skip the attack trace before
any shard is opened.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from research_v2 import io_g  # noqa: E402

GBRIDGE = ROOT / "artifacts" / "agent_v2" / "g_bridge_gpt_oss_20b" / "batch"

# A miniature harmony vocabulary: ids are arbitrary but distinct, exactly as a capture
# manifest records them.
PIECES = {
    1: "<|start|>",
    2: "<|message|>",
    3: "<|channel|>",
    4: "<|constrain|>",
    5: "<|end|>",
    7: "<|return|>",
    10: "analysis",
    11: "final",
    20: "Need",
    21: " to",
    22: "Your",
    23: " order",
    24: ".",
    30: "assistant",
}
#      0  1   2  3   4  5  6  7   8  9  10  11  12  13  14
SEQ = [3, 10, 2, 20, 21, 5, 1, 30, 3, 11, 2, 22, 23, 24, 7]


def _vocabulary() -> io_g.TokenTextVocabulary:
    return io_g.TokenTextVocabulary(PIECES)


class TokenTextVocabularyTest(unittest.TestCase):
    def test_pieces_resolve_and_decode(self) -> None:
        vocab = _vocabulary()
        self.assertEqual(vocab.convert_tokens_to_ids("<|channel|>"), 3)
        self.assertEqual(vocab.decode([20, 21]), "Need to")

    def test_unseen_pieces_get_sentinels_no_real_id_can_equal(self) -> None:
        vocab = _vocabulary()
        first = vocab.piece_id("<|call|>")
        self.assertLess(first, 0)
        self.assertEqual(first, vocab.piece_id("<|call|>"))
        self.assertNotEqual(first, vocab.piece_id("<|constrain_missing|>"))
        self.assertNotIn(first, PIECES)


class SpansFromTokenIdsTest(unittest.TestCase):
    def test_exact_spans_match_the_harmony_segmentation(self) -> None:
        spans = io_g.spans_from_token_ids(SEQ, _vocabulary())
        self.assertEqual([s["channel"] for s in spans], ["analysis", "final"])
        analysis, final = spans
        self.assertEqual(
            (analysis["header_start"], analysis["body_start"], analysis["body_end"], analysis["end"]),
            (0, 3, 5, 5),
        )
        self.assertEqual(
            (final["header_start"], final["body_start"], final["body_end"], final["end"]),
            (8, 11, 14, 14),
        )

    def test_the_episode_offset_shifts_every_index(self) -> None:
        spans = io_g.spans_from_token_ids(SEQ, _vocabulary(), offset=100, agent_step=2)
        self.assertEqual(spans[0]["header_start"], 100)
        self.assertEqual(spans[1]["end"], 114)
        self.assertEqual({s["agent_step"] for s in spans}, {2})

    def test_message_scope_gives_the_start_assistant_gap_to_the_next_message(self) -> None:
        tags = io_g.channel_tag_array(io_g.spans_from_token_ids(SEQ, _vocabulary()), len(SEQ))
        self.assertEqual(tags, ("analysis",) * 6 + ("final",) * 9)
        self.assertEqual(tags[6], "final")  # <|start|>
        self.assertEqual(tags[7], "final")  # assistant

    def test_body_scope_keeps_markers_outside_every_view(self) -> None:
        tags = io_g.channel_tag_array(
            io_g.spans_from_token_ids(SEQ, _vocabulary()), len(SEQ), scope="body"
        )
        self.assertEqual(tags[0], io_g.OTHER)
        self.assertEqual(tags[3:5], ("analysis", "analysis"))
        self.assertEqual(tags[-1], io_g.OTHER)


class SegmentSpansDispatchTest(unittest.TestCase):
    """A step without ``channel_segments`` uses the vocabulary when one is available."""

    STEP = {
        "agent_step": 0,
        "global_token_offset": 0,
        "output_token_count": len(SEQ),
        "output_token_ids": SEQ,
        # deliberately the coarse boundaries the v2.5 controller writes
        "channel_boundaries": {"analysis": 1, "commentary": -1, "final": 9},
    }

    def test_vocabulary_path_is_exact(self) -> None:
        spans = io_g.segment_spans([self.STEP], vocabulary=_vocabulary())
        self.assertEqual([s["body_start"] for s in spans], [3, 11])
        self.assertEqual([s["body_end"] for s in spans], [5, 14])

    def test_boundary_fallback_is_still_used_without_a_vocabulary(self) -> None:
        spans = io_g.segment_spans([self.STEP])
        self.assertEqual([s["channel"] for s in spans], ["analysis", "final"])
        # the coarse fallback cannot see <|end|>: it runs analysis up to the next marker
        self.assertNotEqual(spans[0]["body_end"], 5)


class TokenAxisTest(unittest.TestCase):
    """``routing_step_index_first_decode`` / ``global_token_offset`` from the manifest."""

    def _run_dir(self, directory: str, rows) -> Path:
        path = Path(directory)
        with (path / "manifest.jsonl").open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row) + "\n")
        return path

    def _rows(self, prefill_len: int, blocks) -> list[dict]:
        rows = [
            {
                "step_index": 0,
                "phase": "prefill",
                "token_ids": list(range(900, 900 + prefill_len)),
                "token_texts": ["p"] * prefill_len,
                "agent_steps": [0] * prefill_len,
            }
        ]
        index = 1
        for agent_step, ids in blocks:
            for token in ids:
                rows.append(
                    {
                        "step_index": index,
                        "phase": "decode",
                        "token_ids": [token],
                        "token_texts": [PIECES.get(token, "x")],
                        "agent_steps": [agent_step],
                    }
                )
                index += 1
        return rows

    def test_single_step_axis_is_rebuilt(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            path = self._run_dir(name, self._rows(3, [(0, SEQ)]))
            io_g._MANIFEST_CACHE.pop(str(path), None)
            steps = io_g.with_token_axis(
                path, [{"agent_step": 0, "output_token_count": len(SEQ), "output_token_ids": SEQ}]
            )
        self.assertEqual(steps[0]["routing_step_index_first_decode"], 1)
        self.assertEqual(steps[0]["global_token_offset"], 0)

    def test_a_second_step_gets_its_own_first_shard_and_offset(self) -> None:
        first, second = SEQ[:6], SEQ[6:]
        with tempfile.TemporaryDirectory() as name:
            path = self._run_dir(name, self._rows(2, [(0, first), (1, second)]))
            io_g._MANIFEST_CACHE.pop(str(path), None)
            steps = io_g.with_token_axis(
                path,
                [
                    {"agent_step": 0, "output_token_count": len(first), "output_token_ids": first},
                    {"agent_step": 1, "output_token_count": len(second), "output_token_ids": second},
                ],
            )
        self.assertEqual([s["routing_step_index_first_decode"] for s in steps], [1, 7])
        self.assertEqual([s["global_token_offset"] for s in steps], [0, 6])

    def test_a_disagreeing_manifest_raises_instead_of_scoring(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            path = self._run_dir(name, self._rows(2, [(0, SEQ)]))
            io_g._MANIFEST_CACHE.pop(str(path), None)
            wrong = list(SEQ)
            wrong[4] = 999
            with self.assertRaises(ValueError):
                io_g.with_token_axis(
                    path,
                    [{"agent_step": 0, "output_token_count": len(SEQ), "output_token_ids": wrong}],
                )

    def test_an_agent_step_disagreement_raises(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            path = self._run_dir(name, self._rows(2, [(3, SEQ)]))
            io_g._MANIFEST_CACHE.pop(str(path), None)
            with self.assertRaises(ValueError):
                io_g.with_token_axis(
                    path,
                    [{"agent_step": 0, "output_token_count": len(SEQ), "output_token_ids": SEQ}],
                )

    def test_an_agent_v3_step_is_returned_untouched(self) -> None:
        step = {
            "agent_step": 0,
            "output_token_count": 3,
            "routing_step_index_first_decode": 42,
            "global_token_offset": 7,
        }
        self.assertEqual(io_g.with_token_axis(Path("/nonexistent"), [step]), [step])


class VariantFromPathTest(unittest.TestCase):
    def test_path_supplies_the_arm_when_the_trace_does_not(self) -> None:
        self.assertEqual(io_g._variant_of({}, Path("/x/scen/benign_control")), "benign_control")

    def test_a_disagreement_between_metadata_and_layout_raises(self) -> None:
        with self.assertRaises(ValueError):
            io_g._variant_of({"perturbation": {"arm": "clean"}}, Path("/x/scen/attack"))

    def test_a_non_arm_directory_leaves_the_metadata_alone(self) -> None:
        self.assertEqual(
            io_g._variant_of({"perturbation": {"arm": "clean"}}, Path("/x/whatever")), "clean"
        )


class EpisodeIndexFallbackTest(unittest.TestCase):
    def test_conversation_turns_are_ranked_when_no_episode_index_exists(self) -> None:
        trace = {
            "events": [
                {"kind": "model_generation", "conversation_turn": 1, "agent_step": 1},
                {"kind": "model_generation", "conversation_turn": 1, "agent_step": 0},
                {"kind": "model_generation", "conversation_turn": 3, "agent_step": 0},
                {"kind": "assistant_message", "conversation_turn": 3},
            ]
        }
        steps = io_g._generation_steps(trace)
        self.assertEqual(sorted(steps), [0, 1])
        self.assertEqual([s["agent_step"] for s in steps[0]], [0, 1])
        self.assertEqual(len(steps[1]), 1)

    def test_an_explicit_episode_index_still_wins(self) -> None:
        trace = {
            "events": [
                {"kind": "model_generation", "conversation_turn": 5, "episode_index": 2, "agent_step": 0}
            ]
        }
        self.assertEqual(sorted(io_g._generation_steps(trace)), [2])


@unittest.skipUnless(GBRIDGE.exists(), "the G-bridge batch is not present")
class GBridgeRealTraceTest(unittest.TestCase):
    """One scenario of the real batch; normal arms only, attack routing never opened."""

    SCENARIO = "b2-f0-001-order_and_knowledge-free-verse"

    @classmethod
    def setUpClass(cls) -> None:
        cls.episodes = io_g.load_g(
            GBRIDGE / cls.SCENARIO, variants=io_g.NORMAL_VARIANTS, cache_dir=None
        )

    def test_only_the_normal_arms_are_returned(self) -> None:
        self.assertEqual(
            sorted(e.variant for e in self.episodes), ["benign_control", "clean"]
        )
        self.assertTrue(all(e.variant != io_g.ATTACK for e in self.episodes))

    def test_one_episode_per_trace_with_the_gpt_oss_geometry(self) -> None:
        for episode in self.episodes:
            self.assertEqual(episode.episode_index, 0)
            self.assertEqual(
                tuple(episode.top_k_ids.shape), (24, episode.token_count, 4)
            )
            self.assertEqual(episode.router.num_experts, 32)

    def test_every_token_carries_a_channel_and_none_is_left_over(self) -> None:
        for episode in self.episodes:
            counts = episode.channel_counts()
            self.assertEqual(counts[io_g.OTHER], 0)
            self.assertEqual(sum(counts.values()), episode.token_count)

    def test_the_recomputed_spans_agree_with_generation_channels(self) -> None:
        """``channel_boundaries[c]`` indexes the channel-name token of channel ``c``."""

        for episode in self.episodes:
            trace = json.loads((episode.trace_dir / "trace.json").read_text(encoding="utf-8"))
            boundaries = trace["generation_channels"]["steps"][0]["boundaries"]
            for channel, index in boundaries.items():
                if index < 0:
                    continue
                self.assertEqual(episode.channel_tags[index], channel)

    def test_analysis_precedes_final_on_the_token_axis(self) -> None:
        for episode in self.episodes:
            spans = {s["channel"]: s for s in episode.channel_segments}
            self.assertIn("final", spans)
            if "analysis" in spans:
                self.assertLess(spans["analysis"]["end"], spans["final"]["header_start"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
