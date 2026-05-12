# hermes-sidecar

Adaptive resource governance for Syncthing parity — keeps your laptop↔VM files
in sync without burning CPU or battery.

## Problem

Syncthing is the right tool for continuous laptop↔VM file sync, but with
incomplete `.stignore` patterns it can scan over 500K files including build
caches, virtualenvs, and node_modules — eating 115% CPU and killing battery life.
hermes-sidecar fixes this at two levels:

1. **Prevention** — auto-generates comprehensive `.stignore` files that cut
   tracked files by 97%+ (566K → 13.6K on real-world projects)
2. **Adaptive throttling** — monitors power source, battery level, and CPU load
   to govern Syncthing bandwidth and folder pausing in real time

## Features

- **Power-aware daemon** — 5 governance states driven by AC/battery/CPU metrics
- **Hysteresis protection** — requires 2 consecutive poll cycles before state
  transitions, preventing flapping
- **Battery critical override** — pauses all syncing immediately below 20%
  battery (no hysteresis wait)
- **CPU throttling** — cuts bandwidth to 50 KB/s when system load exceeds 70%
- **Graceful shutdown** — restores AC-state throttle and resumes all folders on
  SIGTERM/SIGINT
- **Status dashboard** — real-time view of power, battery, CPU, Syncthing
  folders, and device connections
- **`.stignore` generator** — auto-detects project type (Python, ML, LaTeX,
  generic) and writes the right ignore patterns
- **Desktop notifications** — native notify-send / osascript alerts on state changes
- **Cross-platform** — macOS and Linux, with LaunchAgent and systemd templates
- **Zero-config start** — auto-detects Syncthing API key from config.xml

## Installation

```bash
# Option 1: pip install from the repo
pip install /path/to/hermes-sidecar

# Option 2: install from source
git clone https://github.com/ashesh8500/hermes-sidecar
cd hermes-sidecar
pip install .
```

Requires Python 3.10+ and a running Syncthing instance.

## Quick start

```bash
# 1. Generate default config
hermes-sidecar init-config

# 2. Generate .stignore for your project (reduces Syncthing file count 97%+)
cd ~/projects/my-project
hermes-sidecar generate-stignore

# 3. See current system + Syncthing state
hermes-sidecar status

# 4. Run a single poll cycle (dry run)
hermes-sidecar daemon --one-shot

# 5. Start the adaptive daemon
hermes-sidecar daemon
```

## Daemon deployment

### macOS (LaunchAgent)

```bash
cp launchd/com.hermes-sidecar.plist ~/Library/LaunchAgents/
# Edit the plist to replace REPLACE_WITH_USERNAME with your username
launchctl load ~/Library/LaunchAgents/com.hermes-sidecar.plist
```

The daemon starts at login, restarts automatically, and writes logs to
`~/.hermes/sidecar/daemon.log`.

### Linux (systemd user unit)

```bash
mkdir -p ~/.config/systemd/user
cp systemd/hermes-sidecar.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now hermes-sidecar.service
```

View logs: `journalctl --user -u hermes-sidecar -f`

## Usage

```
hermes-sidecar status
```

Show the full state dashboard: power source, battery percentage, CPU load,
Syncthing uptime, current throttle, connected devices, and per-folder sync
progress.

```
hermes-sidecar daemon [--one-shot]
```

Run the adaptive daemon event loop.  With `--one-shot`, evaluates state once and
exits — useful for testing or cron jobs.

```
hermes-sidecar throttle [KBPS]
```

Get or set Syncthing bandwidth.  Omit KBPS to read current limits.  0 = unlimited.

```bash
$ hermes-sidecar throttle
Current throttle:  send=unlimited  recv=unlimited

$ hermes-sidecar throttle 500
Throttle set to 500 KB/s.
```

```
hermes-sidecar pause
hermes-sidecar resume
```

Pause or resume all Syncthing folders.  If `syncthing.target_device_id` is set
in config, these operate on that device only.

```
hermes-sidecar stop
hermes-sidecar start
```

Stop restores unlimited throttle and kills the Syncthing process.  Start
launches Syncthing in the background with `--no-browser --no-restart`.

```
hermes-sidecar generate-stignore [--path PATH]
```

Auto-detects the project type from files present in the directory and emits a
`.stignore` with the appropriate ignore patterns.  Templates cover:

| Template  | Special patterns                                              |
|-----------|---------------------------------------------------------------|
| generic   | OS junk, git, venv, node_modules, build artifacts, secrets    |
| python    | + coverage, .hypothesis, pip-wheel-metadata                   |
| ml        | + model weights (.pt, .safetensors), wandb/, checkpoints/     |
| latex     | + .aux, .log, .toc, .synctex*, _minted-*/                     |

```bash
hermes-sidecar init-config
```

Writes a default configuration file to `~/.hermes/sidecar/config.yaml`.

## State machine

The daemon evaluates three metrics on every poll cycle (default: 30s) and selects
a target state.  States are listed in priority order — higher rules override
lower ones.

