# harvestwind-backup

Docker volume + rsync **client**, and Borg + rclone **server**, with ntfy notifications and correct transfer metrics.

Replaces the legacy `Server_Backup_Dev` project (not modified in place). Deployed by the **platform** Ansible role `harvestwind_backup`, triggered from this repo on push to `main`.

## Host assignment

In `platform/inventory/hosts.yml`:

- **`backup_clients`** — run `harvestwind-backup-client` (Docker hosts with compose apps)
- **`backup_servers`** — run `harvestwind-backup-server` (Borg + B2 sync)
- A host can be in both groups with `backup_role: both` in host_vars (uncommon)

Group vars set `backup_role` automatically:

| Group | `backup_role` |
|-------|----------------|
| `backup_clients` | `client` |
| `backup_servers` | `server` |

## Per-host configuration

Define in `platform/inventory/host_vars/<host>.yml`:

```yaml
backup_client_config:
  apps_root: /home/jwp/docker-services
  rsync:
    server_destination:
      type: ssh
      host: debian-04
      user: jwp
      remote_path: /srv/backups/rsync
      auth:
        key_path: /etc/harvestwind-backup/ssh/backup_key
```

```yaml
backup_server_config:
  borg:
    repo_path: /srv/backups/borg_repo
    backup_path: /srv/backups/rsync
    compression: lz4
  b2:
    bucket: your-bucket
    path: backups
```

Secrets in Ansible vault (referenced by `roles/harvestwind_backup/templates/environment.j2`):

- `ntfy_token`
- `borg_passphrase` (server only)

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

## Deploy

**Automatic:** push to `main` → `.github/workflows/deploy.yml` runs on self-hosted runner → `platform/playbooks/deploy_backup.yml`.

**Manual:** Platform workflow *Deploy Backup Agent*, or:

```bash
cd platform
ansible-playbook playbooks/deploy_backup.yml \
  -i inventory/hosts.yml \
  -e backup_repo_path=$HOME/HarvestWind/selfhosted/harvestwind-backup
```

## Compose labels

Same as legacy system:

- `unifybackup.volumes.exclude: "true"`
- `unifybackup.app.exclude: "true"`

## Metrics fixes (vs legacy)

- Parses rsync `--stats` from stdout **and** stderr
- No `-h` on stats lines (numeric bytes)
- Uses **Total transferred file size**
- Falls back to `bytes / wall_time` for throughput
- Overall throughput uses **run wall clock**, not sum of per-app durations
