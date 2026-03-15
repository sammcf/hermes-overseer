"""Entry point for hermes-overseer."""

from __future__ import annotations

import argparse
import logging
import sys
import time

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
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
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


def run_main_loop(cfg: Config) -> None:
    """Main poll loop: monitor → evaluate → respond, with heartbeat/canary on separate intervals."""
    from overseer.binarylane.client import create_client
    from overseer.heartbeat.canary import touch_canary
    from overseer.heartbeat.pulse import send_pulse
    from overseer.monitor.pipeline import run_poll_cycle, run_response_cycle

    bl_client = create_client(cfg.binarylane)
    poll_state = PollState()

    tg_token = resolve_secret(cfg.alerts.telegram.bot_token_env)
    tg_chat = cfg.alerts.telegram.chat_id

    last_heartbeat = 0.0
    last_canary = 0.0

    logger.info("Overseer main loop starting")

    while True:
        loop_start = time.monotonic()

        # --- Poll cycle ---
        try:
            signals, poll_state = run_poll_cycle(cfg, bl_client, poll_state)
            if signals:
                logger.info("Poll produced %d signal(s)", len(signals))
                for sig in signals:
                    logger.info("  [%s] %s: %s", sig.tier.value, sig.source, sig.message)

            results = run_response_cycle(signals, cfg, bl_client)
            for r in results:
                if isinstance(r, Err):
                    logger.error("Action failed: %s", r.error)
                elif isinstance(r, Ok):
                    logger.info("Action succeeded: %s", r.value)
        except Exception:
            logger.exception("Unhandled error in poll/response cycle")

        # --- Canary (touch file on VPS) ---
        now = time.monotonic()
        if now - last_canary >= cfg.overseer.canary_interval_seconds:
            result = touch_canary(cfg.vps.tailscale_hostname, cfg.vps.ssh_user)
            if isinstance(result, Err):
                logger.warning("Canary touch failed: %s", result.error)
            last_canary = now

        # --- Heartbeat (Telegram alive message) ---
        if now - last_heartbeat >= cfg.overseer.heartbeat_interval_seconds:
            vps_status = "OK" if not signals else f"{len(signals)} signal(s)"
            summary = f"Last poll: {poll_state.last_poll_time or 'N/A'}. VPS: {vps_status}"
            pulse_result = send_pulse(tg_token, tg_chat, summary)
            if isinstance(pulse_result, Err):
                logger.warning("Heartbeat pulse failed: %s", pulse_result.error)
            last_heartbeat = now

        # --- Sleep until next poll ---
        elapsed = time.monotonic() - loop_start
        sleep_time = max(0, cfg.overseer.poll_interval_seconds - elapsed)
        if sleep_time > 0:
            time.sleep(sleep_time)


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

    run_main_loop(cfg)


if __name__ == "__main__":
    main()
