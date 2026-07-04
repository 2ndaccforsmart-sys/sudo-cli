"""'sudo status' command — local project summary (no LLM needed)."""

from __future__ import annotations

import os
import subprocess
from collections import Counter
from datetime import datetime
from pathlib import Path

from sudo.core.session import SessionManager
from sudo.utils.output import page, terminal_width
from sudo.utils.constants import SOURCE_EXTS, IGNORED_DIRS


def register(subparsers) -> None:
    p = subparsers.add_parser("status", help="Show project summary")
    p.set_defaults(func=lambda args: run_status(args))


def _git_info(cwd):
    info = {}
    try:
        r = subprocess.run(["git", "branch", "--show-current"], capture_output=True, text=True, timeout=3, cwd=cwd)
        if r.returncode == 0 and r.stdout.strip():
            info["branch"] = r.stdout.strip()
        r = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, timeout=3, cwd=cwd)
        if r.returncode == 0:
            dirty = [l for l in r.stdout.splitlines() if l.strip()]
            if dirty:
                info["dirty"] = True
                info["dirty_count"] = len(dirty)
        r = subprocess.run(["git", "rev-list", "--left-right", "--count", "HEAD...@{upstream}"],
                           capture_output=True, text=True, timeout=3, cwd=cwd)
        if r.returncode == 0 and r.stdout.strip():
            parts = r.stdout.strip().split()
            if len(parts) == 2:
                info["ahead"] = int(parts[0])
                info["behind"] = int(parts[1])
        r = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True, timeout=3, cwd=cwd)
        if r.returncode == 0:
            info["remote"] = r.stdout.strip()
    except Exception:
        pass
    return info


def _file_stats(cwd):
    stats = {}
    total_size = 0
    file_count = 0
    ext_counts = Counter()
    last_modified = []

    source_count = 0
    lang_ext_map = {
        "Python": {".py"}, "JavaScript/TS": {".js", ".ts", ".jsx", ".tsx"},
        "Go": {".go"}, "Rust": {".rs"}, "Java/Kotlin": {".java", ".kt"},
        "C/C++": {".c", ".cpp", ".h", ".hpp"}, "Ruby": {".rb"},
        "Shell": {".sh", ".bash", ".zsh"}, "Web": {".html", ".css", ".scss", ".sass", ".less"},
        "Config": {".yaml", ".yml", ".toml", ".json", ".xml"},
        "Docs": {".md", ".rst"},
    }

    for root, dirs, files in os.walk(cwd):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in IGNORED_DIRS]
        for f in files:
            if f.startswith("."):
                continue
            fpath = os.path.join(root, f)
            try:
                fsize = os.path.getsize(fpath)
            except OSError:
                continue
            total_size += fsize
            file_count += 1
            ext = os.path.splitext(f)[1].lower()
            if ext:
                ext_counts[ext] += 1
            if ext in SOURCE_EXTS:
                source_count += 1
            try:
                last_modified.append((fpath, datetime.fromtimestamp(os.path.getmtime(fpath))))
            except OSError:
                pass

    last_modified.sort(key=lambda x: x[1], reverse=True)
    lang_counts = {}
    for lang, exts in lang_ext_map.items():
        c = sum(ext_counts[e] for e in exts if e in ext_counts)
        if c:
            lang_counts[lang] = c

    stats["file_count"] = file_count
    stats["source_count"] = source_count
    stats["total_size"] = _format_size(total_size)
    stats["languages"] = lang_counts
    stats["recent_files"] = [p for p, _ in last_modified[:5]]
    return stats


def _format_size(size):
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


def _project_name(cwd):
    try:
        r = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True, timeout=3, cwd=cwd)
        if r.returncode == 0 and r.stdout.strip():
            url = r.stdout.strip()
            name = url.rstrip("/").split("/")[-1].replace(".git", "")
            if name:
                return name
    except Exception:
        pass
    return os.path.basename(os.path.abspath(cwd))


def run_status(args) -> None:
    cwd = os.getcwd()
    lines = []
    tw = terminal_width()

    name = _project_name(cwd)
    lines.append(f"  Project: {name}")

    git = _git_info(cwd)
    if git.get("branch"):
        branch = git["branch"]
        if git.get("dirty"):
            branch += f" [+{git['dirty_count']} dirty]"
        if git.get("ahead") or git.get("behind"):
            parts = []
            if git.get("ahead"):
                parts.append(f"+{git['ahead']}")
            if git.get("behind"):
                parts.append(f"-{git['behind']}")
            if parts:
                branch += f" ({','.join(parts)})"
        lines.append(f"  Branch:  {branch}")
    else:
        lines.append("  (no git repo)")

    stats = _file_stats(cwd)
    lines.append(f"  Files:   {stats['file_count']} total, {stats['source_count']} source, {stats['total_size']}")

    lang = stats.get("languages", {})
    if lang:
        sorted_langs = sorted(lang.items(), key=lambda x: -x[1])
        parts = [f"{ln}={cnt}" for ln, cnt in sorted_langs[:6]]
        lines.append(f"  Langs:   {', '.join(parts)}")

    recent = stats.get("recent_files", [])
    if recent:
        lines.append("  Recent:")
        base = os.path.abspath(cwd)
        for fp in recent[:5]:
            try:
                rel = os.path.relpath(fp, base)
            except ValueError:
                rel = fp
            truncated = rel[:tw - 8] if len(rel) > tw - 8 else rel
            lines.append(f"    \u2022 {truncated}")

    try:
        sm = SessionManager()
        session = sm.load()
        if session.get("plan"):
            plan = session["plan"]
            lines.append(f"  Plan:    {plan[:tw-12]}{'...' if len(plan) > tw-12 else ''}")
    except Exception:
        pass

    print("\n".join(lines))
