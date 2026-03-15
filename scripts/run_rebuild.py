#!/usr/bin/env python3
"""Manual rebuild trigger — invokes the same provisioning pipeline as a RED alert.

Usage:
    set -a && source ~/.config/hermes-overseer/env && set +a
    uv run python scripts/run_rebuild.py [--dry-run]
"""

from __future__ import annotations

import argparse
import logging
import sys

from overseer.binarylane.client import create_client
from overseer.config import load_config
from overseer.provision.provisioner import provision_after_rebuild
from overseer.types import Err, Ok


def main() -> None:
    parser = argparse.ArgumentParser(description="Manual rebuild trigger")
    parser.add_argument(
        "--config",
        default="config/overseer.example.yaml",
        help="Path to overseer config",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate config and cloud-init, but don't actually rebuild",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    logger = logging.getLogger("rebuild")

    cfg = load_config(args.config)
    logger.info("Config loaded: server_id=%s, hostname=%s", cfg.vps.server_id, cfg.vps.tailscale_hostname)

    if args.dry_run:
        from overseer.provision.provisioner import _gather_cloud_init_variables
        from overseer.provision.builder import render_cloud_init, validate_cloud_init

        logger.info("=== DRY RUN — validating only ===")
        vars_result = _gather_cloud_init_variables(cfg)
        if isinstance(vars_result, Err):
            logger.error("Variable gathering failed: %s", vars_result.error)
            sys.exit(1)
        logger.info("Template variables OK")

        rendered = render_cloud_init(vars_result.value)
        if isinstance(rendered, Err):
            logger.error("Cloud-init render failed: %s", rendered.error)
            sys.exit(1)
        logger.info("Cloud-init rendered (%d bytes)", len(rendered.value))

        validated = validate_cloud_init(rendered.value)
        if isinstance(validated, Err):
            logger.error("Cloud-init validation failed: %s", validated.error)
            sys.exit(1)
        logger.info("Cloud-init valid")

        from overseer.provision.provisioner import build_hermes_env_content
        env_result = build_hermes_env_content(cfg.hermes_secrets.env_mapping)
        if isinstance(env_result, Err):
            logger.error("Env build failed: %s", env_result.error)
            sys.exit(1)
        logger.info("Hermes .env content OK (%d vars)", env_result.value.count("="))

        logger.info("=== DRY RUN PASSED ===")
        return

    logger.info("=== STARTING REBUILD — THIS WILL WIPE THE VPS ===")
    bl_client = create_client(cfg.binarylane)
    result = provision_after_rebuild(cfg, bl_client)

    if isinstance(result, Ok):
        pr = result.value
        logger.info("=== REBUILD COMPLETE ===")
        logger.info("  config_pushed:   %s", pr.config_pushed)
        logger.info("  env_pushed:      %s", pr.env_pushed)
        logger.info("  service_started: %s", pr.service_started)
    else:
        logger.error("=== REBUILD FAILED ===")
        logger.error("  %s", result.error)
        sys.exit(1)


if __name__ == "__main__":
    main()
