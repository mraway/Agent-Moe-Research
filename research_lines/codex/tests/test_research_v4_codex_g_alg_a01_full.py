"""Synthetic calibration/export/report tests. No experimental files are read."""
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from research_v2 import trm3_g
from research_v4 import codex_g_alg_a01_eval as ev, codex_g_alg_a01_io as ai
from research_v4 import codex_g_alg_a01_run as run
from research_v4 import codex_g_alg_a01_math as am, codex_g_alg_a01_audit as audit


def grid(n=20):
    return {"ends": np.arange(7, n+7), "tags": np.array(["analysis"]*5+["final"]*(n-5)),
            "ordinals": np.r_[np.arange(5), np.arange(n-5)]}


def metadata():
    result = {}
    for i in range(15):
        normal = i < 12
        result[f"g_dev|s{i}"] = {"variant": "clean" if normal else "attack", "filter_pass": True if normal else None,
          "scenario": f"s{i}", "fold": i%3, "episode_index": 0, "length_group": "short", "token_count": 60,
          "family": f"family{i%3}", "tier": "one", "domain_group": "d", "injection_channel": "user",
          "e": None if normal else 10, "x": None if normal else 20, "complete_16": not normal,
          "has_hit_look": not normal, "old_incomplete_16": False, "attack_bearing": not normal,
          "silent": False, "trajectory_class": "on_task" if normal else "executed"}
    return result


