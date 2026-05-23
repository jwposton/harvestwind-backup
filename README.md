# harvestwind-backup

Docker volume + rsync **client**, and Borg + rclone **server**, with ntfy notifications and correct transfer metrics.

## Backup pipeline (server)

1. **Client** — rsync app trees to `borg.backup_path` on the backup server (e.g. `/srv/backups/nixihost/rsync`).
2. **Server** — `borg create` archives that tree into `borg.repo_path` (local versioned repo).
3. **Server** — `rclone sync` then `rclone check --one-way` on `borg.repo_path` → B2 (legacy parity: mirror + verify, retries with backoff).

Deployed by the **platform** Ansible role `harvestwind_backup` (configs and secrets live in that repo, not here).

**Production ops:** [BACKUP_OPERATIONS.md](https://github.com/jwposton/platform/blob/main/docs/BACKUP_OPERATIONS.md). **Rebuild Borg + B2 on NAS:** [BORG_B2_SETUP.md](https://github.com/jwposton/platform/blob/main/docs/BORG_B2_SETUP.md). **Fleet deploy:** [FLEET_GUIDE.md](https://github.com/jwposton/platform/blob/main/docs/FLEET_GUIDE.md).

## Host assignment

In your Ansible inventory (e.g. `platform/inventory/hosts.yml`):

- **`backup_clients`** — run `harvestwind-backup-client` (Docker hosts with compose apps)
- **`backup_servers`** — run `harvestwind-backup-server` (Borg + cloud sync)
- A host can be in both groups with `backup_role: both` in host_vars (uncommon)

Group vars set `backup_role` automatically:

| Group | `backup_role` |
|-------|----------------|
| `backup_clients` | `client` |
| `backup_servers` | `server` |

## Configuration (Ansible → host_vars)

Deploy does **not** copy this repo to servers. Ansible:

1. `pip install` from Git (`backup_pip_spec` in platform `group_vars/backup.yml`)
2. Renders `/etc/harvestwind-backup/client.yml` or `server-<profile>.yml` from **host_vars**

Example client snippet (replace hosts, paths, and users with yours):

```yaml
backup_client_config:
  apps_root: /home/backup-user/docker-services
  volumes: { max_backups: 1, backup_dir: vol_bkup, uid: 1000, gid: 1000 }
  rsync:
    server_destination:
      type: ssh
      host: backup-nas.example
      user: backup-user
      remote_path: /srv/backups/site-a/rsync
      auth: { key_path: /etc/harvestwind-backup/ssh/id_ed25519 }
      options: { compress: true, delete: true, bwlimit: 30000 }
```

Example server profiles on one NAS host:

```yaml
backup_server_profiles:
  - name: site-a
    config: { server: {...}, borg: {...}, b2: { bucket: site-a-bucket, path: backups } }
  - name: site-b
    config: { server: {...}, borg: {...}, b2: { bucket: site-b-bucket, path: backups } }
```

Secrets stay in Ansible vault → `/etc/harvestwind-backup/environment` (mode `0640`, service user readable).

Systemd runs as a non-root user with `SupplementaryGroups=docker` on clients.

### Client config keys (common)

| Key | Purpose |
|-----|---------|
| `apps_root` | Parent of compose app dirs (e.g. `/home/jwp/docker-services`) |
| `rsync.staging_lock.enabled` | SSH lock on NAS during rsync (default true when SSH dest configured) |
| `rsync.verify` | Post-sync dry-run: `enabled`, `skip_if_unchanged`, optional `checksum` |
| `rsync.server_destination` | SSH rsync target (`remote_path`, `auth.key_path`, `options`) |

### Server config keys (per profile)

| Key | Purpose |
|-----|---------|
| `server.staging_lock_wait_timeout` | Seconds to wait for client lock before Borg (e.g. `10800`) |
| `server.staging_lock_poll_interval` | Poll interval while waiting (e.g. `30`) |
| `borg.backup_path` | Rsync staging tree |
| `borg.repo_path` | Local Borg repository |

### Serial server units (Ansible role, not this package)

When `backup_server_serial: true` on a NAS host, the platform role installs **`harvestwind-backup-server-all.service`** / **`.timer`** (one daily run, profiles in `backup_server_profiles` order) and **disables** per-profile timers. Per-profile services remain for manual `systemctl start harvestwind-backup-server-<profile>.service`. See [BACKUP_OPERATIONS.md](https://github.com/jwposton/platform/blob/main/docs/BACKUP_OPERATIONS.md).

Vault variables (names only):

- `ntfy_token`
- `borg_passphrase` (server)
- `backup_ssh_private_key` / `backup_ssh_public_key` (if using role SSH deploy)

## Local development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```

```bash
harvestwind-backup-client config/client.example.yml
harvestwind-backup-server config/server.example.yml
```

## Install from GitHub (manual / pyenv)

```bash
pip install "git+https://github.com/jwposton/harvestwind-backup.git@main"
```

## Deploy

Configure `backup_git_repo` in platform, add hosts to `backup_clients` / `backup_servers`, define configs in host_vars.

**GitHub Actions:** run the **Deploy Backup Agent** workflow manually (`workflow_dispatch`) on a self-hosted runner with the platform repo available.

**Manual (local):**

```bash
cd platform
ansible-playbook playbooks/deploy_backup.yml -i inventory/hosts.yml
# optional pin: -e backup_git_ref=v0.1.0
```

## Compose labels

Set on the **top-level** `volumes:` entry (not on `services.*.volumes` mounts):

- `unifybackup.volumes.exclude: "true"` — skip Docker volume tar for that named volume (data may still be in the app tree via rsync).

Skip the **entire stack** (no volume tars, no rsync) with `unifybackup.app.exclude: "true"` on:

- compose `x-labels` / `labels`, or
- any `services.<name>.labels` (one labeled service excludes the whole app folder).

Bind mounts declared under `services` (e.g. `./data:/path`) are backed up via **rsync** of the app directory, not volume tar. Top-level `volumes:` entries with `driver: tmpfs`, `type: bind`, or path-like names (`./foo`) are not tarred.

## Borg retention

After each successful `borg create`, the server runs `borg prune` with GFS-style keep rules (default: 7 daily, 4 weekly, 6 monthly). Configure under `borg.retention` in server config; set `borg.prune: false` or `borg.retention: false` to disable.

## Metrics fixes (vs legacy)

- Parses rsync `--stats` from stdout **and** stderr
- No `-h` on stats lines (numeric bytes)
- Uses **Total transferred file size**
- Falls back to `bytes / wall_time` for throughput
- Overall throughput uses **run wall clock**, not sum of per-app durations
