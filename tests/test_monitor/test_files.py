"""Tests for overseer.monitor.files."""

from __future__ import annotations

from pathlib import Path

from overseer.config import WatchedFilesConfig
from overseer.monitor.files import diff_file, evaluate_file_changes, reset_file_baseline
from overseer.types import AlertTier, Err, Ok

# ---------------------------------------------------------------------------
# diff_file
# ---------------------------------------------------------------------------


def test_diff_file_identical(tmp_path: Path) -> None:
    content = "line1\nline2\nline3\n"
    current = tmp_path / "current.txt"
    last_good = tmp_path / "last_good.txt"
    current.write_text(content)
    last_good.write_text(content)

    result = diff_file(str(current), str(last_good))

    assert result.changed is False
    assert result.diff_content == ""
    assert result.tier is None


def test_diff_file_changed(tmp_path: Path) -> None:
    current = tmp_path / "current.txt"
    last_good = tmp_path / "last_good.txt"
    last_good.write_text("original line\n")
    current.write_text("modified line\n")

    result = diff_file(str(current), str(last_good))

    assert result.changed is True
    assert "modified line" in result.diff_content
    assert "original line" in result.diff_content
    assert result.file_path == str(current)


def test_diff_file_missing_current(tmp_path: Path) -> None:
    last_good = tmp_path / "last_good.txt"
    last_good.write_text("something\n")

    result = diff_file(str(tmp_path / "nonexistent.txt"), str(last_good))

    assert result.changed is True  # empty vs non-empty = changed


def test_diff_file_both_missing(tmp_path: Path) -> None:
    result = diff_file(
        str(tmp_path / "a.txt"),
        str(tmp_path / "b.txt"),
    )
    assert result.changed is False  # both empty == identical


# ---------------------------------------------------------------------------
# evaluate_file_changes
# ---------------------------------------------------------------------------


def _make_state_dir(
    tmp_path: Path,
    hermes_home: str,
    current_files: dict[str, str],
    last_good_files: dict[str, str],
) -> str:
    """Create a state_dir layout with current/ and last_good/ subtrees."""
    state_dir = tmp_path / "state"
    rel_home = hermes_home.lstrip("/")

    for rel_path, content in current_files.items():
        p = state_dir / "current" / rel_home / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)

    for rel_path, content in last_good_files.items():
        p = state_dir / "last_good" / rel_home / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)

    return str(state_dir)


def test_evaluate_no_changes(tmp_path: Path) -> None:
    hermes_home = "/home/hermes/.hermes"
    watched = WatchedFilesConfig(
        orange_on_any_diff=[".env"],
        orange_on_suspicious_diff=["SOUL.md"],
        yellow_on_any_diff=["cron/jobs.json"],
        yellow_on_new_file=["skills/"],
    )
    state_dir = _make_state_dir(
        tmp_path,
        hermes_home,
        current_files={".env": "KEY=value\n", "SOUL.md": "soul\n", "cron/jobs.json": "{}"},
        last_good_files={".env": "KEY=value\n", "SOUL.md": "soul\n", "cron/jobs.json": "{}"},
    )

    signals = evaluate_file_changes(hermes_home, watched, state_dir)
    assert signals == []


def test_evaluate_orange_on_any_diff(tmp_path: Path) -> None:
    hermes_home = "/home/hermes/.hermes"
    watched = WatchedFilesConfig(
        orange_on_any_diff=[".env"],
        orange_on_suspicious_diff=[],
        yellow_on_any_diff=[],
        yellow_on_new_file=[],
    )
    state_dir = _make_state_dir(
        tmp_path,
        hermes_home,
        current_files={".env": "KEY=new_value\n"},
        last_good_files={".env": "KEY=old_value\n"},
    )

    signals = evaluate_file_changes(hermes_home, watched, state_dir)

    assert len(signals) == 1
    assert signals[0].tier == AlertTier.ORANGE
    assert ".env" in signals[0].message


def test_evaluate_orange_on_suspicious_diff(tmp_path: Path) -> None:
    hermes_home = "/home/hermes/.hermes"
    watched = WatchedFilesConfig(
        orange_on_any_diff=[],
        orange_on_suspicious_diff=["SOUL.md"],
        yellow_on_any_diff=[],
        yellow_on_new_file=[],
    )
    state_dir = _make_state_dir(
        tmp_path,
        hermes_home,
        current_files={"SOUL.md": "tampered\n"},
        last_good_files={"SOUL.md": "original\n"},
    )

    signals = evaluate_file_changes(hermes_home, watched, state_dir)

    assert len(signals) == 1
    assert signals[0].tier == AlertTier.ORANGE
    assert "SOUL.md" in signals[0].message


