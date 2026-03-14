"""File change detection via rsync pull + unified diff."""

from __future__ import annotations

import difflib
import os
from pathlib import Path

from overseer.config import WatchedFilesConfig
from overseer.ssh import rsync_pull
from overseer.types import AlertTier, DiffResult, Result, Signal


def pull_watched_files(
    hostname: str,
    user: str,
    hermes_home: str,
    watched_files: WatchedFilesConfig,
    state_dir: str,
) -> Result[str]:
    """Fetch all watched files/dirs from the remote host into state_dir via rsync."""
    all_paths = [
        *watched_files.orange_on_any_diff,
        *watched_files.orange_on_suspicious_diff,
        *watched_files.yellow_on_any_diff,
        *watched_files.yellow_on_new_file,
    ]
    # Resolve each path relative to hermes_home so rsync pulls from the right location.
    remote_paths = [
        os.path.join(hermes_home, p.rstrip("/")) for p in all_paths
    ]
    return rsync_pull(hostname, user, remote_paths, state_dir)


def diff_file(current_path: str, last_good_path: str) -> DiffResult:
    """Produce a unified diff between a current file and its last-known-good copy.

    Both paths must already exist on the local filesystem.
    Returns a DiffResult with changed=False and empty diff_content if files are identical.
    """
    try:
        current_text = Path(current_path).read_text(errors="replace")
    except FileNotFoundError:
        current_text = ""

    try:
        last_good_text = Path(last_good_path).read_text(errors="replace")
    except FileNotFoundError:
        last_good_text = ""

    if current_text == last_good_text:
        return DiffResult(
            file_path=current_path,
            changed=False,
            diff_content="",
            tier=None,
        )

    diff_lines = list(
        difflib.unified_diff(
            last_good_text.splitlines(keepends=True),
            current_text.splitlines(keepends=True),
            fromfile=f"last_good:{last_good_path}",
            tofile=f"current:{current_path}",
        )
    )
    return DiffResult(
        file_path=current_path,
        changed=True,
        diff_content="".join(diff_lines),
        tier=None,  # Caller assigns tier based on category.
    )


def _signal_for_changed_file(
    filename: str,
    tier: AlertTier,
    diff_content: str,
    source: str = "files",
) -> Signal:
    return Signal.now(
        source=source,
        tier=tier,
        message=f"Change detected in watched file '{filename}': {diff_content[:200]}",
    )


def evaluate_file_changes(
    hermes_home: str,
    watched_files: WatchedFilesConfig,
    state_dir: str,
) -> list[Signal]:
    """Compare current pulled files against last-known-good copies, emit Signals.

    state_dir layout (maintained by caller):
      <state_dir>/current/<hermes_home>/...   — freshly pulled files
      <state_dir>/last_good/<hermes_home>/... — previous baseline
    """
    current_root = Path(state_dir) / "current"
    last_good_root = Path(state_dir) / "last_good"

    signals: list[Signal] = []

    def _current(rel: str) -> str:
        return str(current_root / hermes_home.lstrip("/") / rel)

    def _last_good(rel: str) -> str:
        return str(last_good_root / hermes_home.lstrip("/") / rel)

    # orange_on_any_diff — any change is orange
    for rel_path in watched_files.orange_on_any_diff:
        result = diff_file(_current(rel_path), _last_good(rel_path))
        if result.changed:
            signals.append(
                _signal_for_changed_file(rel_path, AlertTier.ORANGE, result.diff_content)
            )

    # orange_on_suspicious_diff — any change is orange (suspicious heuristic deferred)
    for rel_path in watched_files.orange_on_suspicious_diff:
        result = diff_file(_current(rel_path), _last_good(rel_path))
        if result.changed:
            signals.append(
                _signal_for_changed_file(rel_path, AlertTier.ORANGE, result.diff_content)
            )

    # yellow_on_any_diff — any change is yellow
    for rel_path in watched_files.yellow_on_any_diff:
        result = diff_file(_current(rel_path), _last_good(rel_path))
        if result.changed:
            signals.append(
                _signal_for_changed_file(rel_path, AlertTier.YELLOW, result.diff_content)
            )

    # yellow_on_new_file — new files appearing in a directory trigger yellow
    for rel_dir in watched_files.yellow_on_new_file:
        current_dir = Path(_current(rel_dir))
        last_good_dir = Path(_last_good(rel_dir))

        current_files: set[str] = set()
        if current_dir.exists():
            current_files = {
                str(p.relative_to(current_dir))
                for p in current_dir.rglob("*")
                if p.is_file()
            }

        last_good_files: set[str] = set()
        if last_good_dir.exists():
            last_good_files = {
                str(p.relative_to(last_good_dir))
                for p in last_good_dir.rglob("*")
                if p.is_file()
            }

        new_files = current_files - last_good_files
        for new_file in sorted(new_files):
            signals.append(
                Signal.now(
                    source="files",
                    tier=AlertTier.YELLOW,
                    message=f"New file detected in watched directory '{rel_dir}': {new_file}",
                )
            )

    return signals
