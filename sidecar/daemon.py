"""
Adaptive daemon for hermes-sidecar.

Provides a StateMachine that governs Syncthing throttle/pause behavior based on
power source, battery level, and CPU load, and a run_daemon() event loop that
polls system state and applies transitions with hysteresis.

State logic (priority order — higher rules override lower):
  1. CPU > 70%             → 'high_cpu'     (50 KB/s, not paused)
  2. AC power              → 'ac'           (full throttle from config)
  3. Battery > 50%         → 'battery_normal'  (200 KB/s)
  4. Battery 20% – 50%     → 'battery_low'     (50 KB/s)
  5. Battery < 20%         → 'battery_critical' (0 KB/s, paused)

Hysteresis: the target state must be observed for 2 consecutive polls before a
transition is committed.  The 'battery_critical' state triggers immediately
(no hysteresis wait) to avoid data loss on a dying battery.

On SIGTERM / SIGINT the daemon restores the 'ac' state (full throttle, all
folders resumed) before exiting cleanly.
"""

import logging
import os
import signal
import time
from pathlib import Path
from typing import Optional, Tuple

from sidecar.actions.syncthing import SyncthingClient

# ── Config import (written by a parallel worker; provide fallback) ────────
try:
    from sidecar.config import load_config, SidecarConfig  # noqa: F401
except ImportError:
    # Fallback: minimal config stubs for standalone use before config.py lands.
    from dataclasses import dataclass

    @dataclass
    class _SyncthingConfig:
        default_throttle_kbps: int = 500

    @dataclass
    class SidecarConfig:
        syncthing: _SyncthingConfig = _SyncthingConfig()

    def load_config() -> SidecarConfig:
        return SidecarConfig()


# ── Monitor imports (written by parallel workers; provide fallback) ──────
try:
    from sidecar.monitors.power import (  # noqa: F401
        detect_power_source,
        get_battery_percent,
    )
except ImportError:
    import platform as _platform
    import re as _re
    import subprocess as _sp

    def detect_power_source() -> str:
        """Fallback power detection. Returns 'ac' or 'battery'."""
        if _platform.system() == "Darwin":
            try:
                result = _sp.run(
                    ["pmset", "-g", "batt"],
                    capture_output=True, text=True, timeout=5,
                )
                if "AC Power" in result.stdout:
                    return "ac"
                if "Battery Power" in result.stdout:
                    return "battery"
            except Exception:
                pass
        else:
            # Linux: check /sys/class/power_supply
            for supply in ["AC", "AC0", "ADP1"]:
                path = Path("/sys/class/power_supply") / supply / "online"
                if path.exists():
                    try:
                        if path.read_text().strip() == "1":
                            return "ac"
                    except Exception:
                        pass
        return "ac"

    def get_battery_percent() -> int:
        """Fallback battery percentage (0-100). Returns 100 on AC."""
        if _platform.system() == "Darwin":
            try:
                result = _sp.run(
                    ["pmset", "-g", "batt"],
                    capture_output=True, text=True, timeout=5,
                )
                m = _re.search(r"(\d+)%", result.stdout)
                if m:
                    return int(m.group(1))
            except Exception:
                pass
        else:
            for bat in ["BAT0", "BAT1"]:
                cap_path = Path("/sys/class/power_supply") / bat / "capacity"
                if cap_path.exists():
                    try:
                        return int(cap_path.read_text().strip())
                    except Exception:
                        pass
        return 100

try:
    from sidecar.monitors.cpu import get_cpu_load  # noqa: F401
except ImportError:
    import os as _os
    import subprocess as _sp_cpu

    def get_cpu_load() -> float:
        """Fallback CPU load percentage (0-100)."""
        try:
            import psutil  # type: ignore
            return psutil.cpu_percent(interval=1)
        except ImportError:
            if _platform.system() == "Darwin":
                try:
                    result = _sp_cpu.run(
                        ["sysctl", "-n", "vm.loadavg"],
                        capture_output=True, text=True, timeout=3,
                    )
                    parts = result.stdout.strip().strip("{}").split()
                    if parts:
                        count = _os.cpu_count() or 4
                        return float(parts[0]) / count * 100
                except Exception:
                    pass
        return 50.0


# ── constants ────────────────────────────────────────────────────────────

