# WU-001: State Preservation & Local Backend Switch

## Status: Complete

## Motivation

Hermes-agent runs in a Docker terminal backend which loses all installed Python
packages between sessions, makes OAuth token paths awkward (host vs container
mount), and adds friction without meaningful security benefit — the VPS is
ephemeral cattle that the overseer can nuke and rebuild in 4 minutes.

Separately, the overseer currently preserves only config and secrets across
rebuilds. A rebuild today loses all session history, memories, SOUL.md persona,
Google OAuth tokens, installed skills, pairing state, and conversation logs.
From hermes' perspective a rebuild should be a non-event — zero loss of
continuity.

## Changes

### 1. Switch terminal backend: docker -> local

**Canonical config** (`config/hermes-canonical.yaml`):
```yaml
terminal:
  backend: local
  timeout: 180
```

Remove `container_*` and `docker_image` fields (no longer relevant).

**Cloud-init template** (`cloud-init/hermes-vps.yaml`):
- Remove `docker pull ${docker_image}` step (no longer needed at boot).
- Add Google Workspace Python deps to venv install:
  `venv/bin/pip install google-api-python-client google-auth-oauthlib google-auth-httplib2`
- Docker stays installed (hermes may still use it for other things), but it's
  no longer the terminal backend.

### 2. Periodic state snapshot (every 4 hours)

New module: `src/overseer/backup/snapshot.py`

Core function:
```python
def take_snapshot(hostname, user, hermes_home, backup_dir) -> Result[str]:
```

**Strategy:** SSH into the VPS, create a tar.gz archive of `~/.hermes/`
excluding code (`hermes-agent/`, `sandboxes/`), then rsync that single file
down to the overseer's local backup dir.

```bash
# On VPS (via SSH):
tar czf /tmp/hermes-state-{timestamp}.tar.gz \
    -C /home/hermes \
    --exclude='.hermes/hermes-agent' \
    --exclude='.hermes/sandboxes' \
    --exclude='.hermes/bin' \
    --exclude='.hermes/image_cache' \
    --exclude='.hermes/document_cache' \
    .hermes/

# Then rsync the archive down:
rsync -az hermes@hermes-vps:/tmp/hermes-state-{timestamp}.tar.gz {backup_dir}/
```

**Retention:** Keep the last N snapshots (configurable, default 24 = 4 days at
4-hour intervals). Oldest beyond N are deleted.

**Scheduling:** New `backup_interval_seconds` config field on `OverseerConfig`
(default: `14400` = 4 hours). The main loop checks if the interval has elapsed
and triggers a snapshot. Uses the same `time.monotonic()` deadline pattern as
heartbeat/canary.

**State to capture** (~14MB currently, compresses well):

| Path | Contents |
|------|----------|
| `state.db`, `state.db-wal`, `state.db-shm` | SQLite session DB — all messages, FTS5 |
| `sessions/` | Per-session JSONL transcripts + metadata |
| `memories/` | MEMORY.md, USER.md |
| `SOUL.md` | Persona/system prompt |
| `auth.json` | Provider OAuth tokens |
| `config.yaml` | Runtime config |
| `.env` | Secrets (already overseer-managed) |
| `google_token.json` | Google OAuth token (refresh_token survives) |
| `google_client_secret.json` | Google OAuth client credentials |
| `skills/` | Installed skills + hub metadata |
| `pairing/` | Telegram user approval state |
| `cron/` | Job definitions + output |
| `logs/` | Error and gateway logs |
| `gateway_state.json` | Runtime state |
| `.hermes_history` | CLI history |

### 3. Google OAuth as overseer-managed secrets

Add to `HermesSecretsConfig` a new field for file-based secrets (not env vars):

```python
class HermesSecretsConfig(BaseModel, frozen=True):
    env_mapping: dict[str, str] = { ... }   # existing
    file_secrets: dict[str, str] = {
        "google_token.json": "google_token.json",
        "google_client_secret.json": "google_client_secret.json",
    }
```

These map `{hermes_home}/{filename}` -> `{overseer_secrets_dir}/{filename}`.

**Bootstrap:** Copy the two files from the docker sandbox on the current VPS
into the overseer's secrets dir (`~/.config/hermes-overseer/`). These become
the source of truth for all future rebuilds.

**On rebuild:** The provisioner pushes these files via `push_file_content()`
alongside `.env` and `config.yaml`.

**On snapshot:** These files are captured in the tar.gz archive. If a new OAuth
flow happens on the VPS (token refresh, re-auth), the next snapshot captures
the updated token. Overseer's local copy is updated from the latest snapshot.

### 4. State restore on rebuild

