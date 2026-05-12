"""
Cross-platform desktop notifications for hermes-sidecar.

Provides a single notify() function that dispatches to the native notification
system: osascript display notification on macOS, notify-send on Linux.
"""

import platform
import subprocess
from typing import Optional

_IS_MACOS = platform.system() == "Darwin"


def notify(title: str, message: str, sound: bool = False) -> bool:
    """Send a desktop notification.

    Args:
        title: Notification title.
        message: Notification body text.
        sound: Whether to play an alert sound (macOS only; Linux notify-send
               supports this with libnotify >= 0.7.6 but not all distros).

    Returns:
        True if the notification was dispatched successfully, False otherwise.
    """
    try:
        if _IS_MACOS:
            return _notify_macos(title, message, sound)
        else:
            return _notify_linux(title, message, sound)
    except Exception:
        return False


def _notify_macos(title: str, message: str, sound: bool) -> bool:
    """Send a notification via osascript on macOS."""
    script = f'display notification "{_escape(message)}" with title "{_escape(title)}"'
    if sound:
        script += ' sound name "default"'
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=5,
    )
    return result.returncode == 0


def _notify_linux(title: str, message: str, sound: bool) -> bool:
    """Send a notification via notify-send on Linux."""
    cmd = ["notify-send", title, message]
    if sound:
        cmd.extend(["--hint", "int:transient:1"])
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=5,
    )
    return result.returncode == 0


def _escape(text: str) -> str:
    """Escape double-quotes and backslashes for use in AppleScript strings."""
    return text.replace("\\", "\\\\").replace('"', '\\"')