LOG_DIR = Path.home() / ".hermes" / "sidecar"
LOG_FILE = LOG_DIR / "daemon.log"

# Default throttle values (KB/s) per state
STATE_THROTTLE = {
    "ac": 500,
    "battery_normal": 200,
    "battery_low": 50,
    "battery_critical": 0,
    "high_cpu": 50,
}

# Thresholds
CPU_HIGH_THRESHOLD = 70
BATTERY_NORMAL_THRESHOLD = 50
BATTERY_LOW_THRESHOLD = 20
POLL_INTERVAL_S = 30
HYSTERESIS_CHECKS = 2

STATE_LABELS: "dict[str, str]" = {
    "ac": "AC",
    "battery_normal": "BAT",
    "battery_low": "BAT-LOW",
    "battery_critical": "CRITICAL",
    "high_cpu": "CPU-HIGH",
}


class StateMachine:
    """Hysteresis-buffered state machine for Syncthing governance.

    Polls power and CPU state.  Requires *hysteresis* consecutive polls in a
    new target state before committing a transition (except 'battery_critical',
    which commits immediately).

    Attributes:
        current_state: The active state name.
        target_state: The candidate state we are counting toward.
        consecutive_count: Number of consecutive polls in target_state.
        hysteresis: Required consecutive polls before transition.
    """

    def __init__(
        self,
        hysteresis: int = HYSTERESIS_CHECKS,
        initial_state: str = "ac",
    ) -> None:
        self.current_state: str = initial_state
        self.target_state: str = initial_state
        self.consecutive_count: int = 0
        self.hysteresis: int = hysteresis
        self._client: Optional[SyncthingClient] = None

    # ── state determination ────────────────────────────────────────

    def determine_target(
        self,
        power_source: str,
        battery_pct: int,
        cpu_pct: float,
    ) -> str:
        """Determine the target state from current system metrics.

        Args:
            power_source: 'ac' or 'battery'.
            battery_pct: Battery percentage 0-100 (meaningless on AC).
            cpu_pct: System CPU load 0-100.

        Returns:
            The target state name.
        """
        # CPU overload overrides everything — system is working hard.
        if cpu_pct > CPU_HIGH_THRESHOLD:
            return "high_cpu"

        if power_source == "ac":
            return "ac"

        # On battery: tier by percentage.
        if battery_pct <= BATTERY_LOW_THRESHOLD:
            return "battery_critical"
        elif battery_pct <= BATTERY_NORMAL_THRESHOLD:
            return "battery_low"
        else:
            return "battery_normal"

    # ── transition logic ──────────────────────────────────────────

    def transition(self, target: str, logger: logging.Logger) -> bool:
        """Evaluate a poll result and transition state if hysteresis is met.

        Args:
            target: The candidate target state from this poll.
            logger: Logger for recording state changes.

        Returns:
            True if a transition was committed, False otherwise.
        """
        if target == self.current_state:
            self.target_state = target
            self.consecutive_count += 1
            return False

        if target != self.target_state:
            # New candidate — reset the counter.
            self.target_state = target
            self.consecutive_count = 1
            # Battery critical bypasses hysteresis.
            if target == "battery_critical":
                self._commit(target, logger)
                return True
            return False

        # Same candidate as last poll — increment.
        self.consecutive_count += 1
        if self.consecutive_count >= self.hysteresis:
            self._commit(target, logger)
            return True
        return False

    def _commit(self, state: str, logger: logging.Logger) -> None:
        """Commit to a new state, applying it to Syncthing."""
        old_label = STATE_LABELS.get(self.current_state, self.current_state)
        new_label = STATE_LABELS.get(state, state)
        logger.info(
            "%s → %s  (count=%d)",
            old_label,
            new_label,
            self.consecutive_count,
        )
        self.current_state = state
        self.target_state = state
        self.consecutive_count = 0

    # ── applying state to Syncthing ───────────────────────────────

    def apply(self, config: SidecarConfig) -> bool:
        """Apply the current state's throttle and pause settings to Syncthing.

        Args:
            config: The current SidecarConfig (for default throttle values).

        Returns:
            True if applied successfully, False on API failure.
        """
        client = self._get_client()
        if not client:
            return False

        throttle = STATE_THROTTLE.get(
            self.current_state,
            config.syncthing.default_throttle_kbps,
        )
        should_pause = self.current_state == "battery_critical"

        # Set throttle.
        client.set_throttle(throttle)

        # Set folder paused state.
        if should_pause:
            client.pause_all_folders()
        else:
            client.resume_all_folders()

        return True

    def restore_ac_state(self, config: SidecarConfig) -> bool:
        """Restore the AC (full throttle, resumed) state.  Called on shutdown.

        Args:
            config: The current SidecarConfig.

        Returns:
            True on success.
        """
        client = self._get_client()
        if not client:
            return False

        throttle = config.syncthing.default_throttle_kbps
        client.set_throttle(throttle)
        client.resume_all_folders()
        return True

    def _get_client(self) -> Optional[SyncthingClient]:
        """Lazily create and cache the SyncthingClient."""
        if self._client is None:
            self._client = SyncthingClient()
            if not self._client.api_key:
                return None
        return self._client

    def api_key_present(self) -> bool:
        """Check whether a Syncthing API key is available."""
        return self._get_client() is not None


