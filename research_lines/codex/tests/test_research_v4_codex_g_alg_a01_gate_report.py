"""Synthetic tests for risk-gate reporting corrections; no data access."""
import unittest
from research_v4 import codex_g_alg_a01_gate_report as gates


class GateTests(unittest.TestCase):
    def sample(self):
        meta={};c={}
        for f in range(3):
            for i in range(4):
                meta[f"f{f}n{i}"]={"variant":"clean" if i%2 else "benign_control","fold":f,"filter_pass":True,
                                  "scenario":f"f{f}s{i//2}","length_group":"short"}
            c[f"normal/fold/{f}/filtered"]={"rate":.05,"n":4}
        for den in ("all","filtered"):
            c[f"normal_{den}"]={"rate":.05,"n":12}
            c[f"normal_scenario_{den}"]={"rate":.1,"n":6}
            c[f"normal/length_group/short/{den}"]={"rate":.05,"n":12}
            c[f"normal/variant/clean/{den}"]={"rate":.05,"n":6}
            c[f"normal/variant/benign_control/{den}"]={"rate":.08,"n":6}
        c["silent_injected"]={"rate":.075,"n":40}
        return meta,{"control_alarms":c}

    def test_eff_uses_shared_n_plus_one_and_f2_filtered(self):
        m,r=self.sample();r["control_alarms"]["normal/variant/benign_control/filtered"]["rate"]=.3
        x=gates.correct(m,r,"W_ordered",.1,[104,95,94],True)
        self.assertEqual(x["F1"]["alpha_eff_by_fold"][0],10/105)
        self.assertEqual(x["F2a"]["filtered"]["status"],"FAIL")
        self.assertEqual(x["F2a"]["all"]["status"],"PASS")
        self.assertIn("M-only",x["F6"]["reason"])

    def test_disabled_workpoint_fails_attainability(self):
        m,r=self.sample();x=gates.correct(m,r,"W_ordered",0,[104,95,94],True)
        self.assertEqual(x["N4"]["0"]["status"],"FAIL")
        self.assertEqual(x["F1"]["alpha_eff_by_fold"],[0,0,0])
        self.assertFalse(x["F8"]["rank_at_least_one_per_fold"])


if __name__=="__main__":unittest.main()
