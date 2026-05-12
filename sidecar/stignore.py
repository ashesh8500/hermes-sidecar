"""
.stignore generator for hermes-sidecar.

Provides pattern templates for common project types (Python, ML, LaTeX) and an
auto-detection function that picks the right template set for a given directory.
"""

from pathlib import Path
from typing import Dict, List, Set

# ---------------------------------------------------------------------------
# Pattern sets
# ---------------------------------------------------------------------------

COMMON_PATTERNS: List[str] = [
    "# ── hermes-sidecar auto-generated .stignore ──",
    "",
    "# OS & editor",
    ".DS_Store",
    "Thumbs.db",
    "*.swp",
    "*.swo",
    "*~",
    ".idea/",
    ".vscode/",
    "*.sublime-workspace",
    "*.sublime-project",
    "",
    "# Version control",
    ".git/",
    ".hg/",
    ".svn/",
    "",
    "# Dependencies & caches",
    "__pycache__/",
    "*.pyc",
    "*.pyo",
    ".mypy_cache/",
    ".pytest_cache/",
    ".ruff_cache/",
    ".tox/",
    ".eggs/",
    "*.egg-info/",
    ".venv/",
    "venv/",
    "env/",
    "",
    "# Node (present in many projects)",
    "node_modules/",
    ".npm/",
    "",
    "# Build artifacts",
    "dist/",
    "build/",
    "*.o",
    "*.so",
    "*.dylib",
    "*.dll",
    "",
    "# Logs & temp",
    "*.log",
    "*.tmp",
    "*.temp",
    ".cache/",
    "",
    "# Secrets & env",
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "credentials*",
]

PYTHON_EXTRAS: List[str] = [
    "",
    "# ── Python extras ──",
    "*.pyc",
    "*.pyo",
    ".coverage",
    "htmlcov/",
    "coverage.xml",
    ".hypothesis/",
    "pip-wheel-metadata/",
]

ML_EXTRAS: List[str] = [
    "",
    "# ── ML extras ──",
    "*.pt",
    "*.pth",
    "*.ckpt",
    "*.bin",
    "*.safetensors",
    "*.onnx",
    "*.tflite",
    "*.h5",
    "*.pb",
    "runs/",
    "wandb/",
    "mlruns/",
    "checkpoints/",
    "models/",
    "outputs/",
    ".cache/huggingface/",
    ".cache/torch/",
    "*.onnx_data",
    "events.out.tfevents.*",
]

LATEX_EXTRAS: List[str] = [
    "",
    "# ── LaTeX extras ──",
    "*.aux",
    "*.log",
    "*.out",
    "*.toc",
    "*.lof",
    "*.lot",
    "*.bbl",
    "*.blg",
    "*.blg",
    "*.brf",
    "*.fdb_latexmk",
    "*.fls",
    "*.idx",
    "*.ilg",
    "*.ind",
    "*.nav",
    "*.snm",
    "*.vrb",
    "*.synctex.gz",
    "*.synctex(busy)",
    "_minted-*/",
]

# ---------------------------------------------------------------------------
# Template registry
# ---------------------------------------------------------------------------

TEMPLATES: Dict[str, List[str]] = {
    "generic": COMMON_PATTERNS,
    "python": COMMON_PATTERNS + PYTHON_EXTRAS,
    "ml": COMMON_PATTERNS + PYTHON_EXTRAS + ML_EXTRAS,
    "latex": COMMON_PATTERNS + LATEX_EXTRAS,
}

# ---------------------------------------------------------------------------
# Project type detection
# ---------------------------------------------------------------------------

# Files whose presence indicates a project type.
# Detection order matters: ML > Python > LaTeX (ML projects contain Python files
# but not vice versa — check for ML indicators first).
_TYPE_MARKERS: Dict[str, List[str]] = {
    "ml": [
        "*.pt",
        "*.pth",
        "*.ckpt",
        "*.safetensors",
        "*.onnx",
        "models",
        "checkpoints",
        "wandb",
        "mlruns",
    ],
    "latex": [
        "*.tex",
        "*.bib",
        "*.cls",
        "*.sty",
    ],
    "python": [
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "requirements.txt",
        "Pipfile",
        "*.py",
    ],
}


def detect_project_type(path: str = ".") -> str:
    """Auto-detect the project type from files present in *path*.

    Detection order: ML → LaTeX → Python → generic.
    ML projects contain Python files too, so check for ML markers first.
    LaTeX is checked before Python because ``*.tex`` is more specific.

    Args:
        path: Directory to scan.  Defaults to the current working directory.

    Returns:
        One of ``"ml"``, ``"latex"``, ``"python"``, or ``"generic"``.
    """
    project_dir = Path(path).expanduser().resolve()
    if not project_dir.is_dir():
        return "generic"

    for ptype, markers in _TYPE_MARKERS.items():
        if _has_any_marker(project_dir, markers):
            return ptype

    return "generic"


def _has_any_marker(directory: Path, markers: List[str]) -> bool:
    """Return True if *directory* contains any file or dir matching *markers*.

    Glob patterns (``*.py``) are matched via ``Path.glob``; plain names are
    checked with ``(directory / name).exists()``.
    """
    for marker in markers:
        if "*" in marker or "?" in marker or "[" in marker:
            # Glob pattern — check for at least one match.
            try:
                if next(directory.glob(marker), None) is not None:
                    return True
            except StopIteration:
                pass
        else:
            if (directory / marker).exists():
                return True
    return False


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------


def generate_stignore(path: str = ".") -> str:
    """Generate a ``.stignore`` file for the project at *path*.

    Auto-detects the project type from files present and selects the
    appropriate template (Python, ML, LaTeX, or generic).  Returns the
    complete content as a string — the caller is responsible for writing
    it to ``.stignore``.

    Args:
        path: Directory to scan.  Defaults to the current working directory.

    Returns:
        The generated ``.stignore`` content, one pattern per line, ready to
        be written to disk.
    """
    project_type = detect_project_type(path)
    patterns = TEMPLATES.get(project_type, COMMON_PATTERNS)
    return "\n".join(patterns) + "\n"
