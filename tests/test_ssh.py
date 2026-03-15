"""Tests for overseer.ssh: push_file_content, wait_for_ssh, rsync_push, rsync_pull_file."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from overseer.types import Err, Ok

# ---------------------------------------------------------------------------
# push_file_content
# ---------------------------------------------------------------------------


class TestPushFileContent:
    def test_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Successful push returns Ok(remote_path)."""
        from overseer import ssh

        mock_run = MagicMock()
        mock_run.return_value.returncode = 0
        monkeypatch.setattr(ssh, "subprocess", MagicMock(run=mock_run, TimeoutExpired=TimeoutError))

        result = ssh.push_file_content("host", "user", "data\n", "/remote/path", mode="0644")

        assert isinstance(result, Ok)
        assert result.value == "/remote/path"
        # Verify content was piped as bytes
        call_kwargs = mock_run.call_args
        assert call_kwargs.kwargs["input"] == b"data\n"

    def test_failure_returns_err(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-zero exit code returns Err."""
        from overseer import ssh

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = b"Permission denied"
        mock_run = MagicMock(return_value=mock_result)
        monkeypatch.setattr(ssh, "subprocess", MagicMock(run=mock_run, TimeoutExpired=TimeoutError))

        result = ssh.push_file_content("host", "user", "data", "/remote/path")

        assert isinstance(result, Err)
        assert "Permission denied" in result.error

    def test_timeout_returns_err(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Timeout returns Err."""
        import subprocess as real_subprocess

        from overseer import ssh

        def mock_run(*args, **kwargs):
            raise real_subprocess.TimeoutExpired(cmd=args[0], timeout=30)

        monkeypatch.setattr(ssh.subprocess, "run", mock_run)

        result = ssh.push_file_content("host", "user", "data", "/remote/path", timeout=30)

        assert isinstance(result, Err)
        assert "timed out" in result.error

    def test_command_includes_mkdir_and_chmod(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify the SSH command includes mkdir, cat, and chmod."""
        from overseer import ssh

        captured_cmd: list[str] = []

        def mock_run(cmd, **kwargs):
            captured_cmd.extend(cmd)
            result = MagicMock()
            result.returncode = 0
            return result

        monkeypatch.setattr(ssh.subprocess, "run", mock_run)

        ssh.push_file_content("host", "user", "data", "/home/hermes/.env", mode="0600")

        # The SSH command (last element) should contain mkdir, cat, chmod
        ssh_cmd = captured_cmd[-1]
        assert "mkdir -p" in ssh_cmd
        assert "cat > /home/hermes/.env" in ssh_cmd
        assert "chmod 0600" in ssh_cmd


# ---------------------------------------------------------------------------
# wait_for_ssh
# ---------------------------------------------------------------------------


class TestWaitForSsh:
    def test_immediate_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SSH available on first try returns Ok immediately."""
        from overseer import ssh

        monkeypatch.setattr(ssh, "run_ssh_command", lambda *a, **kw: Ok(""))

        result = ssh.wait_for_ssh("host", "user", timeout=60)

        assert isinstance(result, Ok)
        assert result.value == "host"

    def test_retry_then_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Fails twice then succeeds on third try."""
        from overseer import ssh

        call_count = 0

        def mock_ssh_cmd(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return Err("Connection refused")
            return Ok("")

        monkeypatch.setattr(ssh, "run_ssh_command", mock_ssh_cmd)
        monkeypatch.setattr(ssh, "_sleep", lambda _: None)

        result = ssh.wait_for_ssh("host", "user", timeout=300)

        assert isinstance(result, Ok)
        assert call_count == 3

    def test_timeout_returns_err(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SSH never available -> Err after timeout."""
        from overseer import ssh

        call_count = 0

        def fake_monotonic():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return 0.0  # deadline calculation
            return 301.0  # past deadline

        monkeypatch.setattr(ssh.time, "monotonic", fake_monotonic)
        monkeypatch.setattr(ssh, "run_ssh_command", lambda *a, **kw: Err("refused"))
        monkeypatch.setattr(ssh, "_sleep", lambda _: None)

        result = ssh.wait_for_ssh("host", "user", timeout=300)

        assert isinstance(result, Err)
        assert "not available" in result.error

    def test_does_not_sleep_on_immediate_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If SSH connects immediately, _sleep should never be called."""
        from overseer import ssh

        sleep_called = False

        def track_sleep(seconds):
            nonlocal sleep_called
            sleep_called = True

        monkeypatch.setattr(ssh, "run_ssh_command", lambda *a, **kw: Ok(""))
        monkeypatch.setattr(ssh, "_sleep", track_sleep)

        ssh.wait_for_ssh("host", "user", timeout=60)

        assert not sleep_called


# ---------------------------------------------------------------------------
# rsync_push
# ---------------------------------------------------------------------------


class TestRsyncPush:
    def test_success_returns_ok(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory
    ) -> None:
        """Successful rsync push returns Ok(remote_dir)."""
        from overseer import ssh

        mock_result = MagicMock()
        mock_result.returncode = 0
        monkeypatch.setattr(ssh.subprocess, "run", MagicMock(return_value=mock_result))

        result = ssh.rsync_push("host", "user", "/local/file.tar.gz", "/tmp/")

        assert isinstance(result, Ok)
        assert result.value == "/tmp/"

    def test_failure_returns_err(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-zero exit code returns Err."""
        from overseer import ssh

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "rsync: No route to host"
        monkeypatch.setattr(ssh.subprocess, "run", MagicMock(return_value=mock_result))

        result = ssh.rsync_push("host", "user", "/local/file.tar.gz", "/tmp/")

        assert isinstance(result, Err)
        assert "No route to host" in result.error

    def test_timeout_returns_err(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Timeout returns Err."""
        import subprocess as real_subprocess

        from overseer import ssh

        def mock_run(*args, **kwargs):
            raise real_subprocess.TimeoutExpired(cmd=args[0], timeout=120)

        monkeypatch.setattr(ssh.subprocess, "run", mock_run)

        result = ssh.rsync_push("host", "user", "/local/file.tar.gz", "/tmp/")

        assert isinstance(result, Err)
        assert "timed out" in result.error

    def test_command_targets_correct_destination(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify rsync command pushes local file to user@host:remote_dir."""
        from overseer import ssh

        captured: list[list[str]] = []

        def mock_run(cmd, **kwargs):
            captured.append(cmd)
            result = MagicMock()
            result.returncode = 0
            return result

        monkeypatch.setattr(ssh.subprocess, "run", mock_run)

        ssh.rsync_push("myhost", "myuser", "/local/archive.tar.gz", "/tmp/")

        assert len(captured) == 1
        cmd = captured[0]
        assert "/local/archive.tar.gz" in cmd
        assert "myuser@myhost:/tmp/" in cmd


# ---------------------------------------------------------------------------
# rsync_pull_file
# ---------------------------------------------------------------------------


class TestRsyncPullFile:
    def test_success_returns_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Successful pull returns Ok(local_dir)."""
        from overseer import ssh

        mock_result = MagicMock()
        mock_result.returncode = 0
        monkeypatch.setattr(ssh.subprocess, "run", MagicMock(return_value=mock_result))

        result = ssh.rsync_pull_file("host", "user", "/tmp/archive.tar.gz", "/local/dir/")

        assert isinstance(result, Ok)
        assert result.value == "/local/dir/"

    def test_failure_returns_err(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-zero exit code returns Err."""
        from overseer import ssh

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "rsync: connection reset"
        monkeypatch.setattr(ssh.subprocess, "run", MagicMock(return_value=mock_result))

        result = ssh.rsync_pull_file("host", "user", "/tmp/archive.tar.gz", "/local/dir/")

        assert isinstance(result, Err)
        assert "connection reset" in result.error

    def test_command_does_not_use_relative_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """rsync_pull_file does NOT use --relative (unlike rsync_pull for monitoring)."""
        from overseer import ssh

        captured: list[list[str]] = []

        def mock_run(cmd, **kwargs):
            captured.append(cmd)
            result = MagicMock()
            result.returncode = 0
            return result

        monkeypatch.setattr(ssh.subprocess, "run", mock_run)

        ssh.rsync_pull_file("host", "user", "/tmp/hermes-state-xxx.tar.gz", "/backup/dir/")

        assert len(captured) == 1
        cmd = captured[0]
        assert "--relative" not in cmd
        assert "user@host:/tmp/hermes-state-xxx.tar.gz" in cmd
        assert "/backup/dir/" in cmd
