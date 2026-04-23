"""
Unit tests for storage.py
Tests file operations, rotation logic, CSV format correctness
"""

import pytest
import tempfile
import shutil
import os
import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.storage import Storage
from app.models import Result, DomainStatus, FailureType


class TestStorage:
    """Tests for Storage class."""

    @pytest.fixture
    def temp_storage(self):
        temp_dir = tempfile.mkdtemp()
        storage = Storage(data_dir=temp_dir, retention_days=7)
        yield storage
        shutil.rmtree(temp_dir)

    @pytest.fixture
    def sample_result(self):
        return Result(
            timestamp=datetime.now(timezone.utc).isoformat(),
            site_id="test",
            domain_id="test.site",
            domain_status=DomainStatus.UP,
            http_status=200,
            latency_ms=100,
            failure_type=None
        )

    def test_directories_created(self, temp_storage):
        assert temp_storage.live_dir.exists()
        assert temp_storage.archive_dir.exists()

    def test_append_csv_creates_file(self, temp_storage, sample_result):
        temp_storage.append_csv(sample_result)

        domain_file = temp_storage.live_dir / "test" / "site.log"
        assert domain_file.exists()

    def test_append_csv_writes_headers(self, temp_storage, sample_result):
        temp_storage.append_csv(sample_result)

        domain_file = temp_storage.live_dir / "test" / "site.log"
        with open(domain_file, 'r') as f:
            reader = csv.reader(f)
            headers = next(reader)
            assert headers == [
                "timestamp", "site_id", "domain_id", "domain_status",
                "http_status", "latency_ms", "failure_type"
            ]

    def test_append_csv_writes_data(self, temp_storage, sample_result):
        temp_storage.append_csv(sample_result)

        domain_file = temp_storage.live_dir / "test" / "site.log"
        with open(domain_file, 'r') as f:
            reader = csv.reader(f)
            next(reader)  # Skip headers
            row = next(reader)
            assert row[1] == "test"
            assert row[2] == "test.site"
            assert row[3] == "UP"

    def test_append_csv_appends_multiple(self, temp_storage, sample_result):
        temp_storage.append_csv(sample_result)

        result2 = Result(
            timestamp=(datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(),
            site_id="test",
            domain_id="test.site",
            domain_status=DomainStatus.UP,
            http_status=200,
            latency_ms=150,
            failure_type=None
        )
        temp_storage.append_csv(result2)

        domain_file = temp_storage.live_dir / "test" / "site.log"
        with open(domain_file, 'r') as f:
            reader = csv.reader(f)
            next(reader)  # Skip headers
            rows = list(reader)
            assert len(rows) == 2

    def test_read_domain_results(self, temp_storage, sample_result):
        temp_storage.append_csv(sample_result)

        results = temp_storage.read_domain_results("test", "test.site", hours=4)

        assert len(results) == 1
        assert results[0].domain_status == DomainStatus.UP

    def test_read_domain_results_filters_old_data(self, temp_storage):
        old_result = Result(
            timestamp=(datetime.now(timezone.utc) - timedelta(hours=5)).isoformat(),
            site_id="test",
            domain_id="test.site",
            domain_status=DomainStatus.UP,
            http_status=200,
            latency_ms=100,
            failure_type=None
        )
        temp_storage.append_csv(old_result)

        results = temp_storage.read_domain_results("test", "test.site", hours=4)

        assert len(results) == 0

    def test_read_domain_results_file_not_found(self, temp_storage):
        results = temp_storage.read_domain_results("nonexistent", "site", hours=4)
        assert results == []

    def test_read_site_results(self, temp_storage):
        result1 = Result(
            timestamp=datetime.now(timezone.utc).isoformat(),
            site_id="test",
            domain_id="test.domain1",
            domain_status=DomainStatus.UP,
            http_status=200,
            latency_ms=100,
            failure_type=None
        )
        result2 = Result(
            timestamp=datetime.now(timezone.utc).isoformat(),
            site_id="test",
            domain_id="test.domain2",
            domain_status=DomainStatus.DOWN,
            http_status=500,
            latency_ms=200,
            failure_type=FailureType.UNKNOWN
        )
        temp_storage.append_csv(result1)
        temp_storage.append_csv(result2)

        results = temp_storage.read_site_results("test", hours=4)

        assert "test.domain1" in results
        assert "test.domain2" in results
        assert len(results["test.domain1"]) == 1
        assert len(results["test.domain2"]) == 1

    def test_read_site_results_site_not_found(self, temp_storage):
        results = temp_storage.read_site_results("nonexistent", hours=4)
        assert results == {}

    def test_rotate_archives_files(self, temp_storage, sample_result):
        temp_storage.append_csv(sample_result)

        current_month = datetime.now(timezone.utc).strftime("%Y-%m")
        temp_storage.rotate()

        # Check archive was created
        archive_file = temp_storage.archive_dir / current_month / "test" / "site.log"
        assert archive_file.exists()

        # Check live file was truncated (only headers remain)
        live_file = temp_storage.live_dir / "test" / "site.log"
        with open(live_file, 'r') as f:
            reader = csv.reader(f)
            rows = list(reader)
            assert len(rows) == 1  # Only headers

    def test_cleanup_removes_old_archives(self, temp_storage):
        # Create old archive directory (older than retention period)
        old_date = (datetime.now(timezone.utc) - timedelta(days=60)).strftime("%Y-%m")
        old_archive_dir = temp_storage.archive_dir / old_date
        old_archive_dir.mkdir(parents=True)

        # Create a file in the old archive
        (old_archive_dir / "test.log").touch()

        temp_storage.cleanup()

        assert not old_archive_dir.exists()

    def test_cleanup_keeps_recent_archives(self, temp_storage):
        """Test that recent archives are kept."""
        current_month = datetime.now(timezone.utc).strftime("%Y-%m")
        recent_archive = temp_storage.archive_dir / current_month / "test" / "site.log"
        recent_archive.parent.mkdir(parents=True)
        recent_archive.touch()

        temp_storage.cleanup()

        assert recent_archive.exists()

    def test_rotate_appends_to_existing_archive(self, temp_storage, sample_result):
        """Test that rotation appends to archive instead of overwriting."""
        current_month = datetime.now(timezone.utc).strftime("%Y-%m")

        # First rotation - initial data
        temp_storage.append_csv(sample_result)
        temp_storage.rotate()

        # Second rotation - add more data
        result2 = Result(
            timestamp=(datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(),
            site_id="test",
            domain_id="test.site",
            domain_status=DomainStatus.DOWN,
            http_status=500,
            latency_ms=200,
            failure_type=FailureType.HTTP_ERROR
        )
        temp_storage.append_csv(result2)
        temp_storage.rotate()

        # Check archive contains both data rows (not overwritten)
        archive_file = temp_storage.archive_dir / current_month / "test" / "site.log"
        with open(archive_file, 'r') as f:
            reader = csv.reader(f)
            rows = list(reader)

        # Should have header + 2 data rows (not 1 header + 1 data from just second rotation)
        assert len(rows) == 3, f"Expected 3 rows (1 header + 2 data), got {len(rows)}"
        assert rows[0][0] == "timestamp"  # Header
        assert rows[1][3] == "UP"  # First rotation data
        assert rows[2][3] == "DOWN"  # Second rotation data

    def test_rotate_does_not_duplicate_headers(self, temp_storage, sample_result):
        """Test that headers are not duplicated on multiple rotations."""
        current_month = datetime.now(timezone.utc).strftime("%Y-%m")

        # Rotate twice
        temp_storage.append_csv(sample_result)
        temp_storage.rotate()
        temp_storage.rotate()  # Rotate again with empty live file

        archive_file = temp_storage.archive_dir / current_month / "test" / "site.log"
        with open(archive_file, 'r') as f:
            reader = csv.reader(f)
            rows = list(reader)

        # Count headers - should only be one
        header_count = sum(1 for row in rows if row[0] == "timestamp")
        assert header_count == 1, f"Expected 1 header row, found {header_count}"

    def test_rotate_creates_archive_with_headers_if_not_exists(self, temp_storage, sample_result):
        """Test that first rotation creates archive with headers."""
        current_month = datetime.now(timezone.utc).strftime("%Y-%m")

        temp_storage.append_csv(sample_result)
        temp_storage.rotate()

        archive_file = temp_storage.archive_dir / current_month / "test" / "site.log"
        with open(archive_file, 'r') as f:
            reader = csv.reader(f)
            rows = list(reader)

        # First row should be headers
        assert rows[0] == [
            "timestamp", "site_id", "domain_id", "domain_status",
            "http_status", "latency_ms", "failure_type"
        ]
        # Second row should be data
        assert rows[1][2] == "test.site"

    def test_rotate_preserves_live_data_on_archive_error(self, temp_storage, sample_result):
        """Test that live data is preserved if archive operation fails."""
        temp_storage.append_csv(sample_result)

        # Pre-create the full directory structure
        current_month = datetime.now(timezone.utc).strftime("%Y-%m")
        today_archive_dir = temp_storage.archive_dir / current_month
        site_archive_dir = today_archive_dir / "test"
        site_archive_dir.mkdir(parents=True)

        # Create the archive file and make it read-only
        archive_file = site_archive_dir / "site.log"
        archive_file.touch()
        archive_file.chmod(0o444)
        # Also make directory read-only so temp file can't be created
        site_archive_dir.chmod(0o555)

        try:
            # This should fail when trying to write to archive
            temp_storage.rotate()

            # Live file should still have the data (not truncated)
            live_file = temp_storage.live_dir / "test" / "site.log"
            with open(live_file, 'r') as f:
                reader = csv.reader(f)
                rows = list(reader)

            # Should still have header + data row (not truncated to just headers)
            assert len(rows) == 2, f"Expected 2 rows (header + data), got {len(rows)}"
            assert rows[1][3] == "UP"  # Data preserved
        finally:
            # Restore permissions for cleanup
            site_archive_dir.chmod(0o755)
            if archive_file.exists():
                archive_file.chmod(0o644)

    def test_rotate_skips_empty_files(self, temp_storage):
        """Test that rotation skips files with no data rows."""
        # Create a file with only headers (no data)
        live_file = temp_storage.live_dir / "test" / "site.log"
        live_file.parent.mkdir(parents=True, exist_ok=True)
        with open(live_file, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "site_id", "domain_id", "domain_status",
                           "http_status", "latency_ms", "failure_type"])

        current_month = datetime.now(timezone.utc).strftime("%Y-%m")
        temp_storage.rotate()

        # Archive should not exist (nothing to archive)
        archive_file = temp_storage.archive_dir / current_month / "test" / "site.log"
        assert not archive_file.exists()

        # Live file should still exist with just headers
        assert live_file.exists()
        with open(live_file, 'r') as f:
            reader = csv.reader(f)
            rows = list(reader)
        assert len(rows) == 1  # Just headers

    def test_rotate_atomic_write_prevents_corruption(self, temp_storage, sample_result):
        """Test that archive writes are atomic (temp file then rename)."""
        current_month = datetime.now(timezone.utc).strftime("%Y-%m")

        # First rotation - create initial archive
        temp_storage.append_csv(sample_result)
        temp_storage.rotate()

        archive_file = temp_storage.archive_dir / current_month / "test" / "site.log"

        # Add more data and rotate again
        result2 = Result(
            timestamp=(datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(),
            site_id="test",
            domain_id="test.site",
            domain_status=DomainStatus.DOWN,
            http_status=500,
            latency_ms=200,
            failure_type=FailureType.HTTP_ERROR
        )
        temp_storage.append_csv(result2)
        temp_storage.rotate()

        # Verify archive is not corrupted (no temp files, correct row count)
        temp_file = archive_file.with_suffix('.tmp')
        assert not temp_file.exists(), "Temp file should not exist after successful rotation"

        with open(archive_file, 'r') as f:
            reader = csv.reader(f)
            rows = list(reader)

        assert len(rows) == 3  # header + 2 data rows
        assert rows[1][3] == "UP"  # First data row intact
        assert rows[2][3] == "DOWN"  # Second data row intact

    def test_read_archive_index_returns_empty_when_missing(self, temp_storage):
        """Test that read_archive_index returns empty dict when file doesn't exist."""
        index = temp_storage.read_archive_index()
        assert index == {}

    def test_read_archive_index_parses_existing_file(self, temp_storage):
        """Test that read_archive_index correctly parses existing archive_index.json."""
        index_path = temp_storage.get_archive_index_path()
        test_data = {"site1": ["2026-04", "2026-03"], "site2": ["2026-04"]}
        with open(index_path, 'w') as f:
            import json
            json.dump(test_data, f)

        index = temp_storage.read_archive_index()
        assert index == test_data

    def test_read_archive_index_handles_corrupt_json(self, temp_storage):
        """Test that read_archive_index handles corrupt JSON gracefully."""
        index_path = temp_storage.get_archive_index_path()
        with open(index_path, 'w') as f:
            f.write("not valid json {{{")

        index = temp_storage.read_archive_index()
        assert index == {}

    def test_update_archive_index_creates_new_entry(self, temp_storage):
        """Test that update_archive_index creates new site entry."""
        temp_storage.update_archive_index("site1", "2026-04")

        index = temp_storage.read_archive_index()
        assert "site1" in index
        assert index["site1"] == ["2026-04"]

    def test_update_archive_index_appends_new_month(self, temp_storage):
        """Test that update_archive_index appends new month to existing site."""
        temp_storage.update_archive_index("site1", "2026-04")
        temp_storage.update_archive_index("site1", "2026-03")

        index = temp_storage.read_archive_index()
        assert index["site1"] == ["2026-04", "2026-03"]  # Sorted reverse chronologically

    def test_update_archive_index_skips_duplicate_month(self, temp_storage):
        """Test that update_archive_index skips duplicate month entries."""
        temp_storage.update_archive_index("site1", "2026-04")
        temp_storage.update_archive_index("site1", "2026-04")  # Duplicate

        index = temp_storage.read_archive_index()
        assert index["site1"] == ["2026-04"]  # Only one entry

    def test_update_archive_index_sorts_months_reverse(self, temp_storage):
        """Test that update_archive_index sorts months in reverse chronological order."""
        temp_storage.update_archive_index("site1", "2026-02")
        temp_storage.update_archive_index("site1", "2026-04")
        temp_storage.update_archive_index("site1", "2026-03")

        index = temp_storage.read_archive_index()
        assert index["site1"] == ["2026-04", "2026-03", "2026-02"]

    def test_update_archive_index_multiple_sites(self, temp_storage):
        """Test that update_archive_index handles multiple sites independently."""
        temp_storage.update_archive_index("site1", "2026-04")
        temp_storage.update_archive_index("site2", "2026-03")
        temp_storage.update_archive_index("site1", "2026-03")

        index = temp_storage.read_archive_index()
        assert index["site1"] == ["2026-04", "2026-03"]
        assert index["site2"] == ["2026-03"]

    def test_update_archive_index_handles_write_error(self, temp_storage):
        """Test that update_archive_index handles write errors gracefully."""
        # Make data directory read-only
        temp_storage.data_dir.chmod(0o555)

        try:
            # Should not raise exception
            temp_storage.update_archive_index("site1", "2026-04")

            # Index file should not exist
            index_path = temp_storage.get_archive_index_path()
            assert not index_path.exists()
        finally:
            # Restore permissions for cleanup
            temp_storage.data_dir.chmod(0o755)

    def test_rotate_updates_archive_index(self, temp_storage, sample_result):
        """Test that rotate() calls update_archive_index for rotated sites."""
        temp_storage.append_csv(sample_result)
        current_month = datetime.now(timezone.utc).strftime("%Y-%m")

        temp_storage.rotate()

        index = temp_storage.read_archive_index()
        assert "test" in index
        assert current_month in index["test"]

    def test_get_archive_index_path(self, temp_storage):
        """Test that get_archive_index_path returns correct path."""
        path = temp_storage.get_archive_index_path()
        assert path == temp_storage.data_dir / "archive_index.json"
        assert path.parent == temp_storage.data_dir
