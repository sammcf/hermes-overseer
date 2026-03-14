"""Pydantic v2 config schema + YAML loader. Secrets reference env var names."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, model_validator


class OverseerConfig(BaseModel, frozen=True):
    poll_interval_seconds: int = 120
    heartbeat_interval_seconds: int = 1800
    canary_interval_seconds: int = 180
    canary_stale_threshold_seconds: int = 3600
    data_dir: str = "/var/lib/hermes-overseer"

    @model_validator(mode="after")
    def expand_paths(self) -> OverseerConfig:
        object.__setattr__(self, "data_dir", os.path.expanduser(self.data_dir))
        return self


class VpsConfig(BaseModel, frozen=True):
    server_id: int
    tailscale_hostname: str
    ssh_user: str = "hermes"
    hermes_home: str = "/home/hermes/.hermes"
    base_image_id: str = "ubuntu-24.04"
    tailscale_auth_key_env: str = "TS_HERMES_AUTH_KEY"


class BinaryLaneConfig(BaseModel, frozen=True):
    api_token_env: str = "BL_API_TOKEN"
    base_url: str = "https://api.binarylane.com.au"
    max_retries: int = 5


class TelegramConfig(BaseModel, frozen=True):
    bot_token_env: str = "OVERSEER_TG_BOT_TOKEN"
    chat_id: str


class EmailConfig(BaseModel, frozen=True):
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    from_address: str
    to_address: str
    password_env: str = "OVERSEER_EMAIL_PASSWORD"


class AlertsConfig(BaseModel, frozen=True):
    telegram: TelegramConfig
    email: EmailConfig


class WatchedFilesConfig(BaseModel, frozen=True):
    orange_on_any_diff: list[str] = [".env", "config.yaml"]
    orange_on_suspicious_diff: list[str] = ["SOUL.md", "memories/MEMORY.md", "memories/USER.md"]
    yellow_on_any_diff: list[str] = ["cron/jobs.json"]
    yellow_on_new_file: list[str] = ["skills/"]


class MonitorConfig(BaseModel, frozen=True):
    watched_files: WatchedFilesConfig = WatchedFilesConfig()
    connection_allowlist: list[str] = []
    sustained_unknown_threshold: int = 3


class ProviderCostConfig(BaseModel, frozen=True):
    type: str  # "prepaid_wallet" or "rolling_window"
    yellow_remaining_usd: float | None = None
    orange_remaining_usd: float | None = None
    yellow_percent: int | None = None
    orange_percent: int | None = None


class CostConfig(BaseModel, frozen=True):
    providers: dict[str, ProviderCostConfig] = {}
    canonical_hermes_config: str = "config/hermes-canonical.yaml"
    dispatch_policy_fields: list[str] = [
        "model.default",
        "model.provider",
        "toolsets",
        "mcp_servers",
    ]

    @model_validator(mode="after")
    def expand_paths(self) -> CostConfig:
        object.__setattr__(
            self, "canonical_hermes_config", os.path.expanduser(self.canonical_hermes_config)
        )
        return self


class TierActionConfig(BaseModel, frozen=True):
    actions: list[str]


class ResponseConfig(BaseModel, frozen=True):
    yellow: TierActionConfig = TierActionConfig(actions=["alert"])
    orange: TierActionConfig = TierActionConfig(actions=["power_off", "alert", "take_backup"])
    red: TierActionConfig = TierActionConfig(
        actions=["take_backup", "rebuild", "revoke_keys", "alert"]
    )


class Config(BaseModel, frozen=True):
    """Root configuration. All secrets are referenced by env var name, never stored directly."""

    overseer: OverseerConfig = OverseerConfig()
    vps: VpsConfig
    binarylane: BinaryLaneConfig = BinaryLaneConfig()
    alerts: AlertsConfig
    monitor: MonitorConfig = MonitorConfig()
    cost: CostConfig = CostConfig()
    response: ResponseConfig = ResponseConfig()

    @model_validator(mode="after")
    def validate_env_vars_exist(self) -> Config:
        """Warn about missing env vars at config load time."""
        env_refs = [
            self.vps.tailscale_auth_key_env,
            self.binarylane.api_token_env,
            self.alerts.telegram.bot_token_env,
            self.alerts.email.password_env,
        ]
        missing = [ref for ref in env_refs if not os.environ.get(ref)]
        if missing:
            import warnings

            warnings.warn(
                f"Missing env vars (needed at runtime): {', '.join(missing)}",
                stacklevel=2,
            )
        return self


def load_config(path: str | Path) -> Config:
    """Load and validate config from a YAML file."""
    path = Path(path)
    with path.open() as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise ValueError(f"Config file {path} must contain a YAML mapping")
    return Config.model_validate(raw)


def resolve_secret(env_var_name: str) -> str:
    """Resolve a secret from an environment variable. Raises if missing."""
    value = os.environ.get(env_var_name)
    if not value:
        raise RuntimeError(f"Required environment variable not set: {env_var_name}")
    return value
