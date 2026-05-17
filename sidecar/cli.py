"""
hermes-sidecar CLI — adaptive resource governance for Syncthing parity.

Commands
    kanban              View the Hermes Agent kanban board (progress + status).
    status              Show power, battery, CPU, and Syncthing folder states.
    daemon              Run the adaptive daemon event loop (--one-shot for single poll).
    throttle [KBPS]     Get or set the Syncthing bandwidth limit (0 = unlimited).
    pause               Pause the remote Syncthing device (or all folders).
    resume              Resume the remote device and restore the configured throttle.
    stop                Restore full throttle and kill the Syncthing process.
    start               Launch Syncthing in the background.
    generate-stignore   Generate a .stignore file for the current project.
    init-config         Create a default configuration file.
"""

from __future__ import annotations

import logging
import os
import platform
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import Any, Dict, Optional

from sidecar import kanban as _kanban

IS_MACOS: bool = platform.system() == "Darwin"

# ---------------------------------------------------------------------------
# Click CLI
# ---------------------------------------------------------------------------

import click

from sidecar.config import generate_default_config, load_config, SidecarConfig
from sidecar.daemon import run_daemon
from sidecar.actions.syncthing import SyncthingClient
from sidecar.actions.notify import notify
from sidecar.monitors.power import detect_power_source, get_battery_percent
from sidecar.monitors.cpu import get_cpu_load
from sidecar.stignore import generate_stignore as _generate_stignore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EMOJI = {
    "ac": "\u26a1",        # ⚡
    "battery": "\U0001f50b",  # 🔋
    "online": "\u2705",    # ✅
    "offline": "\u274c",   # ❌
    "paused": "\u23f8\ufe0f",   # ⏸️
    "syncing": "\U0001f504",  # 🔄
    "ok": "\u2705",
    "warn": "\u26a0\ufe0f",   # ⚠️
}


def _format_kbps(kbps: int) -> str:
    """Format a bandwidth value in human-readable form."""
    if kbps == 0:
        return "unlimited"
    if kbps >= 1000:
        return f"{kbps / 1000:.1f} MB/s"
    return f"{kbps} KB/s"


