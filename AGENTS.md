# AGENTS.md — Agentic Development Guide

This file tells agentic coding tools (Claude Code, Codex, Hermes) how to work on
the hermes-sidecar repository.  Read it before writing any code.

## Project overview

hermes-sidecar is a resource-aware sidecar process that governs Syncthing
bandwidth and folder pausing based on real-time system conditions — power source,
battery level, and CPU load.  It runs as a long-lived daemon (macOS LaunchAgent
or Linux systemd unit) and provides a CLI for manual control.

The project lives at the boundary between system monitoring and REST API
orchestration.  It reads local hardware state and translates it into Syncthing
REST calls (`PATCH /rest/config/options`, `PATCH /rest/config/folders/<id>`).

## Architecture

```
CLI (Click) ──▶ Daemon (event loop) ──▶ StateMachine ──▶ SyncthingClient (REST)
                     │                      │
                     ▼                      ▼
              Monitors (power,         State thresholds
              cpu, network)            + hysteresis (2 polls)
```

**Separation of concerns:**

- **Monitors** (`sidecar/monitors/`) — read system state.  Pure functions,
  no side effects.  Return typed data (str, int, float, Optional).
- **Actions** (`sidecar/actions/`) — mutate external state.  SyncthingClient for
  REST calls, notify for desktop alerts.  Return bool for success/failure.
- **Daemon** (`sidecar/daemon.py`) — the orchestration layer.  Polls monitors,
  runs the state machine, calls actions.  No system-level or API-level code
  should live here.
- **Config** (`sidecar/config.py`) — typed dataclasses with YAML
  serialization.  Defaults are defined once in `DEFAULT_CONFIG` and merged with
  user config via `_deep_merge`.
- **CLI** (`sidecar/cli.py`) — thin Click wrappers.  Commands call config,
  monitors, actions, and daemon.  No business logic.

## Development conventions

### Python

- **Version:** 3.10+ (`from __future__ import annotations` everywhere)
- **Type hints:** Required on all public functions.  Use `Optional[X]` not
  `X | None` for consistency with the existing codebase.
- **Imports:** stdlib first, then third-party, then `sidecar.*`.  Group with
  blank lines.
- **Docstrings:** Google-style.  Every public function.  First line is a
  one-sentence summary.
- **Error handling:** Monitors return `None` or a safe default on failure (never
  raise).  Actions return `bool`.  The daemon catches and logs exceptions from
  individual polls; it never crashes the event loop.
- **Linting:** The project uses no linter config yet.  Keep code clean: 4-space
  indents, 88-char lines, trailing commas in multi-line constructs.

### Cross-platform

Every platform-specific code path is gated on `platform.system()`:

```python
if platform.system() == "Darwin":
    # macOS: pmset, osascript, sysctl
elif platform.system() == "Linux":
    # Linux: sysfs, notify-send, iwgetid
else:
    # Fallback: assume AC power, return safe defaults
```

Platform checks happen in leaf functions only — callers are platform-agnostic.
When adding a new capability, follow the pattern: a public function that
dispatches to `_macos_*()` / `_linux_*()` helpers.

### Config system

Config is defined in three layers:

1. `DEFAULT_CONFIG` dict in `config.py` — the canonical defaults
2. User's `~/.hermes/sidecar/config.yaml` — overrides
3. `_deep_merge()` — recursive dict merge, user wins on leaf keys

**Critical rule:** If you add a config key, add it to `DEFAULT_CONFIG` and the
corresponding dataclass.  Missing keys in the user file are filled from
defaults — if you skip the default, a missing key becomes a runtime crash.

**Do not** add logic that reads config during module import.  Config loading
happens at CLI invocation time via `load_config()`.  The daemon module has
fallback stubs for standalone use, but those are temporary shims from parallel
development — don't add new ones.

### State machine

The `StateMachine` in `daemon.py` is the core governance engine.  Key rules:

- **Priority:** CPU > AC > battery tiers.  `determine_target()` encodes this.
- **Hysteresis:** 2 consecutive polls in a new target before committing.  The
  `consecutive_count` resets when the target changes.
- **Battery critical bypass:** `battery_critical` commits immediately — no
  hysteresis wait.  This is intentional: a dying battery shouldn't wait for
  confirmation.
