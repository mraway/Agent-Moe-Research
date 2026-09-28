"""Regression for the preflight-only report writer; no research-data reads."""
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from research_v4.codex_g_alg_a01_preflight_v1_1 import write_json


class SerializationTests(unittest.TestCase):
    def test_numpy_scalar_counts_survive_json(self):
        with tempfile.TemporaryDirectory(prefix="codex-a01-json-") as directory:
            path = Path(directory)/"result.json"
            write_json(path, {"n": np.int64(42), "ok": np.bool_(True), "rate": np.float64(.125)})
            self.assertEqual(json.loads(path.read_text()), {"n": 42, "ok": True, "rate": .125})

    def test_invalid_payload_does_not_create_file(self):
        with tempfile.TemporaryDirectory(prefix="codex-a01-json-") as directory:
            path = Path(directory)/"result.json"
            with self.assertRaises(ValueError): write_json(path, {"x": float("nan")})
            self.assertFalse(path.exists())

    def test_refuses_to_overwrite(self):
        with tempfile.TemporaryDirectory(prefix="codex-a01-json-") as directory:
            path = Path(directory)/"result.json"
            write_json(path, {"first": True})
            with self.assertRaises(FileExistsError): write_json(path, {"first": False})
            self.assertEqual(json.loads(path.read_text()), {"first": True})


if __name__ == "__main__": unittest.main()