# ── daemon event loop ────────────────────────────────────────────────────


def run_daemon(
    poll_interval_s: int = POLL_INTERVAL_S,
    hysteresis: int = HYSTERESIS_CHECKS,
) -> None:
    """Run the adaptive daemon event loop.

    Polls power source, battery level, and CPU load at regular intervals.
    Uses the StateMachine to transition between throttle/pause states with
    hysteresis to prevent flapping.  Restores the AC state on SIGTERM/SIGINT.

    Args:
        poll_interval_s: Seconds between polls.
        hysteresis: Consecutive polls required in a new state before transition.
    """
    # ── logging ─────────────────────────────────────────────────────
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("hermes-sidecar.daemon")
    logger.setLevel(logging.INFO)
    handler = logging.FileHandler(str(LOG_FILE))
    handler.setFormatter(logging.Formatter(
        "%(asctime)s  %(message)s",
        datefmt="%H:%M:%S",
    ))
    logger.addHandler(handler)
    # Also log to stderr so the user sees output.
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(
        "%(asctime)s  %(message)s",
        datefmt="%H:%M:%S",
    ))
    logger.addHandler(console)

    config = load_config()

    # ── state machine ───────────────────────────────────────────────
    sm = StateMachine(hysteresis=hysteresis, initial_state="ac")

    # Apply initial state.
    if sm.api_key_present():
        sm.apply(config)

    running = True

    # ── signal handlers ─────────────────────────────────────────────
    def _shutdown(signum: int, frame: object) -> None:
        nonlocal running
        if not running:
            return
        running = False
        logger.info(
            "Received signal %d — restoring AC state and shutting down...",
            signum,
        )
        try:
            sm.restore_ac_state(config)
            logger.info("AC state restored. Goodbye.")
        except Exception as exc:
            logger.error("Error during shutdown: %s", exc)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    # ── main loop ───────────────────────────────────────────────────
    first_poll = True

    logger.info(
        "Daemon started — poll=%ds  hysteresis=%d  cpu_thresh=%d%%  "
        "batt_normal=%d%%  batt_low=%d%%",
        poll_interval_s,
        hysteresis,
        CPU_HIGH_THRESHOLD,
        BATTERY_NORMAL_THRESHOLD,
        BATTERY_LOW_THRESHOLD,
    )

    while running:
        try:
            power_source = detect_power_source()
            battery_pct = get_battery_percent()
            cpu_pct = get_cpu_load()

            target = sm.determine_target(power_source, battery_pct, cpu_pct)

            if first_poll:
                logger.info(
                    "Initial: %s  (power=%s  batt=%d%%  cpu=%.0f%%)",
                    STATE_LABELS.get(target, target),
                    power_source,
                    battery_pct,
                    cpu_pct,
                )
                if target != sm.current_state:
                    sm._commit(target, logger)
                    if sm.api_key_present():
                        sm.apply(config)
                first_poll = False
            else:
                committed = sm.transition(target, logger)
                if committed and sm.api_key_present():
                    sm.apply(config)
        except Exception as exc:
            logger.error("Poll error: %s", exc)

        if running:
            time.sleep(poll_interval_s)

    logger.info("Daemon stopped.")
