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
SUBSETS = ("g_fit", "g_cal", "g_dev", "g_session", "g_medium", "g_conf")


class WrittenConfigTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.configs = {
            subset: json.loads((CONFIG_DIR / f"{subset}.json").read_text(encoding="utf-8"))
            for subset in SUBSETS
        }
        cls.manifest = json.loads((CONFIG_DIR / "manifest.json").read_text(encoding="utf-8"))

    def test_all_six_configs_exist_and_validate(self) -> None:
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
        self.assertEqual(self.manifest["totals"]["scenarios"], 1032)
        self.assertEqual(self.manifest["totals"]["planned_traces"], 2140)
        self.assertEqual(self.manifest["totals"]["fixtures"], 15)

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