class FullTests(unittest.TestCase):
    def test_raw_fold_build_restore_without_training(self):
        meta={k:r for k,r in metadata().items() if r["variant"]=="clean"}; ps={}; grids={}
        rng=np.random.default_rng(31)
        for k in meta:
            ids=np.argsort(rng.random((24,12,32)),axis=-1)[...,:4]
            ps[k]=am.representations(ids,rng.normal(size=(24,12,32)))
            grids[k]={"ends":np.arange(7,12),"tags":np.array(["final"]*5),"ordinals":np.arange(5)}
        class Checked:
            def read(self,path,sha):
                body=path.read_bytes()
                if run.pf.digest(body)!=sha: raise AssertionError("changed bank")
                return body
        with tempfile.TemporaryDirectory(prefix="a01-fold-") as d, patch.object(run.pf,"routes",side_effect=lambda k,*args:ps[k]), patch.object(run,"OUT",Path(d)):
            a=Path(d)/"cal";b=Path(d)/"score";a.mkdir();b.mkdir()
            normal,record=run.raw_fold(0,"calibrate",meta,grids,{}, {},Checked(),a,run.Budget())
            with patch.object(am,"build_bank",run.prohibit):
                scored,_=run.raw_fold(0,"score",meta,grids,{}, {},Checked(),b,run.Budget(),record)
            for k,s in scored.items():
                for field in ("raw","donor_rows","donor_distances"):
                    np.testing.assert_array_equal(s[field],normal[k][field])

    def test_independent_full_bank_enumerator(self):
        rng=np.random.default_rng(99); records=[]
        for i in range(4):
            ids=np.argsort(rng.random((24,12,32)),axis=-1)[...,:4]
            p=am.representations(ids,rng.normal(size=(24,12,32)))
            records.append((f"s{i}",f"k{i}",np.arange(7,12),p))
        bank=am.build_bank(records); query=records[0][3]
        scores,rows,ds=am.neighbour_scores(query,[7],bank,"s0")
        self.assertLess(audit.check_nearest(query,7,bank,"s0",scores[0],rows[0],ds[0]),2e-6)

    def test_independent_family_bootstrap(self):
        a={str(i):i%4<2 for i in range(60)};b={str(i):i%5<2 for i in range(60)}
        groups={str(i):str(i%16) for i in range(60)}
        ref=trm3_g.cluster_bootstrap_paired(a,b,groups)
        self.assertEqual(audit.bootstrap(a,b,groups),ref["ci"])

    def test_shared_calibration_restore_and_literal_p(self):
        g = grid()
        for name in ev.NEW_CELLS:
            streams = [ev.stream(f"s{i}", g, np.linspace(0, 1, 20)+(i%4)*.1) for i in range(16)]
            c = ev.calibrate(name, streams[:8], streams[8:])
            before = c.state_dict(); restored = trm3_g.calibration_from_state(before)
            z, p = ev.score(name, streams[0], c)
            zz, pp = ev.score(name, streams[0], restored)
            np.testing.assert_array_equal(z, zz); np.testing.assert_array_equal(p, pp)
            maxima = np.array(before["channels"][name]["path_maxima"])
            expected = [(1+sum(maxima >= v))/(1+len(maxima)) for v in np.maximum.accumulate(z)]
            np.testing.assert_array_equal(p, expected)
            self.assertEqual(len(p), 20)  # raw windows are NOT averaged a second time
            self.assertEqual(c.horizon["H"], 2**31-1)

    def test_full_path_calibration_sees_late_extreme(self):
        s = [trm3_g.EpisodeStream(f"s{i}", np.arange(500), np.r_[np.zeros(450), np.ones(50)*i], ["final"]*500, np.arange(500)) for i in range(20)]
        c = ev.calibrate("W_ordered", s[:10], s[10:])
        _, p = ev.score("W_ordered", s[-1], c)
        self.assertEqual(len(p), 500); self.assertLess(p[-1], p[0])

    def test_all_attainable_grid_and_normal_only_selection(self):
        meta = {k:r for k,r in metadata().items() if r["variant"] == "clean"}
        p = {k:np.full((2, 7), .5) for k in meta}
        out = ev.freeze_points(meta, p, {n:[9, 9, 9] for n in ev.CELLS})
        self.assertEqual(out["points"]["matched"]["0.05"]["W_ordered"]["alpha"], .4)
        self.assertEqual(out["points"]["matched"]["0.05"]["S"]["measured_far"], 0)
        with self.assertRaises(ValueError): ev.freeze_points(metadata(), p, {n:[9]*3 for n in ev.CELLS})

    def test_no_look_normal_is_not_alarm_at_one(self):
        meta = {"empty": {"variant":"clean", "filter_pass":True}}
        out = ev.freeze_points(meta, {"empty":np.empty((0, 7))}, {n:[9]*3 for n in ev.CELLS})
        self.assertEqual(out["grid"]["S"][-1]["measured_far"], 0)

    def test_stream_archive_roundtrip_and_no_overwrite(self):
        streams = {"g_dev|s0":{**grid(), "raw":np.ones((20,4)), "p":np.ones((20,4))}}
        with tempfile.TemporaryDirectory(prefix="a01-arrays-") as d:
            path=Path(d)/"stream.npz"; ai.save_streams(path, streams, ev.NEW_CELLS)
            restored=ai.unpack(ai.npz_bytes(path.read_bytes()))
            for f in streams["g_dev|s0"]: np.testing.assert_array_equal(streams["g_dev|s0"][f], restored["g_dev|s0"][f])
            with self.assertRaises(FileExistsError): ai.save_streams(path, streams, ev.NEW_CELLS)

    def test_export_fit_hash_excludes_entire_scenario(self):
        meta = {"q": {"scenario":"same"}, "other_arm":{"scenario":"same"}, "donor":{"scenario":"new"}}
        streams={"q":{**grid(), "raw":np.ones((20,4))}}
        # Real keys require the shared batch|trace convention.
        meta["g_dev|q"] = meta.pop("q"); streams["g_dev|q"] = streams.pop("q")
        with tempfile.TemporaryDirectory(prefix="a01-export-") as d:
            p=Path(d)/"scores.jsonl"
            ai.export(p, streams, "U_mean", 0, 0, "fit", list(meta), meta, {"final/ep0":{"sha256":"abc"}})
            row=json.loads(p.read_text())
            self.assertEqual(row["fit_pool_sha256"], ai.fit_hash(["donor"]))
            self.assertEqual(row["schema"], "dataset-g-look-scores-1.0.0")
            self.assertEqual(len(row["scores"]), 20)

    def test_score_stage_training_tripwire(self):
        with patch.object(ev, "calibrate", run.prohibit):
            with self.assertRaises(AssertionError): ev.calibrate("U_mean", [], [])

    def test_metadata_counts_and_pre_E_penalty(self):
        meta=metadata(); streams={}
        for i,(k,r) in enumerate(meta.items()):
            ends=np.arange(7,40); p=np.ones((len(ends),7))
            if i==12: p[0:]=.01  # pre-E is a miss
            if i==13: p[8:]=.01  # between E and X, timely
            streams[k]={"ends":ends,"p":p}
        wp={n:{"alpha":.05} for n in ev.CELLS}
        result, ledger=ev.evaluate(meta, streams, {"matched":{"0.05":wp}}, {n:[9]*3 for n in ev.CELLS})
        row=result["readings"]["matched"]["0.05"]["W_ordered"]
        self.assertEqual(row["timely_recall"], {"count":1,"n":3,"rate":1/3})
        self.assertEqual(row["control_alarms"]["normal_all"]["n"], 12)
        self.assertEqual(row["classification"]["pre_E"], 1)
        self.assertEqual(ledger["matched/0.05"]["W_ordered"]["g_dev|s13"], 15)
        self.assertFalse(result["comparisons"]["0.05"]["W_ordered_minus_S"]["gain_gate_eligible"])

    def test_guards_normal_stage_and_sealed_score_stage(self):
        for normal in (True, False):
            g=ai.Guard(normal)
            with self.assertRaises(PermissionError): g.check_path(ai.ROOT/"artifacts/agent_v2/dataset_g/g_conf/x.json")
        g=ai.Guard(True)
        with self.assertRaises(PermissionError): g.check_path(ai.BASE/"m7_full_episode_v1/score/look_streams.npz")


if __name__ == "__main__": unittest.main()