Extend `provision/provisioner.py` with a new step between cloud-init
completion and service start:

```
Step 5b: Wait for cloud-init (existing)
Step 5c: Restore state from latest snapshot   <-- NEW
Step 6:  Build hermes .env (existing)
...
```

**Restore logic:**
1. Find the latest snapshot archive in `{backup_dir}/`
2. rsync it up to VPS `/tmp/`
3. SSH: `tar xzf /tmp/hermes-state-{latest}.tar.gz -C /home/hermes/`
4. SSH: `chown -R hermes:hermes /home/hermes/.hermes/`

This restores sessions, memories, SOUL.md, skills, pairing, logs — everything.
Then the existing steps overwrite `.env` and `config.yaml` with the
authoritative overseer copies (so any drift in the snapshot is corrected).

If no snapshot exists (first-ever rebuild), this step is skipped gracefully.

### 5. Config additions

```yaml
# overseer.yaml
overseer:
  backup_interval_seconds: 14400   # 4 hours
  backup_retention_count: 24       # keep ~4 days
  backup_dir: "~/.local/share/hermes-overseer/backups"
```

### 6. Overseer monitoring additions

Add to `WatchedFilesConfig`:
```yaml
watched_files:
  orange_on_any_diff:
    - ".env"
    - "config.yaml"
    - "google_token.json"          # new
    - "google_client_secret.json"  # new
```

---

## Files to modify/create

| File | Action |
|------|--------|
| `src/overseer/backup/__init__.py` | New: package init |
| `src/overseer/backup/snapshot.py` | New: take_snapshot, restore_snapshot, prune |
| `src/overseer/provision/provisioner.py` | Add state restore step |
| `src/overseer/config.py` | Add backup config fields, file_secrets |
| `src/overseer/__main__.py` | Add backup interval check to main loop |
| `config/overseer.example.yaml` | Add backup config, watched files |
| `config/hermes-canonical.yaml` | Switch terminal.backend to local |
| `cloud-init/hermes-vps.yaml` | Add google deps, remove docker pull |
| `tests/test_backup/` | New: snapshot, restore, prune tests |
| `tests/test_provision/test_provisioner.py` | Update for restore step |

---

## Future: Tarsnap incremental backups

The tar.gz snapshot approach is good enough for now — small state (<20MB),
simple, no external dependencies. When we want incremental encrypted backups
with deduplication:

- Tarsnap on the overseer host (not the VPS — overseer owns backups)
- Nightly `tarsnap -c` of the latest snapshot + any additional files
- Tarsnap's built-in deduplication handles the incremental efficiency
- Tarsnap key stored in overseer's secrets dir
- This replaces the local retention/pruning — tarsnap handles it

Not implementing now. Revisit when state grows beyond ~100MB or when we want
offsite backup guarantees.

---

## Verification

1. `uv run pytest` — all tests pass (325 passed)
2. `uv run mypy src/` — clean (0 issues in 35 files)
3. Manual: rebuild VPS, verify session history survives
4. Manual: send Telegram message post-rebuild, verify hermes remembers context
5. Manual: verify Google OAuth works post-rebuild without re-auth

---

## Work Log

### 2026-03-18: Final cleanup and audit

**Audit findings** (all plan items implemented in prior sessions):
- §1 Terminal backend → local: done (hermes-canonical.yaml, cloud-init)
- §2 Periodic snapshot: done (backup/snapshot.py, main loop scheduling)
- §3 Google OAuth secrets: done (HermesSecretsConfig.file_secrets, provisioner Step 8b)
- §4 State restore on rebuild: done (provisioner Step 5c)
- §5 Config additions: done (OverseerConfig backup fields)
- §6 Monitoring additions: done in example config, code defaults lagged

**Cleanup performed:**
- Removed dead `docker_image` field from `VpsConfig` (config.py)
- Removed `docker_image` from `_gather_cloud_init_variables()` (provisioner.py)
- Removed `docker_image` from cloud-init template comments (both copies)
- Removed `docker_image` from example config (overseer.example.yaml)
- Removed `test_docker_image_default` test and `docker_image` from builder test fixtures
- Aligned `WatchedFilesConfig` code defaults to include `google_token.json` and `google_client_secret.json` in `orange_on_any_diff`
- Added `TestProvisionFileSecrets` test class (3 tests): mode 0600 verification, missing file graceful skip, push failure best-effort

**False positive dismissed:** Auditor flagged `restore_snapshot` chown only targeting `.hermes/` not extra_paths. Non-issue: tar runs as hermes user via SSH, so extracted files are already owned by hermes:hermes. The explicit chown is defense-in-depth.
