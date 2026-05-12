"""Network interface and connectivity detection.

Cross-platform (macOS / Linux).  Uses psutil for interface enumeration
and connectivity checks; falls back to platform-specific subprocess calls
for Wi-Fi SSID detection.
"""

from __future__ import annotations

import platform
import subprocess
from typing import Optional

try:
    import psutil as _psutil

    _PSUTIL_AVAILABLE = True
except ImportError:
    _psutil = None  # type: ignore[assignment]
    _PSUTIL_AVAILABLE = False

_SYSTEM: str = platform.system()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_primary_interface() -> Optional[str]:
    """Return the name of the primary (default-route) network interface.

    On macOS this parses ``route -n get default``.
    On Linux this reads ``/proc/net/route``.

    Returns *None* when no default-route interface can be identified.
    """
    if _SYSTEM == "Darwin":
        return _macos_get_primary_interface()
    if _SYSTEM == "Linux":
        return _linux_get_primary_interface()
    return _psutil_fallback_interface()


def is_connected() -> bool:
    """Return *True* when the machine appears to have working connectivity.

    Checks that the primary interface exists, is up, and has at least one
    assigned IPv4 address.
    """
    if not _PSUTIL_AVAILABLE:
        return False

    try:
        stats = _psutil.net_if_stats()
        addrs = _psutil.net_if_addrs()
    except Exception:
        return False

    for name, snic_stats in stats.items():
        if not snic_stats.isup:
            continue
        if name == "lo":
            continue
        # Must have at least one non-loopback IPv4 address
        for addr in addrs.get(name, []):
            if addr.family == 2 and not addr.address.startswith("127."):  # AF_INET
                return True
    return False


def get_ssid() -> Optional[str]:
    """Return the SSID of the currently connected Wi-Fi network, or *None*.

    macOS: parses ``/System/Library/PrivateFrameworks/Apple80211.framework/\
Versions/Current/Resources/airport -I``.
    Linux: runs ``iwgetid -r``.
    """
    if _SYSTEM == "Darwin":
        return _macos_get_ssid()
    if _SYSTEM == "Linux":
        return _linux_get_ssid()
    return None


# ---------------------------------------------------------------------------
# Platform-specific helpers
# ---------------------------------------------------------------------------


def _macos_get_primary_interface() -> Optional[str]:
    """Parse ``route -n get default`` for the ``interface:`` line."""
    try:
        result = subprocess.run(
            ["route", "-n", "get", "default"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return _psutil_fallback_interface()

    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("interface:"):
            parts = stripped.split(":", 1)
            if len(parts) == 2:
                return parts[1].strip()
    return _psutil_fallback_interface()


def _linux_get_primary_interface() -> Optional[str]:
    """Read /proc/net/route to find the interface with a gateway of 00000000."""
    try:
        with open("/proc/net/route", "r") as fh:
            # Skip header line
            next(fh)
            for line in fh:
                fields = line.strip().split()
                if len(fields) >= 3 and fields[1] == "00000000":
                    return fields[0]
    except (OSError, StopIteration):
        pass
    return _psutil_fallback_interface()


def _psutil_fallback_interface() -> Optional[str]:
    """Return the first non-loopback UP interface from psutil."""
    if not _PSUTIL_AVAILABLE:
        return None
    try:
        stats = _psutil.net_if_stats()
    except Exception:
        return None
    for name, snic_stats in stats.items():
        if name == "lo":
            continue
        if snic_stats.isup:
            return name
    return None


def _macos_get_ssid() -> Optional[str]:
    """Call the airport CLI to extract the current SSID."""
    airport_bin = (
        "/System/Library/PrivateFrameworks/Apple80211.framework"
        "/Versions/Current/Resources/airport"
    )
    try:
        result = subprocess.run(
            [airport_bin, "-I"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

    if result.returncode != 0:
        return None

    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("ssid:"):
            parts = stripped.split(":", 1)
            if len(parts) == 2:
                ssid = parts[1].strip()
                return ssid if ssid else None

    return None


def _linux_get_ssid() -> Optional[str]:
    """Run ``iwgetid -r`` to get the current SSID."""
    try:
        result = subprocess.run(
            ["iwgetid", "-r"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

    if result.returncode != 0:
        return None

    ssid = result.stdout.strip()
    return ssid if ssid else None
