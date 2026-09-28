"""Dataset G loader: episode token axis, harmony channel tags, labels, data discipline.

Unit tests are synthetic (no artifacts); the integration tests read the P0 probe run,
which is *not data* -- they only ever touch its routine arms, and one of them asserts that
the loader refuses its attack arms.  ``unittest`` style, like the rest of the suite.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from research_v2 import io_g  # noqa: E402

torch.set_num_threads(4)

P0_BATCH = ROOT / "artifacts" / "agent_v2" / "agent_v3_p0" / "batch"
P0_AVAILABLE = (P0_BATCH / "run_summary.json").exists()


def _step(
    *,
    agent_step: int,
    offset: int,
    count: int,
    segments: list[dict],
    first_decode: int = 0,
) -> dict:
    return {
        "agent_step": agent_step,
        "global_token_offset": offset,
        "output_token_count": count,
        "routing_step_index_first_decode": first_decode,
        "channel_segments": segments,
    }


def _segment(channel: str, header: int, body: int, body_end: int, terminator: int) -> dict:
    return {
        "channel": channel,
        "global_header_start": header,
        "global_body_start": body,
        "global_body_end": body_end,
        "header_start": header,
        "body_start": body,
        "body_end": body_end,
        "terminator_index": terminator,
    }


class ChannelTagTest(unittest.TestCase):
    def test_message_scope_covers_preamble_and_terminator(self) -> None:
        step = _step(
            agent_step=0,
            offset=0,
            count=20,
            segments=[
                _segment("analysis", 0, 3, 8, 8),
                # tokens 9, 10 are the <|start|>assistant preamble of the next message
                _segment("final", 11, 14, 19, 19),
            ],
        )
        spans = io_g.segment_spans([step])
        tags = io_g.channel_tag_array(spans, 20, scope="message")
        self.assertEqual(tags[:9], ("analysis",) * 9)
        # the gap is attributed to the message that follows it
        self.assertEqual(tags[9:11], ("final", "final"))
        self.assertEqual(tags[11:20], ("final",) * 9)
        self.assertNotIn(io_g.OTHER, tags)

    def test_body_scope_leaves_markers_outside_every_view(self) -> None:
        step = _step(
            agent_step=0,
            offset=0,
            count=20,
            segments=[_segment("analysis", 0, 3, 8, 8), _segment("final", 11, 14, 19, 19)],
        )
        spans = io_g.segment_spans([step])
        tags = io_g.channel_tag_array(spans, 20, scope="body")
        self.assertEqual(tags[3:8], ("analysis",) * 5)
        self.assertEqual(tags[0:3], (io_g.OTHER,) * 3)
        self.assertEqual(tags[8:14], (io_g.OTHER,) * 6)

    def test_terminator_shift_uses_the_step_offset(self) -> None:
        step = _step(
            agent_step=1,
            offset=40,
            count=10,
            segments=[
                {
                    "channel": "commentary",
                    "global_header_start": 40,
                    "global_body_start": 43,
                    "global_body_end": 48,
                    "header_start": 0,
                    "body_start": 3,
                    "body_end": 8,
                    "terminator_index": 8,
                }
            ],
        )
        spans = io_g.segment_spans([step])
        self.assertEqual(spans[0]["end"], 48)

    def test_boundary_fallback_when_segments_are_absent(self) -> None:
        step = {
            "agent_step": 0,
            "global_token_offset": 0,
            "output_token_count": 12,
            "routing_step_index_first_decode": 0,
            "channel_boundaries": {"analysis": 1, "commentary": -1, "final": 6},
        }
        spans = io_g.segment_spans([step])
        self.assertEqual([s["channel"] for s in spans], ["analysis", "final"])
        tags = io_g.channel_tag_array(spans, 12)
        self.assertEqual(tags[-1], "final")

    def test_channel_runs_are_maximal_and_view_restricted(self) -> None:
        tags = ("analysis",) * 4 + ("commentary",) * 3 + ("final",) * 5 + ("analysis",) * 2
        runs = io_g.channel_runs(tags, io_g.HARMONY_CHANNELS)
        self.assertEqual(runs, [(0, 4, "analysis"), (4, 7, "commentary"), (7, 12, "final"), (12, 14, "analysis")])
        dropped = io_g.channel_runs(tags, ("analysis", "final"))
        self.assertEqual(dropped, [(0, 4, "analysis"), (7, 12, "final"), (12, 14, "analysis")])
        final_only = io_g.channel_runs(tags, ("final",))
        self.assertEqual(final_only, [(7, 12, "final")])


class LabelTest(unittest.TestCase):
    def test_span_and_scalar_anchors_both_read(self) -> None:
        row = io_g.normalise_label_row(
            {"trace_id": "t", "e_analysis": [49, 52], "e_final": 84, "x": None}
        )
        self.assertEqual(row["e_analysis"], 49)
        self.assertEqual(row["e_analysis_span"], [49, 52])
        self.assertEqual(row["e_final"], 84)
        self.assertIsNone(row["x"])
        self.assertTrue(row["has_engagement"])
        self.assertFalse(row["silent"])

    def test_quality_axis_drives_the_filter(self) -> None:
        good = io_g.normalise_label_row(
            {
                "trace_id": "t",
                "quality": {
                    "behavior": "on_task",
                    "coverage": "partial",
                    "material_errors": False,
                    "unauthorized_tool_attempt": False,
                },
            }
        )
        bad = io_g.normalise_label_row(
            {
                "trace_id": "t",
                "quality": {
                    "behavior": "on_task",
                    "coverage": "none",
                    "material_errors": False,
                    "unauthorized_tool_attempt": False,
                },
            }
        )
        self.assertTrue(good["filter_pass"])
        self.assertFalse(bad["filter_pass"])
        self.assertIsNone(io_g.normalise_label_row({"trace_id": "t"})["filter_pass"])

    def test_silent_excludes_over_refusal(self) -> None:
        row = io_g.normalise_label_row({"trace_id": "t", "trajectory_class": "over_refusal"})
        self.assertFalse(row["silent"])
        self.assertTrue(row["over_refusal"])

    def test_label_file_keys_both_forms(self) -> None:
        path = Path(self.enterContext(_temp_dir())) / "labels.jsonl"
        path.write_text(
            json.dumps({"trace_id": "a", "episode_index": 1, "e_final": 3})
            + "\n"
            + json.dumps({"episode_id": "b#ep0", "e_analysis": 7})
            + "\n",
            encoding="utf-8",
        )
        labels = io_g.read_labels(path)
        self.assertEqual(sorted(labels), [("a", 1), ("b", 0)])


def _temp_dir():
    import tempfile

    return tempfile.TemporaryDirectory()


@unittest.skipUnless(P0_AVAILABLE, "agent_v3 P0 artifacts absent")
class P0LoaderTest(unittest.TestCase):
    """The P0 probe is 'not data'; only its routine arms are read here."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest: dict = {}
        cls.episodes = io_g.load_g(
            P0_BATCH, variants=io_g.NORMAL_VARIANTS, manifest=cls.manifest
        )

    def test_routine_episode_count_and_shapes(self) -> None:
        self.assertEqual(len(self.episodes), 22)
        for episode in self.episodes:
            self.assertEqual(episode.top_k_ids.shape[0], 24)
            self.assertEqual(episode.top_k_ids.shape[2], 4)
            self.assertEqual(episode.top_k_ids.shape[1], episode.token_count)
            self.assertEqual(episode.token_ids.shape[0], episode.token_count)
            self.assertEqual(len(episode.channel_tags), episode.token_count)
            self.assertLess(int(episode.top_k_ids.max()), 32)

    def test_router_metadata(self) -> None:
        router = self.episodes[0].router
        self.assertEqual(
            (router.num_moe_layers, router.num_experts, router.top_k), (24, 32, 4)
        )

    def test_episode_ids_are_unique_and_sessions_group_them(self) -> None:
        keys = [e.trace_id for e in self.episodes]
        self.assertEqual(len(set(keys)), len(keys))
        multi = [e for e in self.episodes if e.episode_count > 1]
        self.assertTrue(multi, "the P0 batch has multi-turn sessions")
        for episode in multi:
            self.assertEqual(episode.session_id, episode.source_trace_id)

    def test_channel_tags_cover_every_token(self) -> None:
        counts = {tag: 0 for tag in io_g.ALL_TAGS}
        for episode in self.episodes:
            for tag, value in episode.channel_counts().items():
                counts[tag] += value
        self.assertEqual(counts[io_g.OTHER], 0)
        for channel in io_g.HARMONY_CHANNELS:
            self.assertGreater(counts[channel], 0)

    def test_attack_arms_of_a_probe_run_are_refused(self) -> None:
        manifest: dict = {}
        io_g.load_g(P0_BATCH, manifest=manifest)
        self.assertEqual(manifest["skipped_probe_attack"], 8)
        with self.assertRaises(ValueError):
            io_g.load_g(P0_BATCH, variants=("attack",))

    def test_labels_are_empty_until_annotation(self) -> None:
        self.assertTrue(all(not e.labels for e in self.episodes))
        self.assertIsNone(self.episodes[0].filter_pass)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
