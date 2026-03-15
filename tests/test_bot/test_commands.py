"""Tests for overseer.bot.commands."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, call, patch

import httpx
import pytest

from overseer.bot.commands import (
    BotCommand,
    CommandContext,
    execute_command,
    parse_update,
)
from overseer.config import Config
from overseer.types import Err, Ok, PollState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_update(
    chat_id: int = 99,
    text: str = "/help",
    update_id: int = 1,
) -> dict:  # type: ignore[type-arg]
    return {
        "update_id": update_id,
        "message": {
            "chat": {"id": chat_id},
            "text": text,
            "from": {"username": "operator"},
        },
    }


@pytest.fixture
def ctx(example_config: Config) -> CommandContext:
    state_ref: list[PollState] = [PollState()]
    return CommandContext(
        cfg=example_config,
        bl_client=MagicMock(spec=httpx.Client),
        poll_state=state_ref[0],
        set_poll_state=lambda s: state_ref.__setitem__(0, s),
    )


def _allowed_chat(example_config: Config) -> str:
    return example_config.alerts.telegram.chat_id


# ---------------------------------------------------------------------------
# parse_update
# ---------------------------------------------------------------------------


def test_parse_update_text_command() -> None:
    cmd = parse_update(_make_update(chat_id=42, text="/status", update_id=7))
    assert cmd is not None
    assert cmd.name == "status"
    assert cmd.chat_id == "42"
    assert cmd.update_id == 7


def test_parse_update_non_command_text() -> None:
    assert parse_update(_make_update(text="hello world")) is None


def test_parse_update_no_message() -> None:
    assert parse_update({"update_id": 1, "callback_query": {}}) is None


def test_parse_update_strips_bot_suffix() -> None:
    cmd = parse_update(_make_update(text="/help@OverseerBot"))
    assert cmd is not None
    assert cmd.name == "help"


def test_parse_update_case_normalised() -> None:
    cmd = parse_update(_make_update(text="/STATUS"))
    assert cmd is not None
    assert cmd.name == "status"


def test_parse_update_ignores_extra_text() -> None:
    cmd = parse_update(_make_update(text="/baseline now please"))
    assert cmd is not None
    assert cmd.name == "baseline"


# ---------------------------------------------------------------------------
# execute_command — security
# ---------------------------------------------------------------------------


def test_execute_command_unauthorised_chat_silent(
    ctx: CommandContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[str] = []
    monkeypatch.setattr(
        "overseer.bot.commands._send",
        lambda token, chat, text: sent.append(text),
    )
    cmd = BotCommand(chat_id="BADCHAT", name="help", update_id=1)
    execute_command(cmd, ctx)
    assert sent == []  # silent drop


# ---------------------------------------------------------------------------
# /help
# ---------------------------------------------------------------------------


def test_handle_help_contains_all_commands(
    ctx: CommandContext, monkeypatch: pytest.MonkeyPatch, example_config: Config
) -> None:
    sent: list[str] = []
    monkeypatch.setattr("overseer.bot.commands._send", lambda t, c, text: sent.append(text))
    monkeypatch.setenv(example_config.alerts.telegram.bot_token_env, "TOKEN")
    cmd = BotCommand(chat_id=_allowed_chat(example_config), name="help", update_id=1)
    execute_command(cmd, ctx)
    assert sent
    text = sent[0]
    for keyword in ("/status", "/baseline", "/clear", "/snapshot", "/rebuild", "/help"):
        assert keyword in text


# ---------------------------------------------------------------------------
# /status
# ---------------------------------------------------------------------------


def test_handle_status_never_polled(
    ctx: CommandContext, monkeypatch: pytest.MonkeyPatch, example_config: Config
) -> None:
    sent: list[str] = []
    monkeypatch.setattr("overseer.bot.commands._send", lambda t, c, text: sent.append(text))
    monkeypatch.setenv(example_config.alerts.telegram.bot_token_env, "TOKEN")
    cmd = BotCommand(chat_id=_allowed_chat(example_config), name="status", update_id=1)
    execute_command(cmd, ctx)
    assert "never" in sent[0].lower()


def test_handle_status_shows_last_poll_time(
    ctx: CommandContext, monkeypatch: pytest.MonkeyPatch, example_config: Config
) -> None:
    ts = datetime(2026, 3, 16, 9, 0, 0, tzinfo=UTC)
    ctx.poll_state = PollState(last_poll_time=ts, sustained_unknown_count=2)
    sent: list[str] = []
    monkeypatch.setattr("overseer.bot.commands._send", lambda t, c, text: sent.append(text))
    monkeypatch.setenv(example_config.alerts.telegram.bot_token_env, "TOKEN")
    cmd = BotCommand(chat_id=_allowed_chat(example_config), name="status", update_id=1)
    execute_command(cmd, ctx)
    assert "2026-03-16T09:00:00Z" in sent[0]
    assert "2" in sent[0]  # sustained count


# ---------------------------------------------------------------------------
# /clear
# ---------------------------------------------------------------------------


def test_handle_clear_resets_count(
    ctx: CommandContext, monkeypatch: pytest.MonkeyPatch, example_config: Config
) -> None:
    new_states: list[PollState] = []
    ctx.poll_state = PollState(sustained_unknown_count=5)
    ctx.set_poll_state = lambda s: new_states.append(s)
    sent: list[str] = []
    monkeypatch.setattr("overseer.bot.commands._send", lambda t, c, text: sent.append(text))
    monkeypatch.setenv(example_config.alerts.telegram.bot_token_env, "TOKEN")
    cmd = BotCommand(chat_id=_allowed_chat(example_config), name="clear", update_id=1)
    execute_command(cmd, ctx)
    assert new_states[0].sustained_unknown_count == 0
    assert "0" in sent[0]


# ---------------------------------------------------------------------------
# /baseline
# ---------------------------------------------------------------------------


def test_handle_baseline_pull_failure(
    ctx: CommandContext, monkeypatch: pytest.MonkeyPatch, example_config: Config
) -> None:
    sent: list[str] = []
    monkeypatch.setattr("overseer.bot.commands._send", lambda t, c, text: sent.append(text))
    monkeypatch.setenv(example_config.alerts.telegram.bot_token_env, "TOKEN")
    with patch(
        "overseer.bot.commands.pull_watched_files",
        return_value=Err("ssh failed", source="files"),
    ):
        cmd = BotCommand(chat_id=_allowed_chat(example_config), name="baseline", update_id=1)
        execute_command(cmd, ctx)
    assert any("failed" in s.lower() for s in sent)


def test_handle_baseline_success(
    ctx: CommandContext, monkeypatch: pytest.MonkeyPatch, example_config: Config
) -> None:
    sent: list[str] = []
    monkeypatch.setattr("overseer.bot.commands._send", lambda t, c, text: sent.append(text))
    monkeypatch.setenv(example_config.alerts.telegram.bot_token_env, "TOKEN")
    with (
        patch("overseer.bot.commands.pull_watched_files", return_value=Ok("pulled")),
        patch("overseer.bot.commands.reset_file_baseline", return_value=Ok("/path/last_good")),
    ):
        cmd = BotCommand(chat_id=_allowed_chat(example_config), name="baseline", update_id=1)
        execute_command(cmd, ctx)
    assert any("accepted" in s.lower() for s in sent)


# ---------------------------------------------------------------------------
# /snapshot
# ---------------------------------------------------------------------------


def test_handle_snapshot_success(
    ctx: CommandContext, monkeypatch: pytest.MonkeyPatch, example_config: Config
) -> None:
    sent: list[str] = []
    monkeypatch.setattr("overseer.bot.commands._send", lambda t, c, text: sent.append(text))
    monkeypatch.setenv(example_config.alerts.telegram.bot_token_env, "TOKEN")
    with patch(
        "overseer.bot.commands.take_snapshot",
        return_value=Ok("/backups/hermes-state-20260316T000000Z.tar.gz"),  # type: ignore[arg-type]
    ):
        cmd = BotCommand(chat_id=_allowed_chat(example_config), name="snapshot", update_id=1)
        execute_command(cmd, ctx)
    assert any("hermes-state" in s for s in sent)


def test_handle_snapshot_failure(
    ctx: CommandContext, monkeypatch: pytest.MonkeyPatch, example_config: Config
) -> None:
    sent: list[str] = []
    monkeypatch.setattr("overseer.bot.commands._send", lambda t, c, text: sent.append(text))
    monkeypatch.setenv(example_config.alerts.telegram.bot_token_env, "TOKEN")
    with patch(
        "overseer.bot.commands.take_snapshot",
        return_value=Err("ssh error", source="snapshot"),
    ):
        cmd = BotCommand(chat_id=_allowed_chat(example_config), name="snapshot", update_id=1)
        execute_command(cmd, ctx)
    assert any("failed" in s.lower() for s in sent)


# ---------------------------------------------------------------------------
# /rebuild
# ---------------------------------------------------------------------------


def test_handle_rebuild_sends_ack_before_provision(
    ctx: CommandContext, monkeypatch: pytest.MonkeyPatch, example_config: Config
) -> None:
    """Ack message must be sent BEFORE the blocking provision_after_rebuild call."""
    call_log: list[str] = []
    monkeypatch.setattr(
        "overseer.bot.commands._send",
        lambda t, c, text: call_log.append(("send", text)),
    )
    monkeypatch.setenv(example_config.alerts.telegram.bot_token_env, "TOKEN")

    from overseer.types import ProvisionResult

    def _fake_provision(cfg, bl):
        call_log.append(("provision",))
        return Ok(ProvisionResult(
            rebuild_action={},
            config_pushed=True,
            env_pushed=True,
            service_started=True,
        ))

    with patch("overseer.bot.commands.provision_after_rebuild", side_effect=_fake_provision):
        cmd = BotCommand(chat_id=_allowed_chat(example_config), name="rebuild", update_id=1)
        execute_command(cmd, ctx)

    assert call_log[0][0] == "send", "ack should be first"
    assert call_log[1] == ("provision",), "provision should follow ack"
    assert "started" in call_log[0][1].lower() or "rebuild" in call_log[0][1].lower()


def test_handle_rebuild_success_message(
    ctx: CommandContext, monkeypatch: pytest.MonkeyPatch, example_config: Config
) -> None:
    sent: list[str] = []
    monkeypatch.setattr("overseer.bot.commands._send", lambda t, c, text: sent.append(text))
    monkeypatch.setenv(example_config.alerts.telegram.bot_token_env, "TOKEN")

    from overseer.types import ProvisionResult

    with patch(
        "overseer.bot.commands.provision_after_rebuild",
        return_value=Ok(ProvisionResult(
            rebuild_action={},
            config_pushed=True,
            env_pushed=True,
            service_started=True,
        )),
    ):
        cmd = BotCommand(chat_id=_allowed_chat(example_config), name="rebuild", update_id=1)
        execute_command(cmd, ctx)

    final = sent[-1]
    assert "complete" in final.lower() or "✅" in final


def test_handle_rebuild_failure(
    ctx: CommandContext, monkeypatch: pytest.MonkeyPatch, example_config: Config
) -> None:
    sent: list[str] = []
    monkeypatch.setattr("overseer.bot.commands._send", lambda t, c, text: sent.append(text))
    monkeypatch.setenv(example_config.alerts.telegram.bot_token_env, "TOKEN")

    with patch(
        "overseer.bot.commands.provision_after_rebuild",
        return_value=Err("rebuild failed", source="provision"),
    ):
        cmd = BotCommand(chat_id=_allowed_chat(example_config), name="rebuild", update_id=1)
        execute_command(cmd, ctx)

    assert any("failed" in s.lower() for s in sent)


# ---------------------------------------------------------------------------
# Unknown command
# ---------------------------------------------------------------------------


def test_execute_unknown_command(
    ctx: CommandContext, monkeypatch: pytest.MonkeyPatch, example_config: Config
) -> None:
    sent: list[str] = []
    monkeypatch.setattr("overseer.bot.commands._send", lambda t, c, text: sent.append(text))
    monkeypatch.setenv(example_config.alerts.telegram.bot_token_env, "TOKEN")
    cmd = BotCommand(chat_id=_allowed_chat(example_config), name="notacommand", update_id=1)
    execute_command(cmd, ctx)
    assert sent
    assert "notacommand" in sent[0] or "unknown" in sent[0].lower()
