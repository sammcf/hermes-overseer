"""Behavioural tests for overseer.backup.snapshot.

System invariants:
- take_snapshot: creates timestamped archive on VPS, downloads to local backup_dir
- restore_snapshot: uploads archive to VPS, extracts under hermes_home parent, fixes ownership
- find_latest_snapshot: returns most recent archive path or None
- prune_snapshots: keeps last N, deletes older ones
"""

from __future__ import annotations

from pathlib import Path

import pytest

from overseer.types import Err, Ok

# ---------------------------------------------------------------------------
# take_snapshot
# ---------------------------------------------------------------------------


class TestTakeSnapshot:
    def test_creates_archive_and_downloads_it(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """SSH creates tar.gz on VPS, rsync pulls it locally → Ok(local_path)."""
        import overseer.backup.snapshot as snap

        monkeypatch.setattr(
            snap,
            "run_ssh_command",
            lambda host, user, cmd, timeout=30: Ok(""),
        )
        downloaded: list[str] = []

        def mock_rsync_pull_file(host, user, remote_path, local_dir, timeout=120):
            downloaded.append(remote_path)
            # Simulate the file appearing locally
            archive_name = Path(remote_path).name
            (Path(local_dir) / archive_name).write_bytes(b"archive")
            return Ok(local_dir)

        monkeypatch.setattr(snap, "rsync_pull_file", mock_rsync_pull_file)

        backup_dir = str(tmp_path / "backups")
        result = snap.take_snapshot("hermes-vps", "hermes", "/home/hermes/.hermes", backup_dir)

        assert isinstance(result, Ok)
        assert result.value.startswith(backup_dir)
        assert result.value.endswith(".tar.gz")
        assert "hermes-state-" in result.value
        # Archive was pulled from VPS /tmp/
        assert len(downloaded) == 1
        assert downloaded[0].startswith("/tmp/hermes-state-")

    def test_archive_command_excludes_code_directories(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """tar command excludes hermes-agent, sandboxes, bin, image_cache, document_cache."""
        import overseer.backup.snapshot as snap

        captured_cmds: list[str] = []

        def mock_ssh(host, user, cmd, timeout=30):
            captured_cmds.append(cmd)
            return Ok("")

        monkeypatch.setattr(snap, "run_ssh_command", mock_ssh)
        monkeypatch.setattr(
            snap,
            "rsync_pull_file",
            lambda *a, **kw: Ok(str(tmp_path)),
        )

        snap.take_snapshot("hermes-vps", "hermes", "/home/hermes/.hermes", str(tmp_path))

        # First SSH call is the tar creation
        tar_cmd = next(c for c in captured_cmds if "tar czf" in c)
        assert "--exclude=" in tar_cmd
        assert "hermes-agent" in tar_cmd
        assert "sandboxes" in tar_cmd
        assert "bin" in tar_cmd
        assert "image_cache" in tar_cmd
        assert "document_cache" in tar_cmd

    def test_wal_checkpoint_runs_before_tar(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """sqlite3 WAL checkpoint is issued before the tar archive is created."""
        import overseer.backup.snapshot as snap

        cmd_order: list[str] = []

        def mock_ssh(host, user, cmd, timeout=30):
            cmd_order.append(cmd)
            return Ok("")

        monkeypatch.setattr(snap, "run_ssh_command", mock_ssh)
        monkeypatch.setattr(snap, "rsync_pull_file", lambda *a, **kw: Ok(str(tmp_path)))

        snap.take_snapshot("hermes-vps", "hermes", "/home/hermes/.hermes", str(tmp_path))

        checkpoint_idx = next(
            (i for i, c in enumerate(cmd_order) if "wal_checkpoint" in c), None
        )
        tar_idx = next((i for i, c in enumerate(cmd_order) if "tar czf" in c), None)

        assert checkpoint_idx is not None, (
            "WAL checkpoint command not found in SSH calls. "
            "Add sqlite3 PRAGMA wal_checkpoint(FULL) before the tar in take_snapshot()."
        )
        assert tar_idx is not None, "tar czf command not found"
        assert checkpoint_idx < tar_idx, (
            f"WAL checkpoint (idx={checkpoint_idx}) must run before tar (idx={tar_idx})"
        )
        # Verify it targets the right DB file
        assert "state.db" in cmd_order[checkpoint_idx], (
            "Checkpoint command should reference state.db"
        )

    def test_wal_checkpoint_failure_does_not_abort_snapshot(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A failed WAL checkpoint is best-effort: snapshot proceeds regardless."""
        import overseer.backup.snapshot as snap

        call_count = [0]

        def mock_ssh(host, user, cmd, timeout=30):
            call_count[0] += 1
            if "wal_checkpoint" in cmd:
                return Err("sqlite3: not found", source="ssh")
            return Ok("")

        monkeypatch.setattr(snap, "run_ssh_command", mock_ssh)
        monkeypatch.setattr(snap, "rsync_pull_file", lambda *a, **kw: Ok(str(tmp_path)))

        result = snap.take_snapshot("hermes-vps", "hermes", "/home/hermes/.hermes", str(tmp_path))

        # Snapshot should still succeed even if checkpoint failed
        assert isinstance(result, Ok), (
            "Snapshot should succeed even when WAL checkpoint fails (best-effort)"
        )

    def test_ssh_failure_during_archive_creation_returns_err(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """If SSH tar command fails, returns Err without attempting rsync."""
        import overseer.backup.snapshot as snap

        rsync_called = False

        def mock_rsync(*a, **kw):
            nonlocal rsync_called
            rsync_called = True
            return Ok(str(tmp_path))

        monkeypatch.setattr(
            snap, "run_ssh_command", lambda *a, **kw: Err("SSH failed", source="ssh")
        )
        monkeypatch.setattr(snap, "rsync_pull_file", mock_rsync)

        result = snap.take_snapshot("hermes-vps", "hermes", "/home/hermes/.hermes", str(tmp_path))

        assert isinstance(result, Err)
        assert not rsync_called

    def test_rsync_download_failure_returns_err(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """If rsync pull fails after successful tar, returns Err."""
        import overseer.backup.snapshot as snap

        ssh_calls: list[str] = []

        def mock_ssh(host, user, cmd, timeout=30):
            ssh_calls.append(cmd)
            return Ok("")

        monkeypatch.setattr(snap, "run_ssh_command", mock_ssh)
        monkeypatch.setattr(
            snap,
            "rsync_pull_file",
            lambda *a, **kw: Err("rsync: connection lost", source="ssh"),
        )

        result = snap.take_snapshot("hermes-vps", "hermes", "/home/hermes/.hermes", str(tmp_path))

        assert isinstance(result, Err)
        assert "rsync" in result.error.lower() or "connection" in result.error.lower()


# ---------------------------------------------------------------------------
# restore_snapshot
# ---------------------------------------------------------------------------


class TestRestoreSnapshot:
    def test_uploads_and_extracts_archive(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """rsync pushes archive to VPS, SSH extracts under hermes parent → Ok."""
        import overseer.backup.snapshot as snap

        archive = tmp_path / "hermes-state-20260315T120000Z.tar.gz"
        archive.write_bytes(b"archive")

        pushed_to: list[str] = []
        extracted_cmds: list[str] = []

        def mock_rsync_push(host, user, local_path, remote_dir, timeout=120):
            pushed_to.append(remote_dir)
            return Ok(remote_dir)

        def mock_ssh(host, user, cmd, timeout=30):
            extracted_cmds.append(cmd)
            return Ok("")

        monkeypatch.setattr(snap, "rsync_push", mock_rsync_push)
        monkeypatch.setattr(snap, "run_ssh_command", mock_ssh)

        result = snap.restore_snapshot(
            "hermes-vps", "hermes", str(archive), "/home/hermes/.hermes"
        )

        assert isinstance(result, Ok)
        assert len(pushed_to) >= 1
        assert "/tmp/" in pushed_to[0] or "/tmp" in pushed_to[0]
        # SSH extracted with correct parent
        extract_cmd = next(c for c in extracted_cmds if "tar xzf" in c)
        assert "-C /home/hermes" in extract_cmd
        assert "chown -R hermes:hermes" in extract_cmd

    def test_extraction_targets_parent_of_hermes_home(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """tar extracts to parent of hermes_home, not hermes_home itself."""
        import overseer.backup.snapshot as snap

        archive = tmp_path / "hermes-state-20260315T120000Z.tar.gz"
        archive.write_bytes(b"data")

        extract_cmds: list[str] = []

        monkeypatch.setattr(
            snap, "rsync_push", lambda *a, **kw: Ok("/tmp/")
        )

        def mock_ssh(host, user, cmd, timeout=30):
            if "tar xzf" in cmd:
                extract_cmds.append(cmd)
            return Ok("")

        monkeypatch.setattr(snap, "run_ssh_command", mock_ssh)

        snap.restore_snapshot("hermes-vps", "hermes", str(archive), "/custom/path/.hermes")

        assert len(extract_cmds) >= 1
        # Parent of /custom/path/.hermes is /custom/path
        assert "-C /custom/path" in extract_cmds[0]
        assert "chown -R hermes:hermes /custom/path/.hermes" in extract_cmds[0]

    def test_rsync_push_failure_returns_err(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """If rsync push fails, returns Err without attempting SSH."""
        import overseer.backup.snapshot as snap

        archive = tmp_path / "hermes-state-20260315T120000Z.tar.gz"
        archive.write_bytes(b"data")

        ssh_called = False

        def mock_ssh(*a, **kw):
            nonlocal ssh_called
            ssh_called = True
            return Ok("")

        monkeypatch.setattr(
            snap, "rsync_push", lambda *a, **kw: Err("rsync: No route to host", source="ssh")
        )
        monkeypatch.setattr(snap, "run_ssh_command", mock_ssh)

        result = snap.restore_snapshot(
            "hermes-vps", "hermes", str(archive), "/home/hermes/.hermes"
        )

        assert isinstance(result, Err)
        assert not ssh_called

    def test_ssh_extraction_failure_returns_err(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """If SSH extraction fails, returns Err."""
        import overseer.backup.snapshot as snap

        archive = tmp_path / "hermes-state-20260315T120000Z.tar.gz"
        archive.write_bytes(b"data")

        monkeypatch.setattr(
            snap, "rsync_push", lambda *a, **kw: Ok("/tmp/")
        )
        monkeypatch.setattr(
            snap,
            "run_ssh_command",
            lambda host, user, cmd, timeout=30: Err("tar: corrupt archive", source="ssh"),
        )

        result = snap.restore_snapshot(
            "hermes-vps", "hermes", str(archive), "/home/hermes/.hermes"
        )

        assert isinstance(result, Err)


# ---------------------------------------------------------------------------
# find_latest_snapshot
# ---------------------------------------------------------------------------


class TestFindLatestSnapshot:
    def test_empty_or_missing_dir_returns_none(self, tmp_path: Path) -> None:
        """Empty backup dir and non-existent dir both return None."""
        from overseer.backup.snapshot import find_latest_snapshot

        assert find_latest_snapshot(str(tmp_path / "missing")) is None
        assert find_latest_snapshot(str(tmp_path)) is None

    def test_single_snapshot_returns_its_path(self, tmp_path: Path) -> None:
        """With one archive, returns that archive's path."""
        from overseer.backup.snapshot import find_latest_snapshot

        archive = tmp_path / "hermes-state-20260315T120000Z.tar.gz"
        archive.write_bytes(b"data")

        result = find_latest_snapshot(str(tmp_path))
        assert result == str(archive)

    def test_multiple_snapshots_returns_most_recent(self, tmp_path: Path) -> None:
        """With multiple archives, returns the lexicographically last (most recent timestamp)."""
        from overseer.backup.snapshot import find_latest_snapshot

        for ts in ["20260314T000000Z", "20260315T120000Z", "20260313T060000Z"]:
            (tmp_path / f"hermes-state-{ts}.tar.gz").write_bytes(b"data")

        result = find_latest_snapshot(str(tmp_path))
        assert result is not None
        assert "20260315T120000Z" in result


# ---------------------------------------------------------------------------
# prune_snapshots
# ---------------------------------------------------------------------------


class TestPruneSnapshots:
    def test_empty_dir_returns_zero_deleted(self, tmp_path: Path) -> None:
        """Empty or missing backup dir deletes nothing."""
        from overseer.backup.snapshot import prune_snapshots

        assert prune_snapshots(str(tmp_path / "missing"), retention_count=24) == 0
        assert prune_snapshots(str(tmp_path), retention_count=24) == 0

    def test_fewer_than_retention_deletes_nothing(self, tmp_path: Path) -> None:
        """When snapshot count ≤ retention_count, all are kept."""
        from overseer.backup.snapshot import prune_snapshots

        for i in range(3):
            (tmp_path / f"hermes-state-2026031{i}T000000Z.tar.gz").write_bytes(b"data")

        deleted = prune_snapshots(str(tmp_path), retention_count=5)

        assert deleted == 0
        assert len(list(tmp_path.glob("*.tar.gz"))) == 3

    def test_excess_snapshots_oldest_are_deleted(self, tmp_path: Path) -> None:
        """When count exceeds retention, oldest (lexicographically earliest) are deleted."""
        from overseer.backup.snapshot import prune_snapshots

        timestamps = [
            "20260311T000000Z",  # oldest — should be deleted
            "20260312T000000Z",  # should be deleted
            "20260313T000000Z",  # kept
            "20260314T000000Z",  # kept
            "20260315T000000Z",  # kept (newest)
        ]
        for ts in timestamps:
            (tmp_path / f"hermes-state-{ts}.tar.gz").write_bytes(b"data")

        deleted = prune_snapshots(str(tmp_path), retention_count=3)

        assert deleted == 2
        remaining = sorted(tmp_path.glob("*.tar.gz"))
        assert len(remaining) == 3
        # Oldest two should be gone
        assert not (tmp_path / "hermes-state-20260311T000000Z.tar.gz").exists()
        assert not (tmp_path / "hermes-state-20260312T000000Z.tar.gz").exists()

    def test_returns_count_of_deleted(self, tmp_path: Path) -> None:
        """Return value accurately reflects number of files removed."""
        from overseer.backup.snapshot import prune_snapshots

        for i in range(6):
            (tmp_path / f"hermes-state-2026031{i}T000000Z.tar.gz").write_bytes(b"data")

        deleted = prune_snapshots(str(tmp_path), retention_count=2)

        assert deleted == 4
