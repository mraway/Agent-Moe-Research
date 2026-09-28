"""A01 preflight output-only correction; preserves all frozen v1 files and math."""
from __future__ import annotations

import argparse
import json

import numpy as np
import torch

from research_v4 import codex_g_alg_a01_preflight as v1

EXTRA_SOURCES = (
    "docs/research_v4/codex_g_alg_a01_preflight_fix_v1_1.md",
    "scripts/research_v4/codex_g_alg_a01_preflight_v1_1.py",
    "tests/test_research_v4_codex_g_alg_a01_preflight_v1_1.py",
)


def native_scalar(value):
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


def write_json(path, value):
    body = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False, default=native_scalar)+"\n"
    with path.open("x") as f:
        f.write(body)


def configure():
    snapshot_v1 = v1.source_snapshot
    def snapshot():
        return {**snapshot_v1(), **{p: v1.digest((v1.ROOT/p).read_bytes()) for p in EXTRA_SOURCES}}
    v1.source_snapshot = snapshot
    v1.OUT = v1.BASE/"alg_a01_joint_neighbors_v1_1"
    v1.write_json = write_json


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("freeze", "preflight"), required=True)
    parser.add_argument("--source-sha256")
    opt = parser.parse_args(); torch.set_num_threads(1)
    configure()
    if opt.stage == "freeze":
        v1.freeze()
    elif not opt.source_sha256:
        parser.error("preflight requires --source-sha256")
    else:
        v1.run(opt.source_sha256)
