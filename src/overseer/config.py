"""Pydantic v2 config schema + YAML loader. Secrets reference env var names."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, model_validator

from overseer.types import AlertTier


class OverseerConfig(BaseModel, frozen=True):
    poll_interval_seconds: int = 120
    heartbeat_interval_seconds: int = 1800
    canary_interval_seconds: int = 180
    canary_stale_threshold_seconds: int = 3600
    data_dir: str = "/var/lib/hermes-overseer"
    backup_interval_seconds: int = 14400  # 4 hours
    backup_retention_count: int = 4       # keep most recent 4 snapshots
    backup_dir: str = "~/.local/share/hermes-overseer/backups"
    secrets_dir: str = "~/.config/hermes-overseer"
    patches_dir: str = "~/.config/hermes-overseer/patches"

    @model_validator(mode="after")
    def expand_paths(self) -> OverseerConfig:
        object.__setattr__(self, "data_dir", os.path.expanduser(self.data_dir))
        object.__setattr__(self, "backup_dir", os.path.expanduser(self.backup_dir))
        object.__setattr__(self, "secrets_dir", os.path.expanduser(self.secrets_dir))
        object.__setattr__(self, "patches_dir", os.path.expanduser(self.patches_dir))
        return self


class VpsConfig(BaseModel, frozen=True):
    server_id: int
    tailscale_hostname: str
    ssh_user: str = "hermes"
    hermes_home: str = "/home/hermes/.hermes"
    base_image_id: str = "ubuntu-24.04"
    tailscale_auth_key_env: str = "TS_HERMES_AUTH_KEY"
    tailscale_api_key_env: str = "TS_API_KEY"
    tailscale_tailnet: str = "-"
    ssh_public_key_path: str = "~/.ssh/id_ed25519.pub"
    # Paths relative to the hermes user home (parent of hermes_home) to include
    # in snapshots alongside hermes_home/. Use --ignore-failed-read so missing
    # files (e.g. .claude.json before first auth) don't abort the snapshot.
    snapshot_extra_paths: list[str] = [".claude.json", ".claude"]

    @model_validator(mode="after")
    def expand_paths(self) -> VpsConfig:
        object.__setattr__(
            self, "ssh_public_key_path", os.path.expanduser(self.ssh_public_key_path)
        )
        return self


class BinaryLaneConfig(BaseModel, frozen=True):
    api_token_env: str = "BL_API_TOKEN"
    base_url: str = "https://api.binarylane.com.au"
    max_retries: int = 5


class TelegramConfig(BaseModel, frozen=True):
    bot_token_env: str = "OVERSEER_TG_BOT_TOKEN"
    dm_chat_id: str
    group_chat_id: str | None = None

    @property
    def command_chat_ids(self) -> frozenset[str]:
        """All configured chat IDs that should receive commands."""
        ids = {self.dm_chat_id}
        if self.group_chat_id is not None:
            ids.add(self.group_chat_id)
        return frozenset(ids)

    def alert_chat_ids(self, tier: AlertTier) -> frozenset[str]:
        """Returns which chats to alert based on tier.

        YELLOW/ORANGE: group_chat_id only (fallback to dm_chat_id if group not configured).
        RED: both dm_chat_id and group_chat_id (or just dm_chat_id if group not configured).
        """
        if tier == AlertTier.RED:
            ids = {self.dm_chat_id}
            if self.group_chat_id is not None:
                ids.add(self.group_chat_id)
            return frozenset(ids)
        else:
            # YELLOW or ORANGE
            if self.group_chat_id is not None:
                return frozenset({self.group_chat_id})
            return frozenset({self.dm_chat_id})

    @property
    def heartbeat_chat_id(self) -> str:
        """Returns group_chat_id if set, else dm_chat_id."""
        return self.group_chat_id if self.group_chat_id is not None else self.dm_chat_id


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
    orange_on_any_diff: list[str] = [".env", "config.yaml", "google_token.json", "google_client_secret.json"]
    orange_on_suspicious_diff: list[str] = ["SOUL.md", "memories/MEMORY.md", "memories/USER.md"]
    yellow_on_any_diff: list[str] = []
    yellow_on_new_file: list[str] = ["skills/"]


class SessionThresholds(BaseModel, frozen=True):
    """Thresholds for hermes session activity monitoring."""

    window_hours: int = 6
    inactivity_alert_hours: int = 24
    max_tool_calls_per_session: int = 200
    max_tokens_per_window: int = 2_000_000
    max_session_duration_hours: float = 8.0


class TokenBudgetConfig(BaseModel, frozen=True):
    """Rolling-window token budget enforcement."""

    window_hours: int = 6
    warn_tokens: int = 1_000_000
    critical_tokens: int = 3_000_000


class MonitorConfig(BaseModel, frozen=True):
    watched_files: WatchedFilesConfig = WatchedFilesConfig()
    connection_allowlist: list[str] = []
    sustained_unknown_threshold: int = 3
    session_thresholds: SessionThresholds = SessionThresholds()
    token_budget: TokenBudgetConfig = TokenBudgetConfig()


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


class HermesSecretsConfig(BaseModel, frozen=True):
    """Maps hermes .env variable names → overseer env var names for secret resolution.

    file_secrets maps hermes_home-relative filenames → overseer secrets_dir filenames.
    These are pushed to the VPS on rebuild (e.g. Google OAuth token/credentials).
    """

    env_mapping: dict[str, str] = {
        "OPENROUTER_API_KEY": "OPENROUTER_API_KEY",
        "TELEGRAM_BOT_TOKEN": "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_ALLOWED_USERS": "TELEGRAM_ALLOWED_USERS",
        "FIRECRAWL_API_KEY": "FIRECRAWL_API_KEY",
    }
    file_secrets: dict[str, str] = {
        "google_token.json": "google_token.json",
        "google_client_secret.json": "google_client_secret.json",
    }


class ApiConfig(BaseModel, frozen=True):
    enabled: bool = False
    host: str = "0.0.0.0"
    port: int = 8900
    bearer_token_env: str = "OVERSEER_API_TOKEN"


class Config(BaseModel, frozen=True):
    """Root configuration. All secrets are referenced by env var name, never stored directly."""

    overseer: OverseerConfig = OverseerConfig()
    vps: VpsConfig
    binarylane: BinaryLaneConfig = BinaryLaneConfig()
    alerts: AlertsConfig
    monitor: MonitorConfig = MonitorConfig()
    cost: CostConfig = CostConfig()
    response: ResponseConfig = ResponseConfig()
    hermes_secrets: HermesSecretsConfig = HermesSecretsConfig()
    api: ApiConfig = ApiConfig()

    @model_validator(mode="after")
    def validate_env_vars_exist(self) -> Config:
        """Warn about missing env vars at config load time."""
        env_refs = [
            self.vps.tailscale_auth_key_env,
            self.vps.tailscale_api_key_env,
            self.binarylane.api_token_env,
            self.alerts.telegram.bot_token_env,
            self.alerts.email.password_env,
            *self.hermes_secrets.env_mapping.values(),
        ]
        if self.api.enabled:
            env_refs.append(self.api.bearer_token_env)
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
