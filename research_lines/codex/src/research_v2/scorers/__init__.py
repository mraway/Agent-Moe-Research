"""Scorer registry for the research-v2 harness (protocol v2, spec section 1.10).

A scorer is any object with

    window_width: int                                   # causal window width w
    fit(routine_traces: Sequence[LoadedTrace]) -> state # ONLY routine traces
    score(state, trace: LoadedTrace) -> (scores[nwin], ends[nwin])

``ends[i]`` is the decode index of the last token of window ``i`` (window covers
``[ends[i]-w+1, ends[i]]``); ``scores`` must be causal -- computable from decode tokens
``<= ends[i]`` plus offline routine statistics.

Register a scorer from a module under ``src/research_v2/scorers/``::

    from research_v2.scorers import register

    @register("my_scorer")
    def build(config: dict) -> MyScorer:
        return MyScorer(**config)

Modules in this package are imported automatically by ``available()`` / ``build()``.

Reference-only scorers (they see drift labels and are therefore NOT detectors) set
``requires_positives = True``; the harness then hands ``fit`` the whole fitting-side
record set and marks every result row ``reference_only``.
"""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path
from typing import Any, Callable

_REGISTRY: dict[str, Callable[[dict[str, Any]], Any]] = {}
_LOADED = False


def register(name: str) -> Callable[[Callable[[dict[str, Any]], Any]], Callable[[dict[str, Any]], Any]]:
    def decorator(factory: Callable[[dict[str, Any]], Any]) -> Callable[[dict[str, Any]], Any]:
        if name in _REGISTRY:
            raise ValueError(f"scorer already registered: {name}")
        _REGISTRY[name] = factory
        return factory

    return decorator


def _load_all() -> None:
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    package_dir = Path(__file__).resolve().parent
    for module in pkgutil.iter_modules([str(package_dir)]):
        if module.name.startswith("_"):
            continue
        importlib.import_module(f"{__name__}.{module.name}")
    importlib.import_module("research_v2.baselines")


def available() -> tuple[str, ...]:
    _load_all()
    return tuple(sorted(_REGISTRY))


def build(name: str, config: dict[str, Any] | None = None) -> Any:
    _load_all()
    if name not in _REGISTRY:
        raise ValueError(f"unknown scorer {name!r}; available: {available()}")
    return _REGISTRY[name](dict(config or {}))
