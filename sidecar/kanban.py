"""
Kanban board dashboard for hermes-sidecar.

Reads from the Hermes Agent kanban system (``hermes kanban list --json``)
and renders a rich terminal dashboard with progress bars, per-status
breakdowns, per-assignee breakdowns, and recent completions.

Cross-platform — depends only on the ``hermes`` CLI being on PATH.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Find hermes binary — prefer the active venv, fall back to PATH
_HERMES_BIN: Optional[str] = None


def _find_hermes() -> str:
    """Locate the hermes binary.  Cached after first call."""
    global _HERMES_BIN
    if _HERMES_BIN is not None:
        return _HERMES_BIN

    candidates = [
        # Active venv (most common)
        os.path.join(sys.prefix, "bin", "hermes"),
        # Hermes agent default venv
        os.path.join(str(Path.home()), ".hermes", "hermes-agent", "venv", "bin", "hermes"),
    ]

    for candidate in candidates:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            _HERMES_BIN = candidate
            return candidate

    # Fall back to shutil.which
    import shutil
    found = shutil.which("hermes")
    if found:
        _HERMES_BIN = found
        return found

    raise FileNotFoundError(
        "hermes CLI not found.  Install hermes-agent or add it to PATH."
    )


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------


def fetch_tasks(*, mine: bool = False, tenant: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return all non-archived kanban tasks as a list of dicts.

    Args:
        mine: If True, filter to tasks assigned to ``$HERMES_PROFILE``.
        tenant: Optional tenant filter.

    Returns:
        List of task dicts.  Empty list if no tasks or hermes is unavailable.
    """
    cmd = [_find_hermes(), "kanban", "list", "--json"]
    if mine:
        cmd.append("--mine")
    if tenant:
        cmd.extend(["--tenant", tenant])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
            env={**os.environ, "HERMES_NO_COLOR": "1"},
        )
        if result.returncode != 0:
            print(f"[kanban] hermes exited {result.returncode}: {result.stderr.strip()}", file=sys.stderr)
            return []
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        print("[kanban] hermes kanban list timed out", file=sys.stderr)
        return []
    except json.JSONDecodeError as exc:
        print(f"[kanban] failed to parse kanban JSON: {exc}", file=sys.stderr)
        return []
    except FileNotFoundError as exc:
        print(f"[kanban] {exc}", file=sys.stderr)
        return []


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

_STATUS_ICONS: Dict[str, str] = {
    "done": "✅",
    "running": "🔄",
    "ready": "▶️",
    "blocked": "⊘",
    "todo": "⏳",
    "triage": "📥",
}

_STATUS_ORDER: List[str] = ["running", "blocked", "ready", "todo", "triage", "done"]

TERM_WIDTH: int = 80  # Will be recomputed at render time if narrower needed
import shutil as _shutil
TERM_WIDTH = min(_shutil.get_terminal_size().columns, 80)


def _bar(percent: float, width: int = 20) -> str:
    """Draw a Unicode progress bar."""
    filled = int(round(percent / 100 * width))
    empty = width - filled
    blocks = "█" * filled + "░" * empty
    return f"│{blocks}│"


def _format_age(timestamp: Optional[float]) -> str:
    """Human-readable relative time from a Unix timestamp."""
    if timestamp is None:
        return "—"
    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    now = datetime.now(tz=timezone.utc)
    delta = now - dt
    seconds = int(delta.total_seconds())

    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def _truncate(text: str, max_width: int) -> str:
    """Truncate text to fit within max_width characters, adding ellipsis."""
    if len(text) <= max_width:
        return text
    return text[: max_width - 1] + "…"


# ---------------------------------------------------------------------------
# Dashboard rendering
# ---------------------------------------------------------------------------


