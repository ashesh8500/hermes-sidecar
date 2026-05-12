"""Core configuration system for hermes-sidecar.

Provides typed dataclasses for all config sections, YAML loading,
and default config generation.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


# ---------------------------------------------------------------------------
# Default configuration (dict form, serialized to YAML)
# ---------------------------------------------------------------------------

DEFAULT_CONFIG: Dict[str, Any] = {
    "syncthing": {
        "api_url": "http://localhost:8384",
        "api_key": "",
        "config_path": "~/.config/syncthing",
        "device_name": "",
        "target_device_id": "",
    },
    "monitor": {
        "poll_interval_seconds": 5,
        "power_enabled": True,
        "cpu_enabled": True,
        "network_enabled": True,
    },
    "thresholds": {
        "battery_pause_percent": 20,
        "battery_resume_percent": 30,
        "cpu_pause_load": 0.80,
        "cpu_resume_load": 0.50,
        "network_required_ssids": [],
    },
    "actions": {
        "on_battery_pause": True,
        "on_battery_throttle_kbps": 0,
        "on_cpu_high_pause": True,
        "on_cpu_high_throttle_kbps": 100,
        "on_network_loss_pause": True,
        "on_network_restore_resume": True,
    },
}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class SyncthingConfig:
    """Connection and identity settings for the local Syncthing instance."""

    api_url: str = "http://localhost:8384"
    api_key: str = ""
    config_path: str = "~/.config/syncthing"
    device_name: str = ""
    target_device_id: str = ""

    @property
    def resolved_config_path(self) -> Path:
        return Path(self.config_path).expanduser()


@dataclass
class MonitorConfig:
    """Polling and toggles for the three monitor subsystems."""

    poll_interval_seconds: int = 5
    power_enabled: bool = True
    cpu_enabled: bool = True
    network_enabled: bool = True


@dataclass
class ThresholdConfig:
    """Thresholds that trigger adaptive governance actions.

    battery_pause_percent  – pause syncing when battery drops below this.
    battery_resume_percent – resume syncing when battery rises above this.
    cpu_pause_load         – pause syncing when 1-min load avg exceeds this.
    cpu_resume_load        – resume syncing when 1-min load avg drops below this.
    network_required_ssids – if non-empty, syncing only allowed on these SSIDs.
    """

    battery_pause_percent: int = 20
    battery_resume_percent: int = 30
    cpu_pause_load: float = 0.80
    cpu_resume_load: float = 0.50
    network_required_ssids: List[str] = field(default_factory=list)


@dataclass
class ActionConfig:
    """What actions to take when thresholds are crossed.

    Each 'pause' boolean controls whether the sidecar calls the Syncthing
    pause/resume REST endpoint. Throttle values (kbps) let you reduce
    bandwidth instead of (or in addition to) pausing — 0 means no throttle.
    """

    on_battery_pause: bool = True
    on_battery_throttle_kbps: int = 0
    on_cpu_high_pause: bool = True
    on_cpu_high_throttle_kbps: int = 100
    on_network_loss_pause: bool = True
    on_network_restore_resume: bool = True


@dataclass
class SidecarConfig:
    """Root configuration for the hermes-sidecar process."""

    syncthing: SyncthingConfig = field(default_factory=SyncthingConfig)
    monitor: MonitorConfig = field(default_factory=MonitorConfig)
    thresholds: ThresholdConfig = field(default_factory=ThresholdConfig)
    actions: ActionConfig = field(default_factory=ActionConfig)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a nested dict (suitable for YAML dump)."""
        return asdict(self)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_config(path: Optional[str] = None) -> SidecarConfig:
    """Load configuration from a YAML file, filling in defaults for missing keys.

    Args:
        path: Filesystem path to the config file.  Defaults to
              ``~/.hermes/sidecar/config.yaml``.

    Returns:
        A fully-populated :class:`SidecarConfig`.

    Raises:
        FileNotFoundError: if the config file does not exist.
        yaml.YAMLError: if the file is not valid YAML.
    """
    if path is None:
        path = str(Path.home() / ".hermes" / "sidecar" / "config.yaml")

    config_path = Path(path).expanduser()

    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path, "r", encoding="utf-8") as fh:
        raw: Dict[str, Any] = yaml.safe_load(fh) or {}

    return _parse_config(raw)


def generate_default_config(path: Optional[str] = None) -> Path:
    """Write the default configuration to *path*, creating parent directories.

    Args:
        path: Target filesystem path.  Defaults to
              ``~/.hermes/sidecar/config.yaml``.

    Returns:
        The :class:`Path` where the config was written.
    """
    if path is None:
        path = str(Path.home() / ".hermes" / "sidecar" / "config.yaml")

    config_path = Path(path).expanduser()
    config_path.parent.mkdir(parents=True, exist_ok=True)

    with open(config_path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(DEFAULT_CONFIG, fh, default_flow_style=False, sort_keys=False)

    return config_path


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _deep_merge(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge *overlay* into *base* (mutates *base*)."""
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def _parse_config(raw: Dict[str, Any]) -> SidecarConfig:
    """Merge *raw* over defaults and instantiate typed config objects."""

    # Start with a deep copy of the defaults
    merged: Dict[str, Any] = yaml.safe_load(
        yaml.safe_dump(DEFAULT_CONFIG, default_flow_style=False)
    )
    _deep_merge(merged, raw)

    return SidecarConfig(
        syncthing=SyncthingConfig(**merged.get("syncthing", {})),
        monitor=MonitorConfig(**merged.get("monitor", {})),
        thresholds=ThresholdConfig(**merged.get("thresholds", {})),
        actions=ActionConfig(**merged.get("actions", {})),
    )
