"""Borg create --stats and info --json parsing."""

from harvestwind_backup.borg import parse_create_stats, parse_repo_info_json


def test_parse_create_stats_this_archive_row() -> None:
    sample = """
Number of files: 42,123
                       Original size      Compressed size    Deduplicated size
This archive:               1.50 GB                1.20 GB              800.50 MB
All archives:              66.62 GB               45.15 GB                8.87 GB
"""
    stats = parse_create_stats(sample, "2026-05-22_21-56-04", 120.0)
    assert stats.name == "2026-05-22_21-56-04"
    assert stats.num_files == 42123
    assert stats.size_orig == int(1.5 * 1024**3)
    assert stats.size_compressed == int(1.2 * 1024**3)
    assert stats.size_deduplicated == int(800.5 * 1024**2)
    assert stats.duration == 120.0


def test_parse_create_stats_plain_bytes() -> None:
    sample = """
Number of files: 100
Original size 1234567890
Compressed size 987654321
Deduplicated size 123456789
"""
    stats = parse_create_stats(sample, "archive-1", 5.0)
    assert stats.size_orig == 1234567890
    assert stats.size_compressed == 987654321
    assert stats.size_deduplicated == 123456789
    assert stats.num_files == 100


def test_parse_repo_info_json_archives_list() -> None:
    data = {
        "archives": [{"name": "a"}, {"name": "b"}],
        "cache": {"stats": {"unique": {"total_size": 9500000000}}},
    }
    info = parse_repo_info_json(data)
    assert info["total_archives"] == 2
    assert info["total_size"] == 9500000000


def test_parse_repo_info_json_archives_count_dict() -> None:
    data = {
        "archives": {"count": 17},
        "cache": {"stats": {"total_size": 1000, "unique": {"total_size": 900}}},
    }
    info = parse_repo_info_json(data)
    assert info["total_archives"] == 17
    assert info["total_size"] == 900
