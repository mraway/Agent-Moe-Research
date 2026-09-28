"""Synthetic M15 protocol, control selection and distribution geometry tests."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"src")); sys.path.insert(0, str(ROOT/"scripts"))
from research_v2 import io_g
from research_v4 import codex_g_mech_m15 as run, codex_g_mech_m15_math as mm, codex_g_mech_m15_audit as audit


def vocabulary():
    return io_g.TokenTextVocabulary({1:"<|start|>", 2:"<|message|>", 3:"<|channel|>", 4:"<|constrain|>",
                                    5:"<|end|>", 6:"<|call|>", 7:"<|return|>", 10:"analysis", 11:"final", 12:"commentary",
                                    **{i:f" word{i}" for i in range(20, 90)}})


def axis(first=12, second=40):
    tokens = np.array([3,10,2]+[20]*first+[5,3,11,2]+[21]*second+[7])
    return mm.token_axis(tokens, [len(tokens)], vocabulary())


def meta(n):
    keys = np.array([f"ep{i}" for i in range(n)])
    rows = {str(k):dict(variant="clean", filter_pass=True, scenario=f"s{i}", fold=0, episode_index=0,
                        family="", tier="T0") for i,k in enumerate(keys)}
    return keys, rows


class ProtocolTest(unittest.TestCase):
    def test_header_is_not_previous_behavior(self):
        a = axis(); start = a["bodies"][1]["start"]
        self.assertEqual(a["tokens"][start-1], 2); self.assertEqual(mm.ROLES[a["roles"][start-1]], "header")
        ctx, why = mm.body_context(a, 0, start)
        self.assertEqual(why, "available"); self.assertEqual(ctx["mode"], "previous_message")
        self.assertEqual(ctx["pre"], list(range(7,15))); self.assertTrue((a["roles"][ctx["pre"]] == 2).all())

    def test_short_same_body_baseline_is_not_padded(self):
        a = axis(); start = a["bodies"][1]["start"]
        ctx, _ = mm.body_context(a, 0, start+3)
        self.assertEqual(ctx["pre"], [start,start+1,start+2]); self.assertEqual(ctx["width"], 3)
        self.assertEqual(ctx["mode"], "same_body")

    def test_no_earlier_body_fallback(self):
        a = axis(first=3); start = a["bodies"][1]["start"]
        self.assertEqual(mm.body_context(a, 0, start), (None, "previous_body_shorter_than_8"))
        self.assertEqual(mm.body_context(a, 0, a["bodies"][0]["start"]), (None, "no_previous_body"))

    def test_protocol_anchor_not_treated_as_body(self):
        a = axis(); self.assertEqual(mm.body_context(a, 0, 2), (None,"anchor_not_body"))

    def test_future_respects_label_and_body_end(self):
        a = axis(second=20); start = a["bodies"][1]["start"]
        ctx, _ = mm.body_context(a, 0, start, start+10)
        self.assertEqual(ctx["post"][:11], list(range(start,start+11)))
        self.assertEqual(ctx["post"][11:], [-1]*21)

    def test_independent_context_construction(self):
        for first in (3,8,12):
            a = axis(first=first)
            for t in range(len(a["tokens"])):
                actual, _ = mm.body_context(a, 0, t)
                self.assertEqual(actual, audit.brute_context(a, 0, t))

    def test_cross_step_preserved_in_signature(self):
        tokens = np.array([3,10,2]+[20]*9+[5]+[3,11,2]+[21]*35+[7])
        a = mm.token_axis(tokens, [13,len(tokens)-13], vocabulary()); r=a["bodies"][1]["start"]
        ctx, _=mm.body_context(a, 0, r)
        self.assertEqual(ctx["step_delta"], 1); self.assertEqual(ctx["pre_channel"], "analysis")


class MatchingTest(unittest.TestCase):
    def fixture(self):
        axes = {i:axis(second=40+i) for i in range(5)}; keys, rows = meta(5)
        rows["ep0"].update(variant="attack",filter_pass=False)
        q, _=mm.body_context(axes[0],0,axes[0]["bodies"][1]["start"])
        return axes, keys, rows, q

    def test_exact_signature_and_independent_selection(self):
        axes, keys, rows, q=self.fixture(); bank=mm.normal_candidates(axes,rows,keys,{0})
        chosen=mm.choose(q,bank[mm.signature(q,rows["ep0"])],rows,keys)
        self.assertEqual([d["episode"] for d in chosen],[1,2,3]); self.assertEqual(chosen,audit.brute_choose(q,axes,rows,keys))

    def test_distinct_scenarios_not_just_distinct_episodes(self):
        axes,keys,rows,q=self.fixture(); rows["ep2"]["scenario"]=rows["ep1"]["scenario"]
        bank=mm.normal_candidates(axes,rows,keys,{0}); chosen=mm.choose(q,bank[mm.signature(q,rows["ep0"])],rows,keys)
        self.assertEqual([d["episode"] for d in chosen],[1,3,4])

    def test_no_future_coverage_selection(self):
        axes,keys,rows,q=self.fixture(); bank=mm.normal_candidates(axes,rows,keys,{0}); cs=bank[mm.signature(q,rows["ep0"])]
        before=mm.choose(q,cs,rows,keys); cs[0]["post"]=[-1]*32
        after=mm.choose(q,cs,rows,keys)
        self.assertEqual([x["episode"] for x in before],[x["episode"] for x in after])

    def test_same_current_token_required(self):
        axes,keys,rows,q=self.fixture(); axes[1]["tokens"][q["anchor"]]=22
        bank=mm.normal_candidates(axes,rows,keys,{0}); chosen=mm.choose(q,bank[mm.signature(q,rows["ep0"])],rows,keys)
        self.assertEqual([d["episode"] for d in chosen],[2,3,4])


class GeometryTest(unittest.TestCase):
    def vectors(self,n=5):
        rng=np.random.default_rng(2); ids=np.tile(np.arange(4),(n,24,1)); logits=rng.normal(size=(n,24,32))
        return mm.geometry.routing_vectors(ids,logits)

    def test_selected_W_and_full_P_differ(self):
        v=self.vectors(); self.assertTrue((v[:,1,:,4:]==0).all()); self.assertTrue((v[:,2,:,4:]>0).all())
        np.testing.assert_allclose(v.sum(-1),1,atol=1e-14)

    def test_no_background_with_one_donor(self):
        v=self.vectors(6); rare=np.zeros((24,32),bool)
        x=mm.per_point_geometry(v[:3],v[None,3:],rare)
        self.assertTrue(np.isfinite(x[:,:,:,0]).all()); self.assertTrue(np.isnan(x[:,:,:,1:]).all())

    def test_independent_point_geometry_and_partition(self):
        v=self.vectors(9); rare=np.zeros((24,32),bool); rare[:,:2]=True
        ds=np.array([v[3:6],v[6:]])
        a=mm.per_point_geometry(v[:3],ds,rare); b=audit.direct_frame(v[:3],ds,rare)
        np.testing.assert_allclose(a,b,atol=1e-12)
        for rep in range(3): np.testing.assert_allclose(a[:,4*rep],a[:,4*rep+1]+a[:,4*rep+2],atol=1e-12)

    def test_mean_distance_not_distance_of_mean(self):
        q=np.zeros((2,3,1,2)); q[0,:,:,0]=1; q[1,:,:,1]=1
        d=q[::-1].copy(); rare=np.ones((1,2),bool)
        self.assertEqual(mm.per_point_geometry(q,d[None],rare)[:,0,0,0].mean(),1)
        self.assertEqual(.5*abs(q.mean(0)-d.mean(0)).sum(-1)[0,0],0)

    def test_band_aggregation(self):
        v=np.broadcast_to(np.arange(24)[None,None,:,None,None],(2,12,24,3,3)).astype(float)
        for b,want in zip(mm.BANDS,(11.5,3.5,11.5,19.5),strict=True): np.testing.assert_allclose(mm.band_values(v,b),want)

    def test_common_cohort_does_not_replace_missing_donor(self):
        actors=[dict(episode=i,anchor=8,pre=list(range(8)),post=list(range(8,40))) for i in range(3)]
        actors[2]["post"][24:]=[-1]*8
        records=[dict(query=actors[0],donors=actors[1:])]; points=mm.point_list(records)
        v=self.vectors(len(points)); keys,meta_=meta(3); raw=np.zeros((len(points),2))
        out=mm.measurements(records,points,v,raw,{0:np.zeros((24,32),bool)},meta_,keys)
        self.assertEqual(out["chosen_donors"][0,0,0].tolist(),[True,True,False])
        self.assertEqual(out["chosen_donors"][0,1,0].tolist(),[True,False,False])
        self.assertTrue(np.isfinite(out["values"][0,1,0,:,:,0,:]).all())
        self.assertTrue(np.isnan(out["values"][0,1,0,:,:,1:,:]).all())


class GuardTest(unittest.TestCase):
    def test_only_fixed_tokenizer_not_model_weights(self):
        guard=run.M15AccessGuard(ROOT); guard.check_path(run.TOKENIZER)
        for p in (run.TOKENIZER.parent/"model.safetensors",ROOT/"artifacts/agent_v2/dataset_g/g_conf/trace.json",
                  ROOT/"artifacts/agent_v2/dataset_g/annotations/g_conf/final_unblinded.jsonl"):
            with self.assertRaises(PermissionError): guard.check_path(p)

    def test_caches_and_prior_outputs_remain_readonly(self):
        guard=run.M15AccessGuard(ROOT)
        for p in (run.cache_path("g_dev|g-dev-001--clean#ep0"),run.m14.OUT/"result.json"):
            guard.check_path(p)
            with self.assertRaises(PermissionError): guard.audit("open",(str(p),None,os.O_WRONLY))


if __name__ == "__main__": unittest.main()
