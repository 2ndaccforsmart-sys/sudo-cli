"""Path boundary sentinel — the fortress wall for sync operations.

Enforces strict, hardcoded security boundaries. Every path entering
the sync system MUST pass through validate_path() first.

BOUNDARY RULES:
  1. Only paths on E: drive or under D:/Daksh are permitted.
  2. D:/Daksh/Coding — FORBIDDEN (all subfolders)
  3. D:/Daksh/Software — FORBIDDEN (all subfolders)
  4. D:/Daksh/Studying — FORBIDDEN (all subfolders)
  5. Any path containing 'jarvis' or 'control center' — FORBIDDEN

If any command attempts to access a forbidden path, the sentinel
immediately halts with a clear security warning.
"""

from __future__ import annotations

import os
import re
from pathlib import Path, PureWindowsPath
from typing import Optional


class PathBoundaryViolation(Exception):
    """Raised when a path violates security boundaries."""
    pass


class PathBlacklistViolation(Exception):
    """Raised when a path hits a blacklisted directory."""
    pass


# ── Hardcoded Boundaries ────────────────────────────────────────────────────

# Config file path
SENTINEL_CONFIG_FILE = Path.home() / ".config" / "sudo" / "sync" / "sentinel.json"

# Default boundaries (used if config file doesn't exist)
DEFAULT_ALLOWED_ROOTS: list[str] = [
    "E:\\",
    "D:\\Daksh",
]

DEFAULT_BLACKLISTED_DIRS: list[str] = [
    "D:\\Daksh\\Coding",
    "D:\\Daksh\\Software",
    "D:\\Daksh\\Studying",
]

DEFAULT_BLACKLISTED_KEYWORDS: list[str] = [
    "jarvis",
    "control center",
]