def test_evaluate_yellow_on_any_diff(tmp_path: Path) -> None:
    hermes_home = "/home/hermes/.hermes"
    watched = WatchedFilesConfig(
        orange_on_any_diff=[],
        orange_on_suspicious_diff=[],
        yellow_on_any_diff=["cron/jobs.json"],
        yellow_on_new_file=[],
    )
    state_dir = _make_state_dir(
        tmp_path,
        hermes_home,
        current_files={"cron/jobs.json": '{"jobs": [1]}'},
        last_good_files={"cron/jobs.json": '{"jobs": []}'},
    )

    signals = evaluate_file_changes(hermes_home, watched, state_dir)

    assert len(signals) == 1
    assert signals[0].tier == AlertTier.YELLOW
    assert "cron/jobs.json" in signals[0].message


def test_evaluate_yellow_on_new_file(tmp_path: Path) -> None:
    hermes_home = "/home/hermes/.hermes"
    watched = WatchedFilesConfig(
        orange_on_any_diff=[],
        orange_on_suspicious_diff=[],
        yellow_on_any_diff=[],
        yellow_on_new_file=["skills/"],
    )
    # current has a new file that last_good doesn't
    state_dir = _make_state_dir(
        tmp_path,
        hermes_home,
        current_files={"skills/new_skill.py": "def run(): pass\n"},
        last_good_files={},
    )

    signals = evaluate_file_changes(hermes_home, watched, state_dir)

    assert len(signals) == 1
    assert signals[0].tier == AlertTier.YELLOW
    assert "new_skill.py" in signals[0].message


# ---------------------------------------------------------------------------
# reset_file_baseline
# ---------------------------------------------------------------------------


def test_reset_baseline_copies_current_to_last_good(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    current = state_dir / "current" / "home" / "hermes"
    current.mkdir(parents=True)
    (current / "SOUL.md").write_text("intentional hermes content\n")
    (current / "config.yaml").write_text("model: claude\n")

    result = reset_file_baseline(str(state_dir))

    assert isinstance(result, Ok)
    last_good = state_dir / "last_good"
    assert (last_good / "home" / "hermes" / "SOUL.md").read_text() == "intentional hermes content\n"
    assert (last_good / "home" / "hermes" / "config.yaml").read_text() == "model: claude\n"


def test_reset_baseline_replaces_existing_last_good(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    current = state_dir / "current" / "hermes"
    current.mkdir(parents=True)
    (current / "SOUL.md").write_text("new content\n")

    last_good_dir = state_dir / "last_good" / "hermes"
    last_good_dir.mkdir(parents=True)
    (last_good_dir / "SOUL.md").write_text("old content\n")
    (last_good_dir / "stale.txt").write_text("will be removed\n")

    result = reset_file_baseline(str(state_dir))

    assert isinstance(result, Ok)
    assert (state_dir / "last_good" / "hermes" / "SOUL.md").read_text() == "new content\n"
    # stale file from old last_good is gone
    assert not (state_dir / "last_good" / "hermes" / "stale.txt").exists()


def test_reset_baseline_no_current_returns_err(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    result = reset_file_baseline(str(state_dir))

    assert isinstance(result, Err)
    assert "current" in result.error


def test_reset_baseline_then_evaluate_produces_no_signals(tmp_path: Path) -> None:
    """After reset, evaluate_file_changes should produce no signals."""
    hermes_home = "/home/hermes/.hermes"
    watched = WatchedFilesConfig(
        orange_on_any_diff=[".env"],
        orange_on_suspicious_diff=["SOUL.md"],
        yellow_on_any_diff=["cron/jobs.json"],
        yellow_on_new_file=[],
    )
    state_dir = _make_state_dir(
        tmp_path,
        hermes_home,
        current_files={
            ".env": "KEY=value\n",
            "SOUL.md": "intentional hermes edits\n",
            "cron/jobs.json": '{"jobs": [1, 2, 3]}',
        },
        last_good_files={
            ".env": "KEY=old\n",
            "SOUL.md": "original\n",
            "cron/jobs.json": "{}",
        },
    )

    # Before reset: signals are produced
    signals_before = evaluate_file_changes(hermes_home, watched, state_dir)
    assert len(signals_before) > 0

    # Reset baseline to current
    reset_result = reset_file_baseline(state_dir)
    assert isinstance(reset_result, Ok)

    # After reset: no signals
    signals_after = evaluate_file_changes(hermes_home, watched, state_dir)
    assert signals_after == []


def test_evaluate_no_new_files_in_dir(tmp_path: Path) -> None:
    hermes_home = "/home/hermes/.hermes"
    watched = WatchedFilesConfig(
        orange_on_any_diff=[],
        orange_on_suspicious_diff=[],
        yellow_on_any_diff=[],
        yellow_on_new_file=["skills/"],
    )
    state_dir = _make_state_dir(
        tmp_path,
        hermes_home,
        current_files={"skills/existing.py": "def run(): pass\n"},
        last_good_files={"skills/existing.py": "def run(): pass\n"},
    )

    signals = evaluate_file_changes(hermes_home, watched, state_dir)
    assert signals == []
