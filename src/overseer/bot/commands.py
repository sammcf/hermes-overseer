"""Bot command parsing and execution for the Telegram operator interface."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Callable

import httpx

from overseer.backup.snapshot import dump_brewfile, prune_snapshots, take_snapshot
from overseer.config import Config
from overseer.monitor.files import pull_watched_files, reset_file_baseline
from overseer.provision.provisioner import provision_after_rebuild
from overseer.types import Err, Ok, PollState

logger = logging.getLogger(__name__)

_HELP_TEXT = (
    "<b>Hermes Overseer — Commands</b>\n\n"
    "/status — last poll info and sustained unknown count\n"
    "/baseline — pull VPS files and reset file monitor baseline\n"
    "/clear — reset sustained_unknown_count to 0\n"
    "/snapshot — take an on-demand state snapshot\n"
    "/rebuild — full post-rebuild provisioning pipeline (~5 min)\n"
    "/help — show this message"
)


@dataclass(frozen=True)
class BotCommand:
    """A parsed Telegram bot command."""

    chat_id: str
    name: str    # command token without slash, e.g. "help", "rebuild"
    update_id: int


@dataclass
class CommandContext:
    """Runtime context passed to command handlers."""

    cfg: Config
    bl_client: httpx.AsyncClient
    poll_state: PollState
    set_poll_state: Callable[[PollState], None]


def parse_update(update: dict) -> BotCommand | None:  # type: ignore[type-arg]
    """Extract a BotCommand from a raw Telegram Update dict.

    Returns None if the update is not a text message starting with '/'.
    """
    message = update.get("message")
    if not message:
        return None
    text = (message.get("text") or "").strip()
    if not text.startswith("/"):
        return None
    # Normalise: take only the command token, strip /cmd@BotName suffix
    name = text.split()[0].lstrip("/").split("@")[0].lower()
    chat_id = str(message["chat"]["id"])
    return BotCommand(chat_id=chat_id, name=name, update_id=update["update_id"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _send(bot_token: str, chat_id: str, text: str) -> None:
    """Fire-and-forget send. Errors are logged but not raised."""
    from overseer.alert.telegram import send_telegram

    result = await send_telegram(bot_token, chat_id, text)
    if isinstance(result, Err):
        logger.warning("Bot reply failed: %s", result.error)


def _resolve_token(cfg: Config) -> str:
    from overseer.config import resolve_secret

    return resolve_secret(cfg.alerts.telegram.bot_token_env)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


async def _handle_help(cmd: BotCommand, ctx: CommandContext, bot_token: str) -> None:
    await _send(bot_token, cmd.chat_id, _HELP_TEXT)


async def _handle_status(cmd: BotCommand, ctx: CommandContext, bot_token: str) -> None:
    state = ctx.poll_state
    last = (
        state.last_poll_time.strftime("%Y-%m-%dT%H:%M:%SZ")
        if state.last_poll_time
        else "never"
    )
    msg = (
        "<b>Overseer Status</b>\n\n"
        f"VPS: <code>{ctx.cfg.vps.tailscale_hostname}</code>\n"
        f"Last poll: <i>{last}</i>\n"
        f"Sustained unknown count: {state.sustained_unknown_count}"
    )
    await _send(bot_token, cmd.chat_id, msg)


async def _handle_baseline(cmd: BotCommand, ctx: CommandContext, bot_token: str) -> None:
    await _send(bot_token, cmd.chat_id, "Pulling VPS files and resetting baseline…")
    cfg = ctx.cfg
    # Regenerate Brewfile before pulling so the snapshot always has a fresh package list
    dump_brewfile(cfg.vps.tailscale_hostname, cfg.vps.ssh_user)
    pull = pull_watched_files(
        hostname=cfg.vps.tailscale_hostname,
        user=cfg.vps.ssh_user,
        hermes_home=cfg.vps.hermes_home,
        watched_files=cfg.monitor.watched_files,
        state_dir=cfg.overseer.data_dir,
    )
    if isinstance(pull, Err):
        await _send(bot_token, cmd.chat_id, f"Baseline pull failed: {pull.error}")
        return
    reset = reset_file_baseline(cfg.overseer.data_dir)
    if isinstance(reset, Err):
        await _send(bot_token, cmd.chat_id, f"Baseline reset failed: {reset.error}")
        return
    await _send(bot_token, cmd.chat_id, "✅ Baseline accepted.")


async def _handle_clear(cmd: BotCommand, ctx: CommandContext, bot_token: str) -> None:
    old_count = ctx.poll_state.sustained_unknown_count
    ctx.set_poll_state(replace(ctx.poll_state, sustained_unknown_count=0))
    await _send(
        bot_token,
        cmd.chat_id,
        f"✅ Cleared. sustained_unknown_count reset from {old_count} to 0.",
    )


async def _handle_snapshot(cmd: BotCommand, ctx: CommandContext, bot_token: str) -> None:
    await _send(bot_token, cmd.chat_id, "Taking snapshot…")
    cfg = ctx.cfg
    result = take_snapshot(
        cfg.vps.tailscale_hostname,
        cfg.vps.ssh_user,
        cfg.vps.hermes_home,
        cfg.backup.dir,
        extra_paths=cfg.backup.extra_paths,
    )
    if isinstance(result, Err):
        await _send(bot_token, cmd.chat_id, f"❌ Snapshot failed: {result.error}")
    else:
        pruned = prune_snapshots(
            cfg.backup.dir, cfg.backup.retention_count
        )
        if pruned:
            logger.info("Pruned %d old snapshot(s) after /snapshot", pruned)
        filename = result.value.rsplit("/", 1)[-1]
        await _send(bot_token, cmd.chat_id, f"✅ Snapshot saved: <code>{filename}</code>")


async def _handle_rebuild(cmd: BotCommand, ctx: CommandContext, bot_token: str) -> None:
    # Ack immediately — the rebuild blocks for ~5 minutes
    await _send(
        bot_token,
        cmd.chat_id,
        "🔄 Rebuild started. This takes ~5 minutes. I'll report back when done.",
    )
    result = provision_after_rebuild(ctx.cfg, ctx.bl_client)
    if isinstance(result, Err):
        await _send(bot_token, cmd.chat_id, f"❌ Rebuild failed: {result.error}")
    else:
        r = result.value
        lines = [
            "✅ Rebuild complete",
            f"Config pushed: {'✓' if r.config_pushed else '✗'}",
            f"Env pushed:    {'✓' if r.env_pushed else '✗'}",
            f"Service:       {'✓' if r.service_started else '✗'}",
        ]
        await _send(bot_token, cmd.chat_id, "\n".join(lines))


_HANDLERS: dict[str, Callable] = {
    "help": _handle_help,
    "start": _handle_help,
    "commands": _handle_help,
    "status": _handle_status,
    "baseline": _handle_baseline,
    "clear": _handle_clear,
    "snapshot": _handle_snapshot,
    "rebuild": _handle_rebuild,
}


# ---------------------------------------------------------------------------
# Public dispatch entry point
# ---------------------------------------------------------------------------


async def execute_command(cmd: BotCommand, ctx: CommandContext) -> None:
    """Dispatch a bot command. Security check: silently drops unauthorised chats."""
    allowed_chats = ctx.cfg.alerts.telegram.command_chat_ids
    if cmd.chat_id not in allowed_chats:
        logger.warning(
            "Bot command /%s from unauthorised chat %s (allowed: %s)",
            cmd.name, cmd.chat_id, allowed_chats,
        )
        return

    bot_token = _resolve_token(ctx.cfg)
    logger.info("Bot command /%s from chat %s", cmd.name, cmd.chat_id)

    handler = _HANDLERS.get(cmd.name)
    if handler is None:
        await _send(bot_token, cmd.chat_id, f"Unknown command: /{cmd.name}\n\n{_HELP_TEXT}")
        return

    await handler(cmd, ctx, bot_token)