def _get_syncthing_binary() -> Optional[Path]:
    """Detect the Syncthing binary location."""
    candidates = [
        "syncthing",
        "/usr/local/bin/syncthing",
        "/opt/homebrew/bin/syncthing",
    ]
    if IS_MACOS:
        candidates.extend([
            "/Applications/Syncthing.app/Contents/MacOS/syncthing",
            str(Path.home() / "Applications/Syncthing.app/Contents/MacOS/syncthing"),
        ])

    for candidate in candidates:
        p = Path(candidate) if candidate.startswith("/") else None
        if p and p.exists() and p.is_file():
            return p

    # Try which
    try:
        result = subprocess.run(
            ["which", "syncthing"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return Path(result.stdout.strip())
    except Exception:
        pass

    return None


def _kill_syncthing() -> bool:
    """Kill any running Syncthing processes."""
    try:
        if IS_MACOS:
            subprocess.run(
                ["pkill", "-f", "syncthing"],
                capture_output=True, timeout=10,
            )
        else:
            subprocess.run(
                ["pkill", "-f", "syncthing"],
                capture_output=True, timeout=10,
            )
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------

@click.group()
@click.version_option(version="0.1.0", prog_name="hermes-sidecar")
def main() -> None:
    """hermes-sidecar — adaptive resource governance for Syncthing parity."""


@main.command()
def status() -> None:
    """Show power source, battery level, CPU load, and Syncthing folder states."""
    # ── System metrics ──
    power_source = detect_power_source()
    battery_pct = get_battery_percent()
    cpu_load = get_cpu_load()

    click.echo("─" * 50)
    click.echo("  hermes-sidecar status")
    click.echo("─" * 50)

    # Power
    power_emoji = _EMOJI["ac"] if power_source == "ac" else _EMOJI["battery"]
    power_label = "AC Power" if power_source == "ac" else "Battery"
    click.echo(f"  Power:     {power_emoji}  {power_label}")
    if battery_pct is not None:
        click.echo(f"  Battery:    {battery_pct}%")

    # CPU
    if cpu_load is not None:
        # get_cpu_load returns 0.0–1.0 fraction; display as percentage.
        cpu_pct = cpu_load * 100
        color = "green" if cpu_pct < 50 else ("yellow" if cpu_pct < 80 else "red")
        click.secho(f"  CPU load:   {cpu_pct:.0f}%", fg=color)
    else:
        click.echo("  CPU load:   (psutil not available)")

    click.echo()

    # ── Syncthing ──
    client = SyncthingClient()
    if not client.api_key:
        click.echo("  Syncthing:  \u26a0\ufe0f  No API key — start Syncthing or set SYNCTHING_API_KEY")
        return

    st_status = client.get_status()
    if "error" in st_status:
        click.echo(f"  Syncthing:  {_EMOJI['offline']}  Not reachable ({st_status['error']})")
        return

    click.echo(f"  Syncthing:  {_EMOJI['online']}  Running")

    # Uptime
    uptime_s = st_status.get("uptime", 0)
    if uptime_s >= 86400:
        days = uptime_s // 86400
        hours = (uptime_s % 86400) // 3600
        click.echo(f"  Uptime:     {days}d {hours}h")
    elif uptime_s >= 3600:
        hours = uptime_s // 3600
        mins = (uptime_s % 3600) // 60
        click.echo(f"  Uptime:     {hours}h {mins}m")
    else:
        click.echo(f"  Uptime:     {uptime_s // 60}m")

    # Bandwidth
    opts = st_status.get("options", {})
    send_kbps = opts.get("maxSendKbps", 0)
    recv_kbps = opts.get("maxRecvKbps", 0)
    click.echo(f"  Throttle:   send={_format_kbps(send_kbps)}  recv={_format_kbps(recv_kbps)}")

    click.echo()

    # ── Connections ──
    conns = client.get_connections()
    connections = conns.get("connections", {})
    if connections:
        click.echo("  Connections:")
        for dev_id, info in connections.items():
            short_id = dev_id[:8]
            connected = info.get("connected", False)
            status_emoji = _EMOJI["online"] if connected else _EMOJI["offline"]
            click.echo(f"    {short_id}...  {status_emoji}  {info.get('type', '?')}")
    else:
        click.echo("  Connections: none")

    click.echo()

    # ── Folder states ──
    folders = client.get_folders()
    if not folders:
        click.echo("  Folders:    none configured")
        return

    # Get completion data
    completions = client.get_folder_stats()

    click.echo("  Folders:")
    for folder in folders:
        fid = folder.get("id", "?")
        label = folder.get("label", fid)
        path = folder.get("path", "?")
        paused = folder.get("paused", False)

        f_emoji = _EMOJI["paused"] if paused else _EMOJI["syncing"]
        f_state = "paused" if paused else "active"

        # Completion percentage
        comp = completions.get(fid, {})
        if "error" not in comp:
            need_bytes = comp.get("needBytes", 0)
            if need_bytes == 0:
                sync_str = "100% synced"
            else:
                global_bytes = comp.get("globalBytes", 0)
                if global_bytes > 0:
                    pct = 100 * (1 - need_bytes / global_bytes)
                    sync_str = f"{pct:.0f}% synced  ({_format_kbps(need_bytes // 1024)} to go)"
                else:
                    sync_str = f"need {_format_kbps(need_bytes // 1024)}"
        else:
            sync_str = "?"

        click.echo(f"    {f_emoji}  {label}  [{f_state}]  {sync_str}")

    click.echo()


@main.command()
@click.option("--one-shot", is_flag=True, help="Run a single poll cycle and exit.")
def daemon(one_shot: bool) -> None:
    """Run the adaptive daemon event loop.

    Monitors power, battery, and CPU, then governs Syncthing bandwidth and
    folder pause state accordingly.  Use --one-shot for a single poll cycle.
    """
    if one_shot:
        _daemon_one_shot()
    else:
        run_daemon()


def _daemon_one_shot() -> None:
    """Single-poll daemon: evaluate state and apply once, then exit."""
    config = load_config()

    power_source = detect_power_source()
    battery_pct = get_battery_percent()
    cpu_load = get_cpu_load()
    cpu_pct = (cpu_load or 0) * 100  # Convert fraction to percentage

    # Use the same thresholds as the daemon
    from sidecar.daemon import (
        StateMachine,
        CPU_HIGH_THRESHOLD,
        BATTERY_NORMAL_THRESHOLD,
        BATTERY_LOW_THRESHOLD,
    )

    sm = StateMachine(hysteresis=2, initial_state="ac")
    target = sm.determine_target(power_source, battery_pct or 100, cpu_pct)

    click.echo(f"One-shot poll: power={power_source}  batt={battery_pct}%  cpu={cpu_pct:.0f}%")
    click.echo(f"Target state:  {target}")

    if sm.api_key_present():
        sm._commit(target, logging.getLogger("hermes-sidecar.cli"))
        sm.apply(config)
        click.echo("State applied to Syncthing.")
    else:
        click.echo("\u26a0\ufe0f  No Syncthing API key — state not applied.")


@main.command()
@click.argument("kbps", required=False, type=int)
def throttle(kbps: Optional[int]) -> None:
    """Get or set the Syncthing bandwidth limit.

    KBPS is the bandwidth limit in KB/s.  0 means unlimited.
    Omit KBPS to display the current limit.
    """
    client = SyncthingClient()
    if not client.api_key:
        click.echo("Error: No Syncthing API key configured.", err=True)
        sys.exit(1)

    if kbps is None:
        # Get current
        st = client.get_status()
        if "error" in st:
            click.echo(f"Error: {st['error']}", err=True)
            sys.exit(1)
        opts = st.get("options", {})
        send = opts.get("maxSendKbps", 0)
        recv = opts.get("maxRecvKbps", 0)
        click.echo(f"Current throttle:  send={_format_kbps(send)}  recv={_format_kbps(recv)}")
        return

    if kbps < 0:
        click.echo("Error: throttle must be >= 0 (0 = unlimited).", err=True)
        sys.exit(1)

    ok = client.set_throttle(kbps)
    if ok:
        label = _format_kbps(kbps)
        click.echo(f"Throttle set to {label}.")
        notify("hermes-sidecar", f"Throttle set to {label}")
    else:
        click.echo("Error: Failed to set throttle.", err=True)
        sys.exit(1)


@main.command()
def pause() -> None:
    """Pause the remote Syncthing device (all folders).

    If a target_device_id is configured in config.yaml, that device is paused.
    Otherwise all folders are paused.
    """
    try:
        config = load_config()
    except FileNotFoundError:
        click.echo(
            "No config found.  Run 'hermes-sidecar init-config' first.",
            err=True,
        )
        sys.exit(1)

    client = SyncthingClient()
    if not client.api_key:
        click.echo("Error: No Syncthing API key configured.", err=True)
        sys.exit(1)

    # If a target device is specified, pause only that device.
    target_device = config.syncthing.target_device_id
    if target_device:
        ok = client.pause_device(target_device)
        if ok:
            click.echo(f"Device paused: {target_device[:16]}...")
        else:
            click.echo("Error: Failed to pause device.", err=True)
            sys.exit(1)
    else:
        # Pause all folders.
        ok = client.pause_all_folders()
        if ok:
            click.echo("All folders paused.")
        else:
            click.echo("Error: Failed to pause folders.", err=True)
            sys.exit(1)

    notify("hermes-sidecar", "Syncthing paused")


@main.command()
def resume() -> None:
    """Resume the remote Syncthing device and restore configured throttle."""
    try:
        config = load_config()
    except FileNotFoundError:
        click.echo(
            "No config found.  Run 'hermes-sidecar init-config' first.",
            err=True,
        )
        sys.exit(1)

    client = SyncthingClient()
    if not client.api_key:
        click.echo("Error: No Syncthing API key configured.", err=True)
        sys.exit(1)

    # Resume device or all folders.
    target_device = config.syncthing.target_device_id
    if target_device:
        ok = client.resume_device(target_device)
        if ok:
            click.echo(f"Device resumed: {target_device[:16]}...")
        else:
            click.echo("Error: Failed to resume device.", err=True)
            sys.exit(1)
    else:
        ok = client.resume_all_folders()
        if ok:
            click.echo("All folders resumed.")
        else:
            click.echo("Error: Failed to resume folders.", err=True)
            sys.exit(1)

    # Restore throttle from config default (or unlimited if not specified).
    throttle_kbps = getattr(
        getattr(config, "syncthing", None), "default_throttle_kbps", 0,
    )
    client.set_throttle(throttle_kbps)
    click.echo(f"Throttle restored to {_format_kbps(throttle_kbps)}.")

    notify("hermes-sidecar", "Syncthing resumed")


@main.command()
def stop() -> None:
    """Restore full throttle and kill the Syncthing process."""
    # Restore throttle first (unlimited).
    client = SyncthingClient()
    if client.api_key:
        # Try to restore before killing.
        client.set_throttle(0)
        client.resume_all_folders()
        click.echo("Throttle restored to unlimited.  All folders resumed.")

    # Kill Syncthing.
    ok = _kill_syncthing()
    if ok:
        click.echo("Syncthing process terminated.")
        notify("hermes-sidecar", "Syncthing stopped")
    else:
        click.echo("\u26a0\ufe0f  Could not terminate Syncthing (not running?).")


@main.command()
def start() -> None:
    """Launch Syncthing in the background."""
    binary = _get_syncthing_binary()
    if binary is None:
        click.echo(
            "Error: Syncthing binary not found.  Install Syncthing or add it to PATH.",
            err=True,
        )
        sys.exit(1)

    # Launch as background process.
    log_dir = Path.home() / ".hermes" / "sidecar"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "syncthing.log"

    try:
        with open(str(log_file), "a") as fh:
            subprocess.Popen(
                [str(binary), "--no-browser", "--no-restart"],
                stdout=fh,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
    except Exception as exc:
        click.echo(f"Error: Failed to start Syncthing: {exc}", err=True)
        sys.exit(1)

    click.echo(f"Syncthing launched ({binary}).  Log: {log_file}")
    notify("hermes-sidecar", "Syncthing started")


@main.command("generate-stignore")
@click.option(
    "--path", "-p",
    default=".",
    help="Project directory to scan (default: current directory).",
)
def generate_stignore_cmd(path: str) -> None:
    """Generate a .stignore file for the current project.

    Auto-detects the project type (Python, ML, LaTeX, or generic) and emits
    a .stignore with the appropriate pattern set.
    """
    project_dir = Path(path).expanduser().resolve()
    stignore_path = project_dir / ".stignore"

    # Detect project type
    from sidecar.stignore import detect_project_type

    ptype = detect_project_type(str(project_dir))
    content = _generate_stignore(str(project_dir))

    if stignore_path.exists():
        click.echo(
            f"\u26a0\ufe0f  {stignore_path} already exists.  "
            f"Overwrite? [y/N] ",
            nl=False,
        )
        answer = input().strip().lower()
        if answer not in ("y", "yes"):
            click.echo("Aborted.")
            return

    stignore_path.parent.mkdir(parents=True, exist_ok=True)
    stignore_path.write_text(content)
    click.echo(f"Generated .stignore for {ptype} project → {stignore_path}")


@main.command("init-config")
def init_config_cmd() -> None:
    """Create a default configuration file at ~/.hermes/sidecar/config.yaml."""
    config_path = generate_default_config()
    click.echo(f"Default config written to {config_path}")

    # Show a hint about editing.
    click.echo()
    click.echo("Edit this file to configure:")
    click.echo("  - syncthing.api_key          (auto-detected if Syncthing is running)")
    click.echo("  - syncthing.target_device_id (optional — for per-device pause/resume)")
    click.echo("  - thresholds.battery_pause_percent")
    click.echo("  - thresholds.cpu_pause_load")
    click.echo("  - actions.on_battery_pause")
    click.echo()
    click.echo("Then run:  hermes-sidecar daemon")


# ---------------------------------------------------------------------------
# Kanban board
# ---------------------------------------------------------------------------


@main.group("kanban", invoke_without_command=True)
@click.option("--mine", is_flag=True, help="Show only tasks assigned to $HERMES_PROFILE.")
@click.option("--watch", "-w", is_flag=True, help="Live-refresh the dashboard (Ctrl-C to exit).")
@click.option("--json", "as_json", is_flag=True, help="Export raw JSON instead of the dashboard.")
@click.option("--tenant", default=None, help="Filter by tenant name.")
@click.pass_context
def kanban_cmd(
    ctx: click.Context,
    mine: bool,
    watch: bool,
    as_json: bool,
    tenant: Optional[str],
) -> None:
    """View the Hermes Agent kanban board.

    Default (no flags): render a rich dashboard with progress bars,
    per-status breakdowns, per-assignee breakdowns, in-flight tasks,
    blocked tasks, and recent completions.

    \b
    Examples:
      hermes-sidecar kanban              # Full dashboard
      hermes-sidecar kanban --mine       # Only my tasks
      hermes-sidecar kanban --watch      # Live-refresh every 5s
      hermes-sidecar kanban --json       # Machine-readable export
    """
    # If a subcommand was given, invoke it
    if ctx.invoked_subcommand is not None:
        return

    if watch:
        _kanban.watch_loop(mine=mine, tenant=tenant)
        return

    tasks = _kanban.fetch_tasks(mine=mine, tenant=tenant)

    if as_json:
        click.echo(_kanban.export_json(tasks))
        return

    click.echo(_kanban.render_dashboard(tasks))


@kanban_cmd.command("mine")
def kanban_mine() -> None:
    """Alias for --mine: show only your assigned tasks."""
    tasks = _kanban.fetch_tasks(mine=True)
    click.echo(_kanban.render_dashboard(tasks))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    main()
