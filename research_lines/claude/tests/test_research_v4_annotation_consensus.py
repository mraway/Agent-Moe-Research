"""Tests for scripts/research_v4/annotation_consensus.py (pre-sweep consensus rule)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "research_v4"))

import annotation_consensus as ac  # noqa: E402


def _row(case_id: str, reviewer: str, material_errors=None, coverage="full", behavior="on_task"):
    return {
        "case_id": case_id,
        "reviewer": reviewer,
        "trajectory_class": "silent",
        "events": {"e_analysis": None, "e_final": None, "c": None, "x": None},
        "recovery_spans": [],
        "quality": {
            "behavior": behavior,
            "coverage": coverage,
            "material_errors": material_errors or [],
            "unauthorized_tool_attempt": False,
            "citation": "ok",
            "unavailable_actions": [],
        },
        "overall_confidence": "high",
        "note": "",
    }


def _write(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


class ConsensusRuleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.packet = root / "packet.jsonl"
        # packet order deliberately shuffled on disk
        _write(self.packet, [{"case_id": "c2", "packet_order": 2}, {"case_id": "c0", "packet_order": 0}, {"case_id": "c1", "packet_order": 1}, {"case_id": "c3", "packet_order": 3}])
        self.ann = root / "ann"
        _write(self.ann / "A" / "all.jsonl", [_row("c0", "A"), _row("c1", "A", material_errors=["x"]), _row("c2", "A"), _row("c3", "A", coverage="none")])
        _write(self.ann / "disagreements.jsonl", [{"case_id": "c1", "differing_axes": ["material_errors_present"]}, {"case_id": "c2", "differing_axes": ["coverage"]}])
        _write(self.ann / "adjudication" / "chunk_0.jsonl", [_row("c1", "adj")])

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_adjudicated_else_a_in_packet_order(self) -> None:
        res = ac.build(self.packet, self.ann, partial=True, batch_size=2)
        self.assertEqual([r["case_id"] for r in res["consensus"]], ["c0", "c1", "c2", "c3"])
        self.assertEqual([p["source"] for p in res["provenance"]], ["annotator_A", "adjudication", "annotator_A", "annotator_A"])
        self.assertEqual(res["consensus"][1]["reviewer"], "adj")
        self.assertEqual(res["summary"]["disputed_without_adjudication"], ["c2"])
        self.assertEqual(res["provenance"][2]["flag"], "disputed_without_adjudication")
        self.assertIsNone(res["provenance"][1]["flag"])

    def test_partial_required_when_adjudication_missing(self) -> None:
        with self.assertRaises(ValueError):
            ac.build(self.packet, self.ann, partial=False)

    def test_filter_pass_and_sweep_lists(self) -> None:
        res = ac.build(self.packet, self.ann, partial=True, batch_size=2)
        # c1 adjudicated row has no material errors -> passes; c3 fails on coverage only
        self.assertEqual(res["filter_pass"], {"c0": True, "c1": True, "c2": True, "c3": False})
        self.assertEqual(res["lists"]["batch_00"], {"true": ["c0", "c1"], "false_by_material_errors_only": []})
        self.assertEqual(res["lists"]["batch_01"], {"true": ["c2"], "false_by_material_errors_only": []})
        # a row failing solely on material_errors lands in the second list
        _write(self.ann / "adjudication" / "chunk_0.jsonl", [_row("c1", "adj", material_errors=["price wrong"])])
        res = ac.build(self.packet, self.ann, partial=True, batch_size=2)
        self.assertEqual(res["lists"]["batch_00"], {"true": ["c0"], "false_by_material_errors_only": ["c1"]})
        self.assertEqual(res["summary"]["n_false_by_material_errors_only"], 1)

    def test_rejects_double_or_stray_adjudication(self) -> None:
        _write(self.ann / "adjudication" / "chunk_1.jsonl", [_row("c1", "adj2")])
        with self.assertRaises(ValueError):
            ac.build(self.packet, self.ann, partial=True)
        _write(self.ann / "adjudication" / "chunk_1.jsonl", [_row("c0", "adj2")])
        with self.assertRaises(ValueError):
            ac.build(self.packet, self.ann, partial=True)

    def test_write_outputs_roundtrip(self) -> None:
        res = ac.build(self.packet, self.ann, partial=True, batch_size=2)
        ac.write_outputs(res, self.ann)
        rows = [json.loads(l) for l in (self.ann / "pre_sweep_consensus.jsonl").read_text().splitlines()]
        self.assertEqual(len(rows), 4)
        lists = json.loads((self.ann / "pre_sweep_filter_pass_lists.json").read_text())
        self.assertEqual(lists["summary"]["n_rows"], 4)
        self.assertIn("batch_00", lists["batches"])


if __name__ == "__main__":
    unittest.main()
