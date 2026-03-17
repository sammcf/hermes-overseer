# Plan: Group Chat Support for Overseer Telegram Bot

## Goal
Enable the overseer bot to function in a multi-user group chat alongside the existing DM, with tier-based alert routing.

## Changes

### 1. Config (`src/overseer/config.py`)
- Rename `chat_id: str` → `dm_chat_id: str` in `TelegramConfig`
- Add `group_chat_id: str | None = None` (optional, backwards-compatible)
- Add helper methods:
  - `command_chat_ids` → set of chat IDs that can issue commands (both DM and group)
  - `alert_chat_ids(tier)` → which chats to send alerts to based on tier

### 2. Alert routing (`src/overseer/alert/telegram.py`)
- `send_alert()` currently sends to `config.chat_id` once
- Change to send to `config.alert_chat_ids(tier)` — iterating over the returned set
- YELLOW/ORANGE → group only (if configured, else DM fallback)
- RED → both group and DM

### 3. Heartbeat (`src/overseer/heartbeat/pulse.py` + `__main__.py`)
- `send_pulse()` signature unchanged (takes explicit `chat_id`)
- In `__main__.py`, send heartbeat to group chat if configured, else DM

### 4. Bot command auth (`src/overseer/bot/commands.py`)
- `execute_command()` checks `cmd.chat_id` against the set of allowed chat IDs (both DM and group)
- Command responses still go to `cmd.chat_id` (reply where the command came from)

### 5. Example config (`config/overseer.example.yaml`)
- Update to show both `dm_chat_id` and `group_chat_id`

### 6. Tests (`tests/test_bot/test_commands.py` + new test coverage)
- Update existing tests for renamed field
- Add test: command from group chat ID is accepted
- Add test: command from unauthorised chat is still rejected
- Add test: alert routing sends to correct chats per tier

### 7. Conftest / fixtures
- Update `example_config` fixture if the example YAML changes
