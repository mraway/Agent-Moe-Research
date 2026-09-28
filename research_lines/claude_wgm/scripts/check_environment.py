#!/usr/bin/env python3
"""Read-only environment preflight for the local signal exploration milestone."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


GIB = 1024**3


def _memory() -> dict[str, float | None]:
    values: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            key, raw = line.split(":", 1)
            values[key] = int(raw.strip().split()[0]) * 1024
    except (OSError, ValueError):
        return {"ram_gib": None, "swap_gib": None}
    return {
        "ram_gib": round(values.get("MemTotal", 0) / GIB, 2),
        "swap_gib": round(values.get("SwapTotal", 0) / GIB, 2),
    }


def _disk() -> dict[str, float]:
    usage = shutil.disk_usage(Path.cwd())
    return {
        "total_gib": round(usage.total / GIB, 2),
        "free_gib": round(usage.free / GIB, 2),
    }


def _nvidia_smi() -> dict[str, Any]:
    candidates = [Path("/usr/lib/wsl/lib/nvidia-smi")]
    discovered = shutil.which("nvidia-smi")
    if discovered:
        candidates.append(Path(discovered))
    executable = next((p for p in candidates if p.exists()), None)
    if executable is None:
        return {"available": False, "error": "nvidia-smi not found"}
    try:
        result = subprocess.run(
            [
                str(executable),
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"available": False, "executable": str(executable), "error": str(exc)}
    rows = [row.strip() for row in result.stdout.splitlines() if row.strip()]
    return {"available": bool(rows), "executable": str(executable), "gpus": rows}


def _torch() -> dict[str, Any]:
    try:
        import torch
    except ImportError:
        return {"installed": False}
    report: dict[str, Any] = {
        "installed": True,
        "version": torch.__version__,
        "built_cuda": torch.version.cuda,
        "cuda_available": torch.cuda.is_available(),
    }
    if not report["cuda_available"]:
        return report
    try:
        device = torch.device("cuda:0")
        left = torch.randn((256, 256), device=device, dtype=torch.bfloat16)
        right = torch.randn((256, 256), device=device, dtype=torch.bfloat16)
        product = left @ right
        torch.cuda.synchronize()
        report.update(
            {
                "device_name": torch.cuda.get_device_name(0),
                "capability": list(torch.cuda.get_device_capability(0)),
                "bf16_matmul": bool(torch.isfinite(product).all().item()),
            }
        )
    except (RuntimeError, AssertionError) as exc:
        report["smoke_error"] = str(exc)
    return report


def main() -> int:
    memory = _memory()
    report = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "kernel": platform.release(),
        "wsl": "microsoft" in platform.release().lower(),
        "dxg_present": Path("/dev/dxg").exists(),
        "memory": memory,
        "disk": _disk(),
        "nvidia_smi": _nvidia_smi(),
        "torch": _torch(),
        "cwd": os.fspath(Path.cwd()),
    }
    problems: list[str] = []
    if (memory["ram_gib"] or 0) < 20:
        problems.append("WSL RAM is below the 20 GiB preflight minimum")
    if (memory["swap_gib"] or 0) < 8:
        problems.append("WSL swap is below the 8 GiB preflight minimum")
    if not report["nvidia_smi"].get("available"):
        problems.append("GPU is not visible to nvidia-smi in this execution context")
    if not report["torch"].get("installed"):
        problems.append("PyTorch is not installed")
    elif not report["torch"].get("cuda_available"):
        problems.append("PyTorch cannot access CUDA")
    elif not report["torch"].get("bf16_matmul"):
        problems.append("CUDA bf16 matmul smoke test did not pass")
    report["problems"] = problems
    report["ready"] = not problems
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