def _load_sentinel_config() -> dict:
    """Load sentinel config from file, return defaults if not found."""
    if SENTINEL_CONFIG_FILE.exists():
        try:
            import json
            return json.loads(SENTINEL_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


# Load config at module import
_config = _load_sentinel_config()

# These are the active boundaries (configurable via sentinel.json)
ALLOWED_ROOTS: list[str] = _config.get("allowed_roots", DEFAULT_ALLOWED_ROOTS)
BLACKLISTED_DIRS: list[str] = _config.get("blacklisted_dirs", DEFAULT_BLACKLISTED_DIRS)
BLACKLISTED_KEYWORDS: list[str] = _config.get("blacklisted_keywords", DEFAULT_BLACKLISTED_KEYWORDS)

# Default excluded directories for all sync operations (cannot be overridden)
HARDCODED_EXCLUSIONS: set[str] = {
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    ".env", "dist", "build", ".tox", ".eggs", "target",
    ".DS_Store", "Thumbs.db", "*.pyc", "*.pyo", "*.swp",
    "*.swo", ".egg-info", ".next", ".cache", "pnpm-lock.yaml",
}


# ── Core Validation ─────────────────────────────────────────────────────────

def _normalize_path(path: str) -> Path:
    """Normalize a path to absolute, resolving .. and separators."""
    p = Path(path)
    if not p.is_absolute():
        p = Path.cwd() / p
    return p.resolve()


def _to_windows_str(path: Path) -> str:
    """Convert a Path to uppercase Windows string for comparison."""
    s = str(path)
    # Normalize to backslashes for Windows comparison
    s = s.replace("/", "\\")
    # Handle drive letter case: "e:" -> "E:"
    if len(s) >= 2 and s[1] == ":":
        s = s[0].upper() + s[1:]
    return s


def _is_within_root(path_str: str, root: str) -> bool:
    """Check if a normalized path string starts with a root."""
    return path_str.upper().startswith(root.upper())


def _contains_blacklisted_dir(path_str: str) -> Optional[str]:
    """Check if path contains any blacklisted directory. Returns the match or None."""
    path_upper = path_str.upper()
    for blacklisted in BLACKLISTED_DIRS:
        bl_upper = blacklisted.upper().replace("/", "\\")
        if path_upper.startswith(bl_upper + "\\") or path_upper == bl_upper:
            return blacklisted
    return None


def _contains_blacklisted_keyword(path_str: str) -> Optional[str]:
    """Check if path contains any blacklisted keyword. Returns the keyword or None."""
    path_lower = path_str.lower()
    for keyword in BLACKLISTED_KEYWORDS:
        if keyword.lower() in path_lower:
            return keyword
    return None


def validate_path(path: str) -> Path:
    """Validate a path against all security boundaries.

    This is the PRIMARY entry point. Every sync operation must call
    this function before accessing any file.

    Args:
        path: The path to validate (string).

    Returns:
        Resolved Path object if validation passes.

    Raises:
        PathBoundaryViolation: If path is outside allowed drives.
        PathBlacklistViolation: If path hits a blacklisted directory or keyword.
    """
    resolved = _normalize_path(path)
    win_str = _to_windows_str(resolved)

    # Check 1: Must be within allowed roots
    within_allowed = False
    for root in ALLOWED_ROOTS:
        root_normalized = root.replace("/", "\\")
        if _is_within_root(win_str, root_normalized):
            within_allowed = True
            break

    if not within_allowed:
        raise PathBoundaryViolation(
            f"BOUNDARY VIOLATION: Path is outside allowed zones.\n"
            f"  Attempted path: {resolved}\n"
            f"  Allowed zones:  {', '.join(ALLOWED_ROOTS)}\n"
            f"  This path has been BLOCKED by the security sentinel."
        )

    # Check 2: Must not be within blacklisted directories
    blacklisted_dir = _contains_blacklisted_dir(win_str)
    if blacklisted_dir:
        raise PathBlacklistViolation(
            f"BLACKLIST VIOLATION: Path is inside a forbidden directory.\n"
            f"  Attempted path: {resolved}\n"
            f"  Blacklisted:    {blacklisted_dir}\n"
            f"  This directory is BLOCKED by the security sentinel."
        )

    # Check 3: Must not contain blacklisted keywords
    keyword = _contains_blacklisted_keyword(win_str)
    if keyword:
        raise PathBlacklistViolation(
            f"KEYWORD VIOLATION: Path contains forbidden keyword '{keyword}'.\n"
            f"  Attempted path: {resolved}\n"
            f"  This path is BLOCKED by the security sentinel."
        )

    return resolved


def is_within_boundaries(path: str) -> bool:
    """Check if a path is within allowed boundaries (non-raising).

    Returns True if the path is safe, False otherwise.
    """
    try:
        validate_path(path)
        return True
    except (PathBoundaryViolation, PathBlacklistViolation):
        return False


def get_cloud_prefix(local_path: str) -> str:
    """Map a local path to a GCS namespace prefix.

    This ensures files from different drives never overlap in the bucket.

    Examples:
        E:\\Projects\\MyApp  -> E/Projects/MyApp
        D:\\Daksh\\Business  -> D/Daksh/Business
    """
    resolved = _normalize_path(local_path)
    win_str = _to_windows_str(resolved)
    # Convert backslashes to forward slashes for GCS
    prefix = win_str.replace("\\", "/")
    # Remove trailing slash
    return prefix.rstrip("/")


def scan_safe_directory(path: str, max_depth: int = 3) -> list[dict]:
    """Scan a directory for subdirectories, respecting all boundaries.

    Returns a list of dicts with path, name, file_count, size info.
    Only returns directories that pass the sentinel checks.
    """
    results = []
    resolved = _normalize_path(path)

    if not resolved.is_dir():
        return results

    def _scan(current: Path, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            for item in sorted(current.iterdir()):
                if not item.is_dir():
                    continue
                # Skip hidden directories
                if item.name.startswith("."):
                    continue
                # Skip hardcoded exclusions
                if item.name in HARDCODED_EXCLUSIONS:
                    continue
                # Run sentinel check
                try:
                    validate_path(str(item))
                except (PathBoundaryViolation, PathBlacklistViolation):
                    continue

                # Count files (quick, non-recursive)
                file_count = 0
                total_size = 0
                try:
                    for f in item.rglob("*"):
                        if f.is_file():
                            file_count += 1
                            try:
                                total_size += f.stat().st_size
                            except OSError:
                                pass
                except OSError:
                    pass

                results.append({
                    "path": str(item),
                    "name": item.name,
                    "depth": depth,
                    "file_count": file_count,
                    "total_size": total_size,
                })

                # Recurse into subdirectories
                _scan(item, depth + 1)
        except PermissionError:
            pass
        except OSError:
            pass

    _scan(resolved, 0)
    return results
