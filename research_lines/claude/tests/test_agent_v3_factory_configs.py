"""The written dataset G configs: schema, manifest, validators, reproducibility."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_v3 import validate_agent_v3_experiment  # noqa: E402
from agent_v3.factory import build_all, sha256_file  # noqa: E402
from agent_v3.factory import validate as factory_validate  # noqa: E402

CONFIG_DIR = ROOT / "configs" / "dataset_g"
SUBSETS = ("g_fit", "g_cal", "g_dev", "g_session", "g_medium", "g_conf", "g_conf2")


class WrittenConfigTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.configs = {
            subset: json.loads((CONFIG_DIR / f"{subset}.json").read_text(encoding="utf-8"))
            for subset in SUBSETS
        }
        cls.manifest = json.loads((CONFIG_DIR / "manifest.json").read_text(encoding="utf-8"))

    def test_all_configs_exist_and_validate(self) -> None:
        for subset, config in self.configs.items():
            validate_agent_v3_experiment(config)
            self.assertEqual(config["phase"], "agent_v3")
            self.assertTrue(config["dataset_role"])
            self.assertTrue(config["fixture_ids"])
            self.assertEqual(config["manifest"], "configs/dataset_g/manifest.json")
            self.assertTrue(config["collection_plan"])

    def test_manifest_hashes_match_the_files_on_disk(self) -> None:
        for relative, digest in self.manifest["files"].items():
            path = ROOT / relative
            self.assertTrue(path.exists(), relative)
            self.assertEqual(sha256_file(path), digest, relative)

    def test_manifest_totals(self) -> None:
        self.assertEqual(self.manifest["totals"]["scenarios"], 1312)
        self.assertEqual(self.manifest["totals"]["planned_traces"], 2860)
        self.assertEqual(self.manifest["totals"]["fixtures"], 18)

    def test_config_fixture_hashes_agree_with_the_manifest(self) -> None:
        for config in self.configs.values():
            files = config["fixture_files"]
            self.assertEqual(
                self.manifest["files"][files["knowledge_base"]],
                files["knowledge_base_sha256"],
            )
            self.assertEqual(
                self.manifest["files"][files["support_records"]],
                files["support_records_sha256"],
            )

    def test_agent_definitions_share_the_frozen_system_prompt_and_tools(self) -> None:
        source = json.loads(
            (ROOT / "configs" / "agent_v3_support.json").read_text(encoding="utf-8")
        )
        for config in self.configs.values():
            agent = json.loads((ROOT / config["agent_config"]).read_text(encoding="utf-8"))
            self.assertEqual(agent["system_prompt"], source["system_prompt"])
            self.assertEqual(agent["tools"], source["tools"])
            self.assertEqual(agent["runtime"], source["runtime"])
            self.assertNotEqual(agent["knowledge_base"], source["knowledge_base"])

    def test_medium_uses_the_medium_model_config(self) -> None:
        model = json.loads(
            (ROOT / self.configs["g_medium"]["model_config"]).read_text(encoding="utf-8")
        )
        self.assertEqual(model["chat_template_kwargs"]["reasoning_effort"], "medium")
        base = json.loads(
            (ROOT / self.configs["g_dev"]["model_config"]).read_text(encoding="utf-8")
        )
        self.assertEqual(base["chat_template_kwargs"]["reasoning_effort"], "low")
        self.assertEqual(model["model_id"], base["model_id"])
        self.assertEqual(model["revision"], base["revision"])

    def test_all_validators_pass(self) -> None:
        results = factory_validate.run_all(CONFIG_DIR)
        failed = [result.name for result in results if not result.passed]
        self.assertEqual(failed, [], f"failing checks: {failed}")
        self.assertGreaterEqual(len(results), 19)


class AppendOnlyExtensionTest(unittest.TestCase):
    """G-conf-2 was added by appending, never by re-rolling (build log, section 2)."""

    PRE_EXISTING = (
        "g_fit",
        "g_cal",
        "g_dev",
        "g_session",
        "g_medium",
        "g_conf",
    )

    def test_new_subset_is_registered_everywhere(self) -> None:
        from agent_v3.factory.build import FIXTURE_SUBSETS, SUBSET_ORDER
        from agent_v3.factory.constants import SUBSET_ID_PREFIX, SUBSET_SEED_BASE

        self.assertEqual(SUBSET_ORDER[-1], "g_conf2", "g_conf2 must be built last")
        self.assertIn("g_conf2", FIXTURE_SUBSETS)
        self.assertEqual(SUBSET_ID_PREFIX["g_conf2"], "g-cf2")
        self.assertEqual(SUBSET_SEED_BASE["g_conf2"], 670000)

    def test_the_new_prefix_and_seed_block_collide_with_nothing(self) -> None:
        from agent_v3.factory.constants import SUBSET_ID_PREFIX, SUBSET_SEED_BASE

        prefixes = list(SUBSET_ID_PREFIX.values())
        self.assertEqual(len(prefixes), len(set(prefixes)), "id prefixes must be unique")
        ids: dict[str, str] = {}
        seeds: dict[int, str] = {}
        for subset in SUBSETS:
            config = json.loads(
                (CONFIG_DIR / f"{subset}.json").read_text(encoding="utf-8")
            )
            for scenario in config["scenarios"]:
                sid = scenario["base_task_id"]
                self.assertNotIn(sid, ids, f"{sid} in {subset} and {ids.get(sid)}")
                ids[sid] = subset
                seed = scenario["sampling_seed"]
                if subset != "g_medium":  # the paired re-run repeats its partner's seed
                    self.assertNotIn(seed, seeds, f"seed {seed} reused by {subset}")
                    seeds[seed] = subset
        self.assertTrue(
            all(sid.startswith("g-cf2-") for sid, s in ids.items() if s == "g_conf2")
        )
        bases = sorted(SUBSET_SEED_BASE.values())
        self.assertEqual(len(bases), len(set(bases)))

    def test_appending_merchants_left_the_existing_fixtures_untouched(self) -> None:
        """Indices 0-14 of _MERCHANT_TABLE, so their fixture bytes, must not move."""

        from agent_v3.factory.merchants import MERCHANTS

        self.assertEqual(len(MERCHANTS), 18)
        for index, merchant in enumerate(MERCHANTS):
            self.assertEqual(merchant.index, index)
        self.assertEqual(
            [m.code for m in MERCHANTS[:15]],
            ["NLO", "HBL", "CBW", "PGA", "MFK", "SBW", "QLS", "VTB", "RDW", "LTF",
             "EMB", "FXG", "TSL", "WRH", "OSY"],
            "the first fifteen merchants must keep their table positions",
        )
        self.assertEqual([m.code for m in MERCHANTS[15:]], ["BRC", "KSW", "WNF"])
        self.assertEqual({m.subset for m in MERCHANTS[15:]}, {"g_conf2"})
        codes = [m.code for m in MERCHANTS]
        tokens = [m.brand_token for m in MERCHANTS]
        self.assertEqual(len(codes), len(set(codes)))
        self.assertEqual(len(tokens), len(set(tokens)))

    def test_only_the_new_subset_files_and_the_manifest_are_writable_targets(self) -> None:
        """``build_all(only=...)`` refuses to rewrite anything outside the new subset."""

        from agent_v3.factory.build import file_owner

        self.assertIsNone(file_owner("manifest.json"))
        self.assertEqual(file_owner("g_conf2.json"), "g_conf2")
        self.assertEqual(file_owner("agent_g_conf2.json"), "g_conf2")
        self.assertEqual(file_owner("fixtures/kb_g_conf2.json"), "g_conf2")
        self.assertEqual(file_owner("fixtures/records_g_conf2.json"), "g_conf2")
        self.assertEqual(file_owner("g_conf.json"), "g_conf")
        self.assertEqual(file_owner("model_gpt_oss_20b_medium.json"), "g_medium")

    def test_append_only_build_writes_only_the_new_files(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            temporary = Path(raw) / "dataset_g"
            shutil.copytree(CONFIG_DIR, temporary)
            before = {
                path: path.read_bytes()
                for path in sorted(temporary.rglob("*.json"))
            }
            result = build_all(temporary, only=("g_conf2",))
            self.assertEqual(
                sorted(result["written"]),
                sorted(
                    [
                        "agent_g_conf2.json",
                        "fixtures/kb_g_conf2.json",
                        "fixtures/records_g_conf2.json",
                        "g_conf2.json",
                        "manifest.json",
                    ]
                ),
            )
            self.assertEqual(len(result["verified_unchanged"]), 22)
            for path, payload in before.items():
                if path.name == "manifest.json" or "g_conf2" in path.name:
                    continue
                self.assertEqual(path.read_bytes(), payload, path.name)

    def test_append_only_build_aborts_if_a_frozen_file_would_change(self) -> None:
        from agent_v3.factory.build import UnexpectedRewrite

        with tempfile.TemporaryDirectory() as raw:
            temporary = Path(raw) / "dataset_g"
            shutil.copytree(CONFIG_DIR, temporary)
            victim = temporary / "fixtures" / "kb_g_conf.json"
            victim.write_text("{}\n", encoding="utf-8")
            with self.assertRaises(UnexpectedRewrite):
                build_all(temporary, only=("g_conf2",))

    def test_the_manifest_only_gained_entries(self) -> None:
        manifest = json.loads((CONFIG_DIR / "manifest.json").read_text(encoding="utf-8"))
        new = {
            "configs/dataset_g/g_conf2.json",
            "configs/dataset_g/agent_g_conf2.json",
            "configs/dataset_g/fixtures/kb_g_conf2.json",
            "configs/dataset_g/fixtures/records_g_conf2.json",
        }
        self.assertEqual(len(manifest["files"]), 26)
        self.assertTrue(new <= set(manifest["files"]))
        self.assertEqual(len(set(manifest["files"]) - new), 22)
        self.assertEqual(sorted(manifest["subsets"]), sorted(SUBSETS))

    def test_g_conf2_has_the_same_shape_as_g_conf(self) -> None:
        from collections import Counter

        def shape(subset: str) -> dict:
            config = json.loads(
                (CONFIG_DIR / f"{subset}.json").read_text(encoding="utf-8")
            )
            attacks = [
                s for s in config["scenarios"] if "attack" in s["factory"]["collected_arms"]
            ]
            return {
                "scenarios": len(config["scenarios"]),
                "traces": config["allocation"]["trace_count"],
                "roles": Counter(s["factory"]["scenario_role"] for s in config["scenarios"]),
                "channels": Counter(s["arms"]["attack"]["channel"] for s in attacks),
                "tiers": Counter(s["wording_tier"] for s in attacks),
                "groups": Counter(s["domain_group"] for s in attacks),
                "families": len({s["arms"]["attack"]["attack_family_id"] for s in attacks}),
                "plan": [
                    (g["group"], g["scenario_count"], tuple(g["arms"]))
                    for g in config["collection_plan"]
                ],
                "fixture_sizes": sorted(
                    Counter(s["factory"]["fixture_id"] for s in config["scenarios"]).values()
                ),
            }

        self.assertEqual(shape("g_conf2"), shape("g_conf"))

    def test_fixture_rank_mod_folds_are_balanced(self) -> None:
        """freeze review DATA-1: the fold key must split each fixture ~evenly."""

        from collections import Counter, defaultdict

        config = json.loads((CONFIG_DIR / "g_conf2.json").read_text(encoding="utf-8"))
        fixture_of = {
            s["base_task_id"]: s["factory"]["fixture_id"] for s in config["scenarios"]
        }
        rank: Counter = Counter()
        folds: dict[str, Counter] = defaultdict(Counter)
        for scenario_id in sorted(fixture_of):
            fixture = fixture_of[scenario_id]
            folds[fixture][rank[fixture] % 3] += 1
            rank[fixture] += 1
        self.assertEqual(len(folds), 3)
        for fixture, counts in folds.items():
            self.assertEqual(set(counts), {0, 1, 2}, fixture)
            self.assertLessEqual(max(counts.values()) - min(counts.values()), 1, fixture)
            for size in counts.values():
                self.assertIn(size, (31, 32), f"{fixture} fold size {size}")


class ReproducibilityTest(unittest.TestCase):
    def test_rebuilding_reproduces_every_file_byte_for_byte(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            temporary = Path(raw) / "dataset_g"
            build_all(temporary)
            manifest = json.loads(
                (CONFIG_DIR / "manifest.json").read_text(encoding="utf-8")
            )
            rebuilt = json.loads((temporary / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(rebuilt["files"], manifest["files"])
            for name in ("g_dev.json", "manifest.json", "fixtures/kb_g_dev.json"):
                self.assertEqual(
                    (temporary / name).read_bytes(), (CONFIG_DIR / name).read_bytes()
                )
        shutil.rmtree(temporary, ignore_errors=True)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
