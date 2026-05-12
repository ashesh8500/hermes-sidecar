"""CPU load monitoring via psutil.

Provides both blocking (averaged over 1 second) and non-blocking
(instant since last poll) CPU utilization readings.
"""

from __future__ import annotations

from typing import Optional

try:
    import psutil

    _PSUTIL_AVAILABLE = True
except ImportError:
    psutil = None  # type: ignore[assignment]
    _PSUTIL_AVAILABLE = False


def get_cpu_load() -> Optional[float]:
    """Return the system-wide CPU load as a fraction (0.0 – 1.0).

    Blocks for **1 second** while psutil samples utilisation, then returns
    the average over that interval.  Returns *None* if psutil is not
    installed.
    """
    if not _PSUTIL_AVAILABLE:
        return None

    pct = psutil.cpu_percent(interval=1)
    return pct / 100.0


def get_cpu_load_instant() -> Optional[float]:
    """Return an instantaneous CPU load reading (0.0 – 1.0).

    Uses the delta since the last call to *any* ``cpu_percent`` function
    in the same process — the first call after module import will return a
    near-zero value because no baseline has been established yet.

    Returns *None* if psutil is not installed.
    """
    if not _PSUTIL_AVAILABLE:
        return None

    pct = psutil.cpu_percent(interval=0)
    return pct / 100.0
