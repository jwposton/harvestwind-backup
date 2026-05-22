# harvestwind-backup

Docker volume + rsync **client**, and Borg + rclone **server**, with ntfy notifications and correct transfer metrics.

Deployed by the **platform** Ansible role `harvestwind_backup` (configs and secrets live in that repo, not here).

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

**Automatic:** push to `main` → workflow in this repo can trigger `platform/playbooks/deploy_backup.yml`.

**Manual:**

```bash
cd platform
ansible-playbook playbooks/deploy_backup.yml -i inventory/hosts.yml
# optional pin: -e backup_git_ref=v0.1.0
```

## Compose labels

- `unifybackup.volumes.exclude: "true"`
- `unifybackup.app.exclude: "true"`

## Metrics fixes (vs legacy)

- Parses rsync `--stats` from stdout **and** stderr
- No `-h` on stats lines (numeric bytes)
- Uses **Total transferred file size**
- Falls back to `bytes / wall_time` for throughput
- Overall throughput uses **run wall clock**, not sum of per-app durations