```
                    ┌──────────────┐
                    │  CPU > 70%?  │──yes──▶  HIGH_CPU (50 KB/s, not paused)
                    └──────┬───────┘
                           │no
                    ┌──────▼───────┐
                    │  AC power?   │──yes──▶  AC (unlimited, not paused)
                    └──────┬───────┘
                           │no (battery)
                    ┌──────▼──────────┐
                    │  Battery > 50%? │──yes──▶  BATTERY_NORMAL (200 KB/s)
                    └──────┬──────────┘
                           │no
                    ┌──────▼──────────┐
                    │  Battery > 20%? │──yes──▶  BATTERY_LOW (50 KB/s)
                    └──────┬──────────┘
                           │no
                           ▼
                    BATTERY_CRITICAL (0 KB/s, paused)
```

**Hysteresis:** A new target must be observed for 2 consecutive polls before
transitioning, preventing rapid state flapping.  The `battery_critical` state
bypasses hysteresis — it engages immediately to protect a dying battery.

**Shutdown:** On SIGTERM/SIGINT, the daemon restores the AC state (unlimited
throttle, all folders resumed) before exiting.

## Configuration

All settings live in `~/.hermes/sidecar/config.yaml`.  Run `hermes-sidecar
init-config` to generate the default file.

```yaml
# ── Syncthing connection ──────────────────────────────
syncthing:
  api_url: "http://localhost:8384"     # Syncthing REST endpoint
  api_key: ""                           # Auto-detected from config.xml if empty
  config_path: "~/.config/syncthing"    # Path to Syncthing config directory
  device_name: ""                       # Local device name (informational)
  target_device_id: ""                  # Remote device ID for per-device pause/resume

# ── Monitor polling ───────────────────────────────────
monitor:
  poll_interval_seconds: 5             # How often to poll sensors
  power_enabled: true                   # Enable power source monitoring
  cpu_enabled: true                     # Enable CPU load monitoring
  network_enabled: true                 # Enable network connectivity monitoring

# ── Trigger thresholds ────────────────────────────────
thresholds:
  battery_pause_percent: 20            # Pause syncing below this battery level
  battery_resume_percent: 30           # Resume syncing above this level
  cpu_pause_load: 0.80                 # Pause syncing above this CPU load (fraction)
  cpu_resume_load: 0.50               # Resume syncing below this CPU load
  network_required_ssids: []           # If non-empty, only sync on these Wi-Fi SSIDs

# ── Actions on threshold crossing ─────────────────────
actions:
  on_battery_pause: true               # Pause all folders on low battery
  on_battery_throttle_kbps: 0          # Alternative: throttle instead of pause
  on_cpu_high_pause: true              # Pause all folders on high CPU
  on_cpu_high_throttle_kbps: 100       # Throttle bandwidth on high CPU
  on_network_loss_pause: true          # Pause on network loss
  on_network_restore_resume: true      # Resume when network returns
```

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                    hermes-sidecar                    │
├─────────────────────────────────────────────────────┤
│  CLI (click)                                        │
│  ┌──────┐ ┌────────┐ ┌────────┐ ┌───────────────┐  │
│  │status│ │ daemon │ │throttle│ │generate-stignore│  │
│  └──────┘ └────────┘ └────────┘ └───────────────┘  │
├─────────────────────────────────────────────────────┤
│  Daemon (event loop)                                │
│  ┌──────────────┐  ┌───────────────┐               │
│  │ StateMachine │──│ 30s poll loop │               │
│  │ (hysteresis) │  │ power/cpu/... │               │
│  └──────────────┘  └───────────────┘               │
├─────────────────────────────────────────────────────┤
│  Monitors                    Actions                │
│  ┌───────┐ ┌─────┐ ┌──────┐  ┌──────────┐         │
│  │ power │ │ cpu │ │network│  │syncthing │           │
│  │(pmset) │ │psutil│ │(SSID)│  │(REST API)│           │
│  └───────┘ └─────┘ └──────┘  └──────────┘         │
│                               ┌────────┐           │
│                               │ notify │           │
│                               └────────┘           │
├─────────────────────────────────────────────────────┤
│  Config                    stignore                 │
│  ┌──────────────────────┐  ┌─────────────────────┐ │
│  │ config.yaml          │  │ Python / ML / LaTeX │ │
│  │ (dataclass + merge)  │  │ / generic templates │ │
│  └──────────────────────┘  └─────────────────────┘ │
└─────────────────────────────────────────────────────┘
          │                                  │
          ▼                                  ▼
   Syncthing (REST)              .stignore (filesystem)
   localhost:8384                ~/projects/.stignore
```

## Project structure

```
hermes-sidecar/
├── bin/hermes-sidecar          # Launcher script (adds repo to sys.path)
├── sidecar/
│   ├── __init__.py
│   ├── cli.py                  # Click CLI — all 9 commands
│   ├── config.py               # YAML config + typed dataclasses
│   ├── daemon.py               # StateMachine + event loop
│   ├── stignore.py             # .stignore generator + project detection
│   ├── monitors/
│   │   ├── __init__.py
│   │   ├── power.py            # pmset / sysfs power detection
│   │   ├── cpu.py              # psutil CPU load
│   │   └── network.py          # Interface + SSID detection
│   └── actions/
│       ├── __init__.py
│       ├── syncthing.py        # Syncthing REST API client
│       └── notify.py           # osascript / notify-send
├── launchd/
│   └── com.hermes-sidecar.plist  # macOS LaunchAgent template
├── systemd/
│   └── hermes-sidecar.service    # Linux systemd user unit
├── pyproject.toml
└── README.md
```

## Contributing

See [AGENTS.md](AGENTS.md) for development conventions, architecture details, and
guide for extending the project.

## License

MIT