def render_dashboard(
    tasks: List[Dict[str, Any]],
    *,
    assignee_filter: Optional[str] = None,
    tenant_filter: Optional[str] = None,
) -> str:
    """Render a full terminal dashboard from a list of task dicts.

    Returns a multi-line string suitable for direct printing.
    """
    if not tasks:
        return "No kanban tasks found.\n"

    # ── Aggregate stats ────────────────────────────────────────────────
    status_counts: Counter[str] = Counter()
    assignee_counts: Counter[str] = Counter()
    for task in tasks:
        status = task.get("status", "unknown")
        status_counts[status] += 1
        assignee = task.get("assignee", "unassigned") or "unassigned"
        assignee_counts[assignee] += 1

    total = len(tasks)
    done = status_counts.get("done", 0)
    in_flight = status_counts.get("running", 0) + status_counts.get("ready", 0)
    blocked = status_counts.get("blocked", 0)
    pct = (done / total * 100) if total > 0 else 0

    # ── Header ─────────────────────────────────────────────────────────
    lines: List[str] = []
    lines.append("")
    lines.append("  ╔══════════════════════════════════════════════════════╗")
    lines.append(f"  ║  🗂️  Kanban Board{' ' * (37 - len(str(total)))} {total} tasks ║")
    lines.append("  ╚══════════════════════════════════════════════════════╝")
    lines.append("")

    if assignee_filter:
        lines.append(f"  Filter: assignee={assignee_filter}")
    if tenant_filter:
        lines.append(f"  Tenant: {tenant_filter}")
    if assignee_filter or tenant_filter:
        lines.append("")

    # ── Progress bar ──────────────────────────────────────────────────
    lines.append(f"  Progress    {_bar(pct)}  {done}/{total} done  ({pct:.0f}%)")
    lines.append("")

    # ── Status breakdown ──────────────────────────────────────────────
    lines.append("  ▸ By status")
    for status in _STATUS_ORDER:
        count = status_counts.get(status, 0)
        if count == 0:
            continue
        icon = _STATUS_ICONS.get(status, "  ")
        label = status.ljust(8)
        mini_bar = _bar(count / total * 100 if total > 0 else 0, width=12) if total > 0 else ""
        lines.append(f"    {icon} {label} {count:>3}  {mini_bar}")

    lines.append("")

    # ── Assignee breakdown ─────────────────────────────────────────────
    if len(assignee_counts) > 1:
        lines.append("  ▸ By assignee")
        for assignee, count in assignee_counts.most_common():
            lines.append(f"    👤 {assignee:<22} {count:>3} tasks")
        lines.append("")

    # ── In-flight tasks ────────────────────────────────────────────────
    active = [t for t in tasks if t.get("status") in ("running", "ready")]
    if active:
        lines.append("  ▸ In flight")
        for task in active:
            title = _truncate(task.get("title", "untitled"), 50)
            status_icon = _STATUS_ICONS.get(task.get("status", ""), "  ")
            assignee = task.get("assignee", "?")
            age = _format_age(task.get("started_at"))
            lines.append(f"    {status_icon} [{assignee}] {title}")
            lines.append(f"       started {age}")
        lines.append("")

    # ── Blocked tasks ──────────────────────────────────────────────────
    stuck = [t for t in tasks if t.get("status") == "blocked"]
    if stuck:
        lines.append("  ▸ Blocked")
        for task in stuck:
            title = _truncate(task.get("title", "untitled"), 50)
            assignee = task.get("assignee", "?")
            age = _format_age(task.get("started_at"))
            lines.append(f"    ⊘ [{assignee}] {title}")
            lines.append(f"       blocked for {age}")
        lines.append("")

    # ── Recently completed ─────────────────────────────────────────────
    completed = [
        t for t in tasks
        if t.get("status") == "done" and t.get("completed_at")
    ]
    completed.sort(key=lambda t: t.get("completed_at", 0), reverse=True)
    if completed:
        lines.append(f"  ▸ Recently completed (last {min(5, len(completed))})")
        for task in completed[:5]:
            title = _truncate(task.get("title", "untitled"), 50)
            assignee = task.get("assignee", "?")
            age = _format_age(task.get("completed_at"))
            lines.append(f"    ✅ [{assignee}] {title}")
            lines.append(f"       completed {age}")
        lines.append("")

    # ── Footer ─────────────────────────────────────────────────────────
    lines.append("  ─────────────────────────────────────────────────────────")
    lines.append(f"  Run:  hermes-sidecar kanban --watch   (live refresh)")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Watch mode
# ---------------------------------------------------------------------------


def watch_loop(
    *,
    mine: bool = False,
    tenant: Optional[str] = None,
    interval: float = 5.0,
) -> None:
    """Clear screen and re-render the dashboard on an interval.

    Press Ctrl-C to exit.
    """
    import time as _time

    try:
        while True:
            # Clear screen
            sys.stdout.write("\033[2J\033[H")
            sys.stdout.flush()

            tasks = fetch_tasks(mine=mine, tenant=tenant)
            print(render_dashboard(tasks))

            sys.stdout.write(f"  Refreshing every {interval:.0f}s — Ctrl-C to exit\n")
            sys.stdout.flush()
            _time.sleep(interval)
    except KeyboardInterrupt:
        print("\n  👋 Done.")
        sys.exit(0)


# ---------------------------------------------------------------------------
# JSON export
# ---------------------------------------------------------------------------


def export_json(tasks: List[Dict[str, Any]], *, compact: bool = False) -> str:
    """Export task list as JSON string."""
    indent = None if compact else 2
    return json.dumps(tasks, indent=indent, sort_keys=True)
