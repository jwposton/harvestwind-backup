from harvestwind_backup.metrics import (
    TransferTotals,
    format_bytes,
    format_duration,
    format_throughput,
    parse_byte_size,
    parse_rsync_stats,
)


def test_parse_byte_size_human():
    assert parse_byte_size("1.5", "GB") == int(1.5 * 1024**3)
    assert parse_byte_size("1024", "B") == 1024


def test_format_bytes_and_duration():
    assert format_bytes(1536) == "1.50 KB"
    assert format_duration(3661) == "01:01:01"
    assert format_throughput(2048) == "2.00 KB/s"


def test_parse_rsync_stats_from_sample():
    sample = """
Number of regular files transferred: 42
Total file size: 10,000,000,000 bytes
Total transferred file size: 1,500,000,000 bytes
sent 1,500,100,000 bytes  received 1,234 bytes  12,345,678.00 bytes/sec
total size is 10,000,000,000  speedup is 6.67
"""
    stats = parse_rsync_stats(sample, wall_seconds=120.0)
    assert stats.files_transferred == 42
    assert stats.bytes_transferred == 1_500_000_000
    assert stats.bytes_per_sec == 12_345_678


def test_parse_rsync_stats_fallback_speed():
    sample = """
Number of regular files transferred: 1
Total transferred file size: 1,000 bytes
"""
    stats = parse_rsync_stats(sample, wall_seconds=10.0)
    assert stats.bytes_transferred == 1000
    assert stats.bytes_per_sec == 100.0


def test_transfer_totals_throughput():
    totals = TransferTotals(files=10, bytes=5000)
    assert totals.throughput(25.0) == 200.0
