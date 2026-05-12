"""Power source and battery-level detection.

Cross-platform (macOS / Linux) with no external dependencies beyond the
standard library.
"""

from __future__ import annotations

import platform
import re
import subprocess
from pathlib import Path
from typing import Literal, Optional

PowerSource = Literal["ac", "battery"]

_SYSTEM: str = platform.system()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_power_source() -> PowerSource:
    """Return ``"ac"`` or ``"battery"`` depending on the active power source.

    On macOS this parses ``pmset -g batt``.
    On Linux this reads ``/sys/class/power_supply/AC*/online``.

    Returns:
        ``"ac"`` when connected to mains power, ``"battery"`` otherwise.

        Defaults to ``"ac"`` when detection fails (e.g. desktop without a
        battery) so that syncing is not blocked on machines where battery
        monitoring is meaningless.
    """
    if _SYSTEM == "Darwin":
        return _macos_detect_power_source()
    if _SYSTEM == "Linux":
        return _linux_detect_power_source()
    # Unknown platform — assume AC to avoid blocking.
    return "ac"


def get_battery_percent() -> Optional[int]:
    """Return the current battery percentage (0-100), or *None*.

    Returns *None* on desktop machines with no battery, on platforms that
    are not macOS or Linux, or when detection fails for any reason.
    """
    if _SYSTEM == "Darwin":
        return _macos_get_battery_percent()
    if _SYSTEM == "Linux":
        return _linux_get_battery_percent()
    return None


# ---------------------------------------------------------------------------
# macOS helpers
# ---------------------------------------------------------------------------

_BATT_LINE_RE = re.compile(r"(\d{1,3})\s*%")


def _macos_parse_pmset() -> tuple[Optional[str], Optional[int]]:
    """Run ``pmset -g batt`` and return (source_string, battery_percent).

    source_string is one of ``"AC Power"``, ``"Battery Power"``, or *None*.
    battery_percent is 0-100 or *None*.
    """
    try:
        result = subprocess.run(
            ["pmset", "-g", "batt"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None, None

    if result.returncode != 0:
        return None, None

    output = result.stdout.strip()
    if not output:
        return None, None

    lines = output.splitlines()
    source: Optional[str] = None

    # First line contains the power source
    for line in lines:
        line_lower = line.lower()
        if "ac power" in line_lower:
            source = "AC Power"
            break
        if "battery power" in line_lower:
            source = "Battery Power"
            break

    # Scan for battery percentage on any line
    pct: Optional[int] = None
    for line in lines:
        m = _BATT_LINE_RE.search(line)
        if m:
            try:
                pct = int(m.group(1))
            except ValueError:
                continue
            break

    return source, pct


def _macos_detect_power_source() -> PowerSource:
    source, _ = _macos_parse_pmset()
    if source == "Battery Power":
        return "battery"
    return "ac"


def _macos_get_battery_percent() -> Optional[int]:
    _, pct = _macos_parse_pmset()
    return pct


# ---------------------------------------------------------------------------
# Linux helpers
# ---------------------------------------------------------------------------


def _linux_detect_power_source() -> PowerSource:
    """Check if any AC adapter reports 'online'."""
    ac_adapters = sorted(Path("/sys/class/power_supply").glob("AC*"))
    for ac_path in ac_adapters:
        online_file = ac_path / "online"
        try:
            value = online_file.read_text().strip()
            if value == "1":
                return "ac"
        except (OSError, PermissionError):
            continue
    return "battery"


def _linux_get_battery_percent() -> Optional[int]:
    """Read capacity from the first battery that provides it."""
    batteries = sorted(Path("/sys/class/power_supply").glob("BAT*"))
    for bat_path in batteries:
        capacity_file = bat_path / "capacity"
        try:
            value = capacity_file.read_text().strip()
            return int(value)
        except (OSError, PermissionError, ValueError):
            continue
    return None
