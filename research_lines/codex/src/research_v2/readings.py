"""Sequential readings of a standardized score stream (protocol v2, spec section 1.5).

Every reading maps the position-bucket standardized score stream ``z`` (one value per
causal window end) to an alarm statistic ``S`` of the same length.  The harness raises an
alarm at the first ``t`` with ``S_t >= h`` (see ``harness.COMPARISONS``).

Two threshold sources exist:

* ``conformal`` -- ``h`` is the finite-sample order statistic of routine trace maxima of
  ``S`` (spec 1.6).  Used by ``max``, ``persist2``, ``ewma*`` and ``cusum*``.
* ``fixed`` -- ``h`` is written into the reading itself.  Only ``runlen(m, c)`` uses this,
  because spec 1.5 defines it as "m consecutive windows with z >= c", i.e. the constant
  ``c`` *is* the threshold.  Its false-alarm rate is therefore not alpha-controlled; it is
  measured and reported like any other row.

``persist2`` is the primary reading (spec 1.5), identical to the ``min(z_{t-1}, z_t)``
persistence used by the main-branch P1-P3 experiments.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import torch

NEG_INF = float("-inf")


@dataclass(frozen=True)
class Reading:
    name: str
    apply: Callable[[torch.Tensor], torch.Tensor]
    threshold_source: str = "conformal"
    fixed_threshold: float | None = None


def read_max(z: torch.Tensor) -> torch.Tensor:
    return z.clone()


def read_persist_m(z: torch.Tensor, m: int) -> torch.Tensor:
    """Running minimum over the last ``m`` windows; positions < m-1 are unreachable."""

    if m <= 1:
        return z.clone()
    out = torch.full_like(z, NEG_INF)
    if z.numel() >= m:
        stacked = torch.stack([z[offset : z.numel() - (m - 1 - offset)] for offset in range(m)])
        out[m - 1 :] = stacked.min(dim=0).values
    return out


def read_ewma(z: torch.Tensor, lam: float) -> torch.Tensor:
    out = torch.empty_like(z)
    acc = 0.0
    for index in range(z.numel()):
        value = float(z[index])
        acc = value if index == 0 else lam * value + (1.0 - lam) * acc
        out[index] = acc
    return out


def read_cusum(z: torch.Tensor, kappa: float) -> torch.Tensor:
    out = torch.empty_like(z)
    acc = 0.0
    for index in range(z.numel()):
        acc = max(0.0, acc + float(z[index]) - kappa)
        out[index] = acc
    return out


def build_readings(names: tuple[str, ...] | list[str] | None = None) -> tuple[Reading, ...]:
    """All readings of spec 1.5 (default) or the named subset."""

    catalogue: dict[str, Reading] = {
        "max": Reading("max", read_max),
        "persist2": Reading("persist2", lambda z: read_persist_m(z, 2)),
        "ewma01": Reading("ewma01", lambda z: read_ewma(z, 0.1)),
        "ewma02": Reading("ewma02", lambda z: read_ewma(z, 0.2)),
        "cusum05": Reading("cusum05", lambda z: read_cusum(z, 0.5)),
        "cusum1": Reading("cusum1", lambda z: read_cusum(z, 1.0)),
        "cusum2": Reading("cusum2", lambda z: read_cusum(z, 2.0)),
    }
    for m, c in ((4, 1.0), (8, 1.0), (4, 2.0), (8, 2.0)):
        key = f"runlen{m}_{int(c)}"
        catalogue[key] = Reading(
            key,
            (lambda width: (lambda z: read_persist_m(z, width)))(m),
            threshold_source="fixed",
            fixed_threshold=c,
        )
    if names is None:
        return tuple(catalogue.values())
    missing = [name for name in names if name not in catalogue]
    if missing:
        raise ValueError(f"unknown reading(s): {missing}")
    return tuple(catalogue[name] for name in names)


READING_NAMES = tuple(reading.name for reading in build_readings())
PRIMARY_READING = "persist2"