- **State application:** `apply()` sets throttle (via
  `SyncthingClient.set_throttle`) and pause state (via
  `SyncthingClient.pause_all_folders` / `resume_all_folders`).  These are
  separate REST calls; if one fails the other still runs.

When adding a new state, update five places:

1. `STATE_THROTTLE` dict — throttle value for this state
2. `STATE_LABELS` dict — human-readable label
3. `determine_target()` — decision logic
4. `apply()` — if the new state needs special pause/throttle behavior (most
   don't — they just set throttle and resume folders)
5. `restore_ac_state()` — only if the state needs special cleanup on shutdown

### Commit discipline

- One logical change per commit
- Commit messages: `<area>: <verb> <what>` — e.g. `cli: add --one-shot flag to daemon`
- No generated files (`.pyc`, `__pycache__`, `*.egg-info`) in commits
- Test before committing: `hermes-sidecar daemon --one-shot` should work

## How to extend

### Adding a new monitor

1. Create `sidecar/monitors/<name>.py`
2. Export one or more public functions that return typed data or `None`
3. Follow the platform-dispatch pattern: public function → `_macos_*() / _linux_*()`
4. Return safe defaults on failure (never crash, never raise)
5. Add to `MonitorConfig` dataclass if it needs a toggle
6. Add to `DEFAULT_CONFIG["monitor"]` if it needs config
7. Import in `daemon.py` with a try/except ImportError fallback
8. Call from the daemon loop in `run_daemon()`

### Adding a new action

1. Create `sidecar/actions/<name>.py`
2. Return `bool` from the primary function (True = success)
3. If the action calls an external API, follow the SyncthingClient pattern:
   `_get()` / `_patch()` helpers with timeout and error handling
4. Import in `cli.py` if the action needs its own CLI command

### Adding a new CLI command

1. Add a `@main.command()` decorated function to `cli.py`
2. Use Click options/arguments for parameters
3. Call config, monitors, and actions — no business logic in the command itself
4. Print results with `click.echo()` / `click.secho()`
5. Use `sys.exit(1)` on fatal errors
6. Update the module docstring at the top of `cli.py` with the new command

### Adding platform support

Currently supported: macOS (Darwin) and Linux.  To add a new platform:

1. Add `elif _SYSTEM == "NewOS":` blocks in each monitor (`power.py`, `cpu.py`,
   `network.py`) and action (`notify.py`, `syncthing.py`)
2. Add platform-specific path detection in `SyncthingClient._detect_api_key()`
   for the Syncthing config.xml location
3. Add a new service template under `platforms/<name>/` for daemon deployment
4. Add any new dependencies to `pyproject.toml` with platform markers

### Adding a new daemon state

The state machine currently has 5 states.  To add a sixth:

1. Add to `STATE_THROTTLE` dict in `daemon.py`
2. Add to `STATE_LABELS` dict in `daemon.py`
3. Add decision logic to `determine_target()` — respect the priority chain
4. Add to `apply()` if it needs special behavior (most states just set throttle
   and resume folders)
5. Add to `restore_ac_state()` only if the new state needs cleanup
6. Test: `hermes-sidecar daemon --one-shot`

## Testing approach

Tests live in `tests/`.  The project uses pytest.

**Testing without real Syncthing:** The `SyncthingClient` takes `api_key` and
`api_base` as constructor arguments.  For unit tests, inject a fake API key and
mock `urllib.request.urlopen` to return canned responses.  See the existing
pattern:

```python
from unittest.mock import patch, Mock

def test_throttle_set():
    client = SyncthingClient(api_key="fake-key", api_base="http://localhost:9999")
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = Mock()
        mock_resp.status = 200
        mock_urlopen.side_effect = [mock_resp, mock_resp]  # CSRF + PATCH
        assert client.set_throttle(500)
```

**Testing the state machine:** Instantiate `StateMachine(hysteresis=2)` and call
`determine_target()` and `transition()` directly.  No Syncthing needed — the
state machine is pure logic.

**Testing monitors:** Mock `subprocess.run` for `pmset` / sysfs calls.  Provide
canned output strings.

## Common pitfalls

### Syncthing REST API

- **CSRF token:** All mutating calls (`PATCH`) require the header
  `X-CSRF-Token-<value>: anything`.  The token value is `urAccepted` from
  `/rest/system/status`.  The GET for the CSRF token and the PATCH must happen
  in the same request session — don't cache the token.
- **API key location:** macOS: `~/Library/Application Support/Syncthing/config.xml`.
  Linux: `~/.config/syncthing/config.xml`.  Fallback: `SYNCTHING_API_KEY` env var.
- **Folder ID vs label:** The REST API uses folder IDs (e.g. `projects`), not
  labels (e.g. "Projects").  Always use `folder["id"]`.
- **Completion endpoint:** `/rest/db/completion` returns a dict keyed by folder
  ID.  Some folders may be missing from the response — treat missing keys as
  100% complete.

### Power detection

- **macOS:** `pmset -g batt` output format changes between macOS versions.  The
  parser in `power.py` looks for `"AC Power"` / `"Battery Power"` substrings
  (case-insensitive) and the first `(\d+)%` pattern found.
- **Linux:** Not all systems have `BAT*` entries in `/sys/class/power_supply`.
  Desktops without batteries return `None` from `get_battery_percent()` — the
  daemon treats `None` as 100% (assumes AC).
- **Fallback is AC:** When detection fails, the system defaults to AC power with
  100% battery.  This is intentional: you don't want to throttle syncing on a
  desktop just because battery detection failed.

### CPU detection

- **psutil required:** The preferred path uses `psutil.cpu_percent(interval=1)`,
  which blocks for 1 second per poll.  Without psutil, falls back to
  `sysctl -n vm.loadavg` on macOS (divides loadavg by cpu_count for approximate
  percentage).
- **First poll:** `psutil.cpu_percent()` returns a near-zero value on first call
  (no baseline).  The daemon handles this by using `interval=1` for blocking
  polls.

### Battery detection edge cases

- **macOS desktops:** `pmset -g batt` returns nothing.  `detect_power_source()`
  returns `"ac"`, `get_battery_percent()` returns `None`.
- **Multiple batteries:** Linux may have `BAT0` and `BAT1`.  Use the first one
  that provides capacity.
- **Charging while on battery:** `pmset -g batt` reports "AC Power" while
  charging even if physically unplugged from AC (MagSafe).  This is correct
  behavior — treat it as AC.

## Design philosophy

- **Simple over clever.**  A 30-line state machine with explicit if/elif chains
  beats a configurable rules engine.  If you need a flowchart to understand it,
  simplify.
- **Cross-platform, not least-common-denominator.**  Platform-specific code
  lives in leaf functions.  The public API is platform-agnostic.  Prefer
  platform-native tools (`pmset`, `sysctl`) over portable abstractions.
- **Fail gracefully.**  Every monitor returns a safe default on failure.  The
  daemon never crashes on a bad poll.  If Syncthing is down, the daemon keeps
  looping and retries on the next cycle.
- **Config over code.**  Thresholds, intervals, and throttle values live in
  `config.yaml`, not in constants.  The daemon module has fallback constants for
  bootstrapping, but the canonical values are in config.
- **No framework overhead.**  Standard library + Click + psutil + PyYAML.
  Nothing async, no dependency injection, no ORM.

## File map

| Path | Purpose | Edit when |
|------|---------|-----------|
| `sidecar/cli.py` | Click CLI entry point | Adding/removing commands |
| `sidecar/config.py` | Typed config + YAML I/O | Adding config keys |
| `sidecar/daemon.py` | StateMachine + event loop | Adding states, changing thresholds |
| `sidecar/stignore.py` | .stignore generator | Adding project types, patterns |
| `sidecar/monitors/power.py` | Battery + power source | Fixing power detection bugs |
| `sidecar/monitors/cpu.py` | CPU load via psutil | Adding CPU detection methods |
| `sidecar/monitors/network.py` | Interface + SSID detection | Adding network conditions |
| `sidecar/actions/syncthing.py` | Syncthing REST client | Adding API endpoints |
| `sidecar/actions/notify.py` | Desktop notifications | Adding notification backends |
| `bin/hermes-sidecar` | Launcher script | Changing import paths |
| `launchd/com.hermes-sidecar.plist` | macOS service template | Changing daemon args |
| `systemd/hermes-sidecar.service` | Linux service template | Changing daemon args |
| `pyproject.toml` | Package metadata + deps | Adding dependencies |
| `tests/` | Test suite | Adding tests |
