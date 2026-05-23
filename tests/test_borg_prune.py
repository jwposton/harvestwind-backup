from harvestwind_backup.borg import BorgManager
from harvestwind_backup.config import BorgRetention, ServerConfig


def test_prune_argv_builds_keep_flags():
    manager = BorgManager.__new__(BorgManager)
    manager.repo_path = "/srv/backups/borg_repo"

    argv = manager.prune_argv(BorgRetention(daily=7, weekly=4, monthly=6), lock_timeout=300)

    assert argv[:3] == ["borg", "prune", "--lock-wait=300"]
    assert "--keep-daily=7" in argv
    assert "--keep-weekly=4" in argv
    assert "--keep-monthly=6" in argv
    assert "--list" in argv
    assert "--keep-yearly" not in " ".join(argv)
    assert argv[-1] == "/srv/backups/borg_repo"


def test_prune_argv_includes_yearly_when_set():
    manager = BorgManager.__new__(BorgManager)
    manager.repo_path = "/repo"

    argv = manager.prune_argv(BorgRetention(yearly=2), lock_timeout=60)

    assert "--keep-yearly=2" in argv


def test_borg_config_retention_disabled():
    data = {
        "backup": {
            "borg": {
                "repo_path": "/repo",
                "backup_path": "/data",
                "prune": False,
            },
            "b2": {"bucket": "b", "path": "p"},
        }
    }

    import yaml
    from pathlib import Path
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False) as fh:
        yaml.safe_dump(data, fh)
        path = Path(fh.name)

    cfg = ServerConfig.from_yaml(path)
    assert cfg.borg.retention is None


def test_borg_retention_defaults():
    retention = BorgRetention.from_dict({})
    assert retention.daily == 7
    assert retention.weekly == 4
    assert retention.monthly == 6


def test_parse_prune_deleted_count():
    from harvestwind_backup.borg import parse_prune_deleted_count

    sample = """
Keeping archive (rule: daily #1):        2026-05-18_03-12-36
Pruning:                                   2026-03-02_03-06-35
Would prune:                               2026-02-16_03-05-58
Pruning archive:                           2026-01-05_03-04-51
"""
    assert parse_prune_deleted_count(sample) == 2
