# Changelog

All notable changes to hermes-sidecar will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.1.0] — 2026-05-13

Initial release.  Achieves the core goal: keep Syncthing file sync running
between laptop and VM without burning CPU or battery.

### Added

- **CLI** (`hermes-sidecar`) with 9 commands:
  - `status` — power source, battery level, CPU load, Syncthing folder states,
    device connections, current throttle
  - `daemon` — adaptive governance event loop with `--one-shot` flag for
    single-poll dry runs
  - `throttle [KBPS]` — get/set Syncthing bandwidth limit (0 = unlimited)
  - `pause` / `resume` — pause or resume all folders (or a specific device if
    `syncthing.target_device_id` is configured)
  - `stop` / `start` — kill or launch the Syncthing process
  - `generate-stignore` — auto-detect project type and emit appropriate
    `.stignore` patterns
  - `init-config` — write default config to `~/.hermes/sidecar/config.yaml`
- **Adaptive daemon** with a 5-state governance machine:
  - `high_cpu` — CPU > 70% → throttle to 50 KB/s
  - `ac` — plugged in → unlimited throttle
  - `battery_normal` — battery > 50% → 200 KB/s
  - `battery_low` — battery 20–50% → 50 KB/s
  - `battery_critical` — battery < 20% → pause all folders immediately
- **Hysteresis protection** — requires 2 consecutive poll cycles in a new state
  before transitioning, preventing flapping
- **Graceful shutdown** — SIGTERM/SIGINT restores AC state (unlimited throttle,
  all folders resumed) before exiting
- **Power monitoring** — macOS (`pmset -g batt`) and Linux
  (`/sys/class/power_supply`) support with safe AC fallback on failure
- **CPU monitoring** — psutil-based with `sysctl vm.loadavg` fallback on macOS
- **Network monitoring** — primary interface detection, connectivity check, Wi-Fi
  SSID reading (macOS airport, Linux iwgetid)
- **Syncthing REST client** — full CRUD wrapper for the Syncthing REST API with
  CSRF token handling, auto-detected API key from config.xml, and
  `SYNCTHING_API_KEY` env var fallback
- **`.stignore` generator** — 4 project type templates (generic, Python, ML,
  LaTeX) with auto-detection from directory contents
- **Desktop notifications** — native alerts on state changes via osascript
  (macOS) and notify-send (Linux)
- **YAML configuration** — typed dataclasses with recursive merge of user config
  over defaults
- **macOS LaunchAgent** template (`launchd/com.hermes-sidecar.plist`)
- **Linux systemd user unit** template (`systemd/hermes-sidecar.service`)
- **Launcher script** (`bin/hermes-sidecar`) for running from source without pip
  install
