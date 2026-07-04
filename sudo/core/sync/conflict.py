"""Conflict resolution for two-way sync.

When a file changes both locally and in the cloud since the last sync,
this module handles the resolution — interactively or by policy.
"""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Optional


class ConflictResolver:
    """Resolves sync conflicts between local and cloud file versions."""

    def __init__(
        self,
        force_local: bool = False,
        force_cloud: bool = False,
        newest_wins: bool = False,
    ):
        self.force_local = force_local
        self.force_cloud = force_cloud
        self.newest_wins = newest_wins
        self.resolved_count = 0
        self.skipped_count = 0
        self.aborted = False

    def resolve(
        self,
        local_path: Path,
        cloud_path: str,
        local_hash: str,
        cloud_hash: str,
        local_mtime: float,
        cloud_mtime: Optional[float] = None,
    ) -> str:
        """Resolve a conflict between local and cloud versions.

        Returns:
            'local' — keep local version (upload)
            'cloud' — keep cloud version (download)
            'skip'  — skip this file
            'abort' — cancel entire sync
        """
        if self.force_local:
            self.resolved_count += 1
            return "local"

        if self.force_cloud:
            self.resolved_count += 1
            return "cloud"

        if self.newest_wins:
            if cloud_mtime and local_mtime:
                if local_mtime > cloud_mtime:
                    self.resolved_count += 1
                    return "local"
                else:
                    self.resolved_count += 1
                    return "cloud"
            # If no cloud mtime, prefer local
            self.resolved_count += 1
            return "local"

        # Interactive resolution
        return self._interactive_resolve(
            local_path, cloud_path, local_mtime, cloud_mtime
        )

    def _interactive_resolve(
        self,
        local_path: Path,
        cloud_path: str,
        local_mtime: float,
        cloud_mtime: Optional[float],
    ) -> str:
        """Prompt user for conflict resolution."""
        from datetime import datetime

        local_time_str = datetime.fromtimestamp(local_mtime).strftime("%Y-%m-%d %H:%M")
        cloud_time_str = (
            datetime.fromtimestamp(cloud_mtime).strftime("%Y-%m-%d %H:%M")
            if cloud_mtime
            else "unknown"
        )

        print(f"\n\033[33m  ⚠ Conflict: {local_path.name}\033[0m")
        print(f"  Local:  modified {local_time_str}")
        print(f"  Cloud:  modified {cloud_time_str}")
        print()
        print("  Options:")
        print("    1. Keep local version (upload to cloud)")
        print("    2. Keep cloud version (download locally)")
        print("    3. View diff")
        print("    4. Skip this file")
        print("    5. Abort sync")
        print()

        while True:
            try:
                choice = input("  Choose (1-5): ").strip()
            except (KeyboardInterrupt, EOFError):
                print()
                self.aborted = True
                return "abort"

            if choice == "1":
                self.resolved_count += 1
                return "local"
            elif choice == "2":
                self.resolved_count += 1
                return "cloud"
            elif choice == "3":
                self._show_diff(local_path, cloud_path)
            elif choice == "4":
                self.skipped_count += 1
                return "skip"
            elif choice == "5":
                self.aborted = True
                return "abort"
            else:
                print("  Invalid choice. Enter 1-5.")

    def _show_diff(self, local_path: Path, cloud_content: Optional[str] = None) -> None:
        """Show a brief diff between local and cloud versions."""
        try:
            local_content = local_path.read_text(encoding="utf-8", errors="replace")
        except (OSError, PermissionError):
            print("  [Could not read local file]")
            return

        if cloud_content is None:
            print("  [Cloud content not available for diff preview]")
            return

        local_lines = local_content.splitlines(keepends=True)
        cloud_lines = cloud_content.splitlines(keepends=True)

        diff = list(difflib.unified_diff(
            cloud_lines, local_lines,
            fromfile=f"cloud/{local_path.name}",
            tofile=f"local/{local_path.name}",
            n=3,
        ))

        if not diff:
            print("  [No differences found]")
            return

        # Show first 40 lines of diff
        for line in diff[:40]:
            if line.startswith("+"):
                print(f"  \033[32m{line.rstrip()}\033[0m")
            elif line.startswith("-"):
                print(f"  \033[31m{line.rstrip()}\033[0m")
            else:
                print(f"  {line.rstrip()}")

        if len(diff) > 40:
            print(f"  ... ({len(diff) - 40} more lines)")
        print()
