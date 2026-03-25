"""Entry point for hermes-overseer."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time

import httpx

from overseer.config import Config, load_config, resolve_secret
from overseer.types import Err, Ok, PollState

logger = logging.getLogger("overseer")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="overseer",
        description="Hermes VPS overseer — monitor, alert, and respond",
    )
    parser.add_argument(
        "--config",
        required=True,
        help="Path to overseer.yaml config file",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Load and validate config, then exit",
    )
    parser.add_argument(
        "--accept-baseline",
        action="store_true",
        help=(
            "Pull current VPS state and accept it as the new file monitor baseline, then exit. "
            "Use this after intentional hermes changes (SOUL.md, memories) to silence alerts."
        ),
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )
    # Forensics subcommand
    parser.add_argument(
        "--forensics",
        action="store_true",
        help="Run forensic analysis on a snapshot DB, then exit",
    )
    parser.add_argument(
        "--snapshot",
        help="Path to snapshot archive (default: latest in backup_dir)",
    )
    parser.add_argument(
        "--window",
        type=float,
        default=24.0,
        help="Analysis window in hours (default: 24)",
    )
    parser.add_argument(
        "--search",
        help="FTS5 query for message content search",
    )
    return parser.parse_args(argv)


def print_config_summary(cfg: Config, config_path: str) -> None:
    """Print a human-readable config summary."""
    print(f"Config loaded from: {config_path}")
    print(f"  VPS server_id:    {cfg.vps.server_id}")
    print(f"  VPS hostname:     {cfg.vps.tailscale_hostname}")
    print(f"  Poll interval:    {cfg.overseer.poll_interval_seconds}s")
    print(f"  Heartbeat:        {cfg.overseer.heartbeat_interval_seconds}s")
    print(f"  Canary interval:  {cfg.overseer.canary_interval_seconds}s")
    print(f"  Data dir:         {cfg.overseer.data_dir}")
    print(f"  BL base URL:      {cfg.binarylane.base_url}")
    print("  Alert channels:   telegram, email")
    print(f"  Monitored files:  {len(cfg.monitor.watched_files.orange_on_any_diff)} orange, "
          f"{len(cfg.monitor.watched_files.orange_on_suspicious_diff)} suspicious, "
          f"{len(cfg.monitor.watched_files.yellow_on_any_diff)} yellow")
    print(f"  Connection allow: {len(cfg.monitor.connection_allowlist)} hosts")
    print(f"  Cost providers:   {len(cfg.cost.providers)}")


def _cmd_accept_baseline(cfg: Config) -> None:
    """Pull current VPS state and reset the file monitor baseline."""
    from overseer.monitor.files import pull_watched_files, reset_file_baseline
    from overseer.types import Err

    print(f"Pulling watched files from {cfg.vps.tailscale_hostname}...")
    pull_result = pull_watched_files(
        hostname=cfg.vps.tailscale_hostname,
        user=cfg.vps.ssh_user,
        hermes_home=cfg.vps.hermes_home,
        watched_files=cfg.monitor.watched_files,
        state_dir=cfg.overseer.data_dir,
    )
    if isinstance(pull_result, Err):
        print(f"Pull failed: {pull_result.error}", file=sys.stderr)
        sys.exit(1)

    reset_result = reset_file_baseline(cfg.overseer.data_dir)
    if isinstance(reset_result, Err):
        print(f"Baseline reset failed: {reset_result.error}", file=sys.stderr)
        sys.exit(1)

    print("Baseline accepted: current VPS state is now the new last-known-good baseline.")
    print(f"  Baseline stored at: {reset_result.value}")


def _cmd_forensics(cfg: Config, args: argparse.Namespace) -> None:
    """Run forensic analysis on a snapshot or the latest backup."""
    import glob
    from pathlib import Path

    from overseer.forensics.session_db import (
        extract_db_from_snapshot,
        generate_incident_report,
        search_messages,
    )
    from overseer.types import Err

    # Determine snapshot path
    snapshot_path = args.snapshot
    if snapshot_path is None:
        backup_dir = cfg.overseer.backup_dir
        archives = sorted(glob.glob(f"{backup_dir}/hermes-state-*.tar.gz"))
        if not archives:
            print(f"No snapshots found in {backup_dir}", file=sys.stderr)
            sys.exit(1)
        snapshot_path = archives[-1]
        print(f"Using latest snapshot: {Path(snapshot_path).name}")

    # Extract DB
    extract_result = extract_db_from_snapshot(snapshot_path)
    if isinstance(extract_result, Err):
        print(f"Extraction failed: {extract_result.error}", file=sys.stderr)
        sys.exit(1)

    db_path = extract_result.value
    print(f"Extracted DB: {db_path}")

    # FTS5 search if requested
    if args.search:
        results = search_messages(db_path, args.search, window_hours=args.window)
        print(f"\nSearch results for '{args.search}' ({len(results)} matches):")
        for r in results:
            ts = r.get("timestamp", "?")
            role = r.get("role", "?")
            content = r.get("content", "")[:100]
            print(f"  [{ts}] {role}: {content}")
        return

    # Generate incident report
    report = generate_incident_report(db_path, window_hours=args.window)
    print(f"\n{'='*60}")
    print(f"INCIDENT REPORT — {report.window_start:%Y-%m-%d %H:%M} to {report.window_end:%Y-%m-%d %H:%M}")
    print(f"{'='*60}")
    print(f"Sessions: {len(report.sessions)}")
    print(f"Total tokens: {report.total_tokens:,}")
    print(f"Models used: {', '.join(report.models_used) or 'none'}")
    print(f"Tool calls: {len(report.tool_calls)}")

    if report.anomalies:
        print(f"\nANOMALIES ({len(report.anomalies)}):")
        for a in report.anomalies:
            print(f"  ⚠ {a}")

    print(f"\nSESSIONS:")
    for s in report.sessions:
        status = "active" if s.ended_at is None else "ended"
        print(
            f"  {s.session_id[:8]} [{s.source}] {s.model or '?'} "
            f"— {s.message_count} msgs, {s.tool_call_count} tools, "
            f"{s.input_tokens + s.output_tokens:,} tok ({status})"
        )


async def _bot_polling_loop(
    cfg: Config,
    tg_token: str,
    get_poll_state: object,
    set_poll_state: object,
) -> None:
    """Continuously poll Telegram for bot commands. Runs as a concurrent task."""
    from overseer.bot.commands import CommandContext, execute_command, parse_update
    from overseer.bot.poller import fetch_updates

    bl_client = httpx.AsyncClient(
        base_url=cfg.binarylane.base_url,
        headers={"Authorization": f"Bearer {resolve_secret(cfg.binarylane.api_token_env)}"},
    )
    bot_update_offset: int = 0

    while True:
        try:
            updates_result = await fetch_updates(tg_token, bot_update_offset)
            if isinstance(updates_result, Ok):
                for update in updates_result.value:
                    update_id: int = update["update_id"]
                    if update_id >= bot_update_offset:
                        bot_update_offset = update_id + 1
                    bot_ctx = CommandContext(
                        cfg=cfg,
                        bl_client=bl_client,
                        poll_state=get_poll_state(),
                        set_poll_state=set_poll_state,
                    )
                    cmd = parse_update(update)
                    if cmd is not None:
                        try:
                            await execute_command(cmd, bot_ctx)
                        except Exception:
                            logger.exception("Unhandled error in bot command /%s", cmd.name)
            elif isinstance(updates_result, Err):
                logger.debug("Bot polling error (non-fatal): %s", updates_result.error)
        except Exception:
            logger.exception("Unhandled error in bot polling loop")

        await asyncio.sleep(5.0)


async def run_main_loop(cfg: Config) -> None:
    """Main async loop: monitor → evaluate → respond, with heartbeat/canary on separate intervals."""
    from overseer.backup.snapshot import prune_snapshots, take_snapshot
    from overseer.binarylane.client import create_client
    from overseer.heartbeat.canary import touch_canary
    from overseer.heartbeat.pulse import send_pulse
    from overseer.monitor.pipeline import run_poll_cycle, run_response_cycle

    bl_client = create_client(cfg.binarylane)
    poll_state = PollState()

    tg_token = resolve_secret(cfg.alerts.telegram.bot_token_env)
    tg_chat = cfg.alerts.telegram.heartbeat_chat_id

    last_heartbeat = 0.0
    last_canary = 0.0
    last_backup = 0.0

    startup_pruned = prune_snapshots(
        cfg.overseer.backup_dir, cfg.overseer.backup_retention_count
    )
    if startup_pruned:
        logger.info("Startup prune removed %d old snapshot(s)", startup_pruned)

    # Shared state accessors for bot polling task
    def get_poll_state() -> PollState:
        return poll_state

    def set_poll_state(new_state: PollState) -> None:
        nonlocal poll_state
        poll_state = new_state

    # Start bot polling as a concurrent task
    bot_task = asyncio.create_task(
        _bot_polling_loop(cfg, tg_token, get_poll_state, set_poll_state)
    )

    # Optionally start the HTTP API server
    api_runner = None
    if cfg.api.enabled:
        import asyncio as _aio

        from overseer.api.server import AppState, start_api_server

        app_state = AppState(
            config=cfg,
            poll_state=poll_state,
            bl_client=bl_client,
            start_time=time.monotonic(),
            op_lock=_aio.Lock(),
        )
        # Keep app_state.poll_state in sync via property-like update in the loop
        _app_state_ref = app_state
        _original_set = set_poll_state

        def set_poll_state_with_api(new_state: PollState) -> None:
            _original_set(new_state)
            _app_state_ref.poll_state = new_state

        set_poll_state = set_poll_state_with_api  # type: ignore[assignment]
        api_runner = await start_api_server(app_state)

    logger.info("Overseer main loop starting")

    try:
        while True:
            loop_start = time.monotonic()

            # --- Poll cycle (sync, run in thread) ---
            try:
                signals, poll_state = await asyncio.to_thread(
                    run_poll_cycle, cfg, bl_client, poll_state
                )
                if api_runner is not None:
                    _app_state_ref.poll_state = poll_state
                if signals:
                    logger.info("Poll produced %d signal(s)", len(signals))
                    for sig in signals:
                        logger.info("  [%s] %s: %s", sig.tier.value, sig.source, sig.message)

                results = await run_response_cycle(signals, cfg, bl_client)
                for r in results:
                    if isinstance(r, Err):
                        logger.error("Action failed: %s", r.error)
                    elif isinstance(r, Ok):
                        logger.info("Action succeeded: %s", r.value)
            except Exception:
                logger.exception("Unhandled error in poll/response cycle")

            # --- Canary (touch file on VPS, blocking SSH → thread) ---
            now = time.monotonic()
            if now - last_canary >= cfg.overseer.canary_interval_seconds:
                result = await asyncio.to_thread(
                    touch_canary, cfg.vps.tailscale_hostname, cfg.vps.ssh_user
                )
                if isinstance(result, Err):
                    logger.warning("Canary touch failed: %s", result.error)
                last_canary = now

            # --- Periodic state snapshot (blocking SSH → thread) ---
            if now - last_backup >= cfg.overseer.backup_interval_seconds:
                snap_result = await asyncio.to_thread(
                    take_snapshot,
                    cfg.vps.tailscale_hostname,
                    cfg.vps.ssh_user,
                    cfg.vps.hermes_home,
                    cfg.overseer.backup_dir,
                    cfg.vps.snapshot_extra_paths,
                )
                if isinstance(snap_result, Err):
                    logger.warning("Snapshot failed: %s", snap_result.error)
                else:
                    logger.info("Snapshot saved: %s", snap_result.value)
                    pruned = prune_snapshots(
                        cfg.overseer.backup_dir, cfg.overseer.backup_retention_count
                    )
                    if pruned:
                        logger.info("Pruned %d old snapshot(s)", pruned)
                last_backup = now

            # --- Heartbeat (Telegram alive message) ---
            if now - last_heartbeat >= cfg.overseer.heartbeat_interval_seconds:
                vps_status = "OK" if not signals else f"{len(signals)} signal(s)"
                summary = f"Last poll: {poll_state.last_poll_time or 'N/A'}. VPS: {vps_status}"
                pulse_result = await send_pulse(tg_token, tg_chat, summary)
                if isinstance(pulse_result, Err):
                    logger.warning("Heartbeat pulse failed: %s", pulse_result.error)
                last_heartbeat = now

            # --- Sleep until next poll cycle ---
            elapsed = time.monotonic() - loop_start
            sleep_remaining = max(0.0, cfg.overseer.poll_interval_seconds - elapsed)
            await asyncio.sleep(sleep_remaining)
    finally:
        bot_task.cancel()
        try:
            await bot_task
        except asyncio.CancelledError:
            pass
        if api_runner is not None:
            await api_runner.cleanup()


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    try:
        cfg = load_config(args.config)
        print_config_summary(cfg, args.config)
    except Exception as e:
        print(f"Error loading config: {e}", file=sys.stderr)
        sys.exit(1)

    if args.validate_only:
        print("Config valid.")
        return

    if args.accept_baseline:
        _cmd_accept_baseline(cfg)
        return

    if args.forensics:
        _cmd_forensics(cfg, args)
        return

    asyncio.run(run_main_loop(cfg))


if __name__ == "__main__":
    main()
