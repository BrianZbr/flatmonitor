"""
Tests for CSV schema migration and backward compatibility.
"""
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
import tempfile
import csv

from app.models import Result, DomainConfig, DomainStatus, FailureType
from app.schema_versions import (
    CSV_SCHEMA_VERSIONS,
    CURRENT_SCHEMA_VERSION,
    get_fields_for_version,
    get_field_changes,
    is_deprecated,
)
from app.migrations import (
    apply_migrations,
    migrate_v1_to_v2,
    get_migration_path,
    MIGRATIONS,
)
from app.storage import Storage


def recent_timestamp():
    """Generate a timestamp from 1 hour ago (within test time windows)."""
    return (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()


# Test data fixtures
@pytest.fixture
def sample_result():
    """Create a sample Result with all fields populated."""
    return Result(
        timestamp=recent_timestamp(),
        site_id="test-site",
        domain_id="test-site.example.com",
        domain_status=DomainStatus.UP,
        http_status=200,
        latency_ms=45,
        failure_type=None,
        protection_type=None
    )


@pytest.fixture
def v1_csv_row():
    """Legacy v1 CSV row (no protection_type field) with recent timestamp."""
    ts = recent_timestamp()
    return [ts, "test-site", "test-site.example.com",
            "UP", "200", "45", ""]


@pytest.fixture
def v2_csv_row():
    """Current v2 CSV row (with protection_type field) with recent timestamp."""
    ts = recent_timestamp()
    return [ts, "test-site", "test-site.example.com",
            "UP", "200", "45", "", ""]


@pytest.fixture
def temp_storage():
    """Create a temporary storage instance for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = Storage(data_dir=tmpdir, retention_days=1)
        yield storage


class TestSchemaVersions:
    """Test schema version registry functions."""

    def test_current_version_is_defined(self):
        """CURRENT_SCHEMA_VERSION must exist in registry."""
        assert CURRENT_SCHEMA_VERSION in CSV_SCHEMA_VERSIONS

    def test_get_fields_for_version_v1(self):
        """v1 should have 7 fields."""
        fields = get_fields_for_version(1)
        assert len(fields) == 7
        assert "protection_type" not in fields

    def test_get_fields_for_version_v2(self):
        """v2 should have 8 fields including protection_type."""
        fields = get_fields_for_version(2)
        assert len(fields) == 8
        assert "protection_type" in fields

    def test_get_field_changes_v1_to_v2(self):
        """v1 to v2 adds protection_type field."""
        changes = get_field_changes(1, 2)
        assert changes["added"] == ["protection_type"]
        assert changes["removed"] == []

    def test_unknown_version_raises_error(self):
        """Unknown schema versions should raise ValueError."""
        with pytest.raises(ValueError):
            get_fields_for_version(999)

    def test_no_versions_are_deprecated(self):
        """Currently no versions should be deprecated."""
        for version in CSV_SCHEMA_VERSIONS:
            assert not is_deprecated(version)


class TestMigrations:
    """Test migration functions."""

    def test_migrate_v1_to_v2_adds_protection_type(self, v1_csv_row):
        """v1 to v2 migration should add empty protection_type field."""
        v1_headers = get_fields_for_version(1)
        result = migrate_v1_to_v2(v1_csv_row.copy(), v1_headers)

        assert len(result) == 8
        assert result[-1] == ""  # protection_type should be empty
        assert result[:-1] == v1_csv_row  # All other fields unchanged

    def test_migrate_short_row(self):
        """Migration should handle rows shorter than expected."""
        ts = recent_timestamp()
        short_row = [ts, "test-site"]  # Only 2 fields
        v1_headers = get_fields_for_version(1)
        result = migrate_v1_to_v2(short_row.copy(), v1_headers)

        assert len(result) == 8  # Should be padded to v2 length

    def test_apply_migrations_no_change_if_current(self, v2_csv_row):
        """If version matches current, no migration should occur."""
        v2_headers = get_fields_for_version(2)
        result = apply_migrations(v2_csv_row.copy(), 2, 2, v2_headers)
        assert result == v2_csv_row

    def test_apply_migrations_v1_to_v2(self, v1_csv_row):
        """Applying migrations from v1 to v2 should work."""
        v1_headers = get_fields_for_version(1)
        result = apply_migrations(v1_csv_row.copy(), 1, 2, v1_headers)

        assert len(result) == len(get_fields_for_version(2))
        assert result[-1] == ""  # protection_type added as empty

    def test_get_migration_path(self):
        """Should return correct migration steps."""
        path = get_migration_path(1, 3)
        assert path == [(1, 2), (2, 3)]

    def test_get_migration_path_no_steps_needed(self):
        """If versions are equal, no steps needed."""
        path = get_migration_path(2, 2)
        assert path == []

    def test_get_migration_path_reverse(self):
        """Cannot migrate backward - should return empty."""
        path = get_migration_path(3, 1)
        assert path == []


class TestResultModelParsing:
    """Test Result model CSV parsing with headers."""

    def test_from_csv_row_with_headers_v2(self, v2_csv_row):
        """Parse v2 row with headers."""
        headers = get_fields_for_version(2)
        result = Result.from_csv_row(v2_csv_row, headers)

        assert result.site_id == "test-site"
        assert result.domain_id == "test-site.example.com"
        assert result.domain_status == DomainStatus.UP
        assert result.http_status == 200
        assert result.latency_ms == 45
        assert result.failure_type is None
        assert result.protection_type is None

    def test_from_csv_row_positional_fallback_v1(self, v1_csv_row):
        """Parse v1 row without headers (positional fallback)."""
        result = Result.from_csv_row(v1_csv_row, headers=None)

        assert result.site_id == "test-site"
        assert result.protection_type is None  # Should default to None

    def test_from_csv_row_with_protection_type(self):
        """Parse row with protection_type populated."""
        ts = recent_timestamp()
        row = [ts, "test-site", "test-site.example.com",
               "PROTECTED", "503", "100", "", "cloudflare"]
        headers = get_fields_for_version(2)
        result = Result.from_csv_row(row, headers)

        assert result.domain_status == DomainStatus.PROTECTED
        assert result.protection_type == "cloudflare"
        assert result.http_status == 503

    def test_from_csv_row_unknown_failure_type(self):
        """Unknown failure types should map to UNKNOWN enum."""
        ts = recent_timestamp()
        row = [ts, "test-site", "test-site.example.com",
               "DOWN", "500", "200", "legacy_error_value", ""]
        headers = get_fields_for_version(2)
        result = Result.from_csv_row(row, headers)

        assert result.failure_type == FailureType.UNKNOWN

    def test_from_csv_row_short_row_with_headers(self):
        """Short rows with headers should use defaults."""
        ts = recent_timestamp()
        row = [ts, "test-site"]  # Missing fields
        headers = get_fields_for_version(2)
        result = Result.from_csv_row(row, headers)

        assert result.site_id == "test-site"
        assert result.domain_status == DomainStatus.UNKNOWN  # Default for missing
        assert result.protection_type is None

    def test_get_csv_headers_matches_model(self):
        """get_csv_headers should return fields matching schema v2."""
        headers = Result.get_csv_headers()
        expected = get_fields_for_version(2)
        assert headers == expected

    def test_get_csv_field_count(self):
        """Field count should match current schema."""
        count = Result.get_csv_field_count()
        assert count == len(get_fields_for_version(2))


class TestStorageVersionDetection:
    """Test Storage class version detection and migration."""

    def test_detect_schema_version_v1_file(self, temp_storage):
        """Detect version 1 from legacy file without comments."""
        domain_path = temp_storage._get_domain_path("test-site", "test-site.example.com")
        domain_path.parent.mkdir(parents=True, exist_ok=True)

        # Write v1-style file (no version comment)
        with open(domain_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(get_fields_for_version(1))  # v1 headers
            ts = recent_timestamp()
            writer.writerow([ts, "test-site",
                           "test-site.example.com", "UP", "200", "45", ""])

        version, headers = temp_storage._detect_schema_version(domain_path)
        assert version == 1  # Should default to 1 for files without version comment
        assert headers == get_fields_for_version(2)  # Should return current headers as default

    def test_detect_schema_version_v2_file(self, temp_storage):
        """Detect version 2 from file with version comment."""
        domain_path = temp_storage._get_domain_path("test-site", "test-site.example.com")
        domain_path.parent.mkdir(parents=True, exist_ok=True)

        # Write v2-style file with version comment
        with open(domain_path, "w", newline="") as f:
            f.write("# version: 2\n")
            f.write(f"# schema: {','.join(get_fields_for_version(2))}\n")
            writer = csv.writer(f)
            writer.writerow(get_fields_for_version(2))
            ts = recent_timestamp()
            writer.writerow([ts, "test-site",
                           "test-site.example.com", "UP", "200", "45", "", ""])

        version, headers = temp_storage._detect_schema_version(domain_path)
        assert version == 2
        assert headers == get_fields_for_version(2)

    def test_read_domain_results_migrates_v1_data(self, temp_storage):
        """Reading v1 file should automatically migrate data."""
        domain_path = temp_storage._get_domain_path("test-site", "test-site.example.com")
        domain_path.parent.mkdir(parents=True, exist_ok=True)

        # Write v1-style file
        with open(domain_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(get_fields_for_version(1))
            ts = recent_timestamp()
            writer.writerow([ts, "test-site",
                           "test-site.example.com", "UP", "200", "45", ""])

        results = temp_storage.read_domain_results("test-site", "test-site.example.com", hours=24)

        assert len(results) == 1
        result = results[0]
        assert result.protection_type is None  # Should have been migrated
        assert result.http_status == 200
        assert result.domain_status == DomainStatus.UP

    def test_append_csv_writes_version_comment(self, temp_storage, sample_result):
        """New files should include version comment."""
        temp_storage.append_csv(sample_result)

        domain_path = temp_storage._get_domain_path("test-site", "test-site.example.com")
        assert domain_path.exists()

        with open(domain_path, "r") as f:
            lines = f.readlines()

        # First line should be version comment
        assert lines[0].startswith("# version:")
        assert "2" in lines[0]

        # Second line should be schema comment
        assert lines[1].startswith("# schema:")

    def test_read_domain_results_reads_v2_data(self, temp_storage):
        """Reading v2 file should work without migration."""
        domain_path = temp_storage._get_domain_path("test-site", "test-site.example.com")
        domain_path.parent.mkdir(parents=True, exist_ok=True)

        # Write v2-style file with version comment
        with open(domain_path, "w", newline="") as f:
            f.write("# version: 2\n")
            f.write(f"# schema: {','.join(get_fields_for_version(2))}\n")
            writer = csv.writer(f)
            writer.writerow(get_fields_for_version(2))
            ts = recent_timestamp()
            writer.writerow([ts, "test-site",
                           "test-site.example.com", "PROTECTED", "503", "100",
                           "", "cloudflare"])

        results = temp_storage.read_domain_results("test-site", "test-site.example.com", hours=24)

        assert len(results) == 1
        result = results[0]
        assert result.domain_status == DomainStatus.PROTECTED
        assert result.protection_type == "cloudflare"

    def test_pad_row_to_headers(self, temp_storage):
        """Row padding should work correctly."""
        row = ["a", "b", "c"]
        headers = ["x", "y", "z", "w"]
        result = temp_storage._pad_row_to_headers(row, headers)

        assert len(result) == 4
        assert result == ["a", "b", "c", ""]

    def test_pad_row_truncation(self, temp_storage):
        """Row truncation should work for rows longer than headers."""
        row = ["a", "b", "c", "d", "e"]
        headers = ["x", "y", "z"]
        result = temp_storage._pad_row_to_headers(row, headers)

        assert len(result) == 3
        assert result == ["a", "b", "c"]


class TestLegacyCompatibility:
    """Test backward compatibility with existing data formats."""

    def test_legacy_v1_file_without_protection_field(self, temp_storage):
        """Files created before protection_type was added should still work."""
        domain_path = temp_storage._get_domain_path("legacy-site", "legacy-site.old.com")
        domain_path.parent.mkdir(parents=True, exist_ok=True)

        # Simulate old-style CSV (v1 format, no version comments)
        with open(domain_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "site_id", "domain_id", "domain_status",
                           "http_status", "latency_ms", "failure_type"])  # 7 fields
            ts1 = recent_timestamp()
            ts2 = (datetime.now(timezone.utc) - timedelta(minutes=50)).isoformat()
            writer.writerow([ts1, "legacy-site",
                           "legacy-site.old.com", "UP", "200", "50", ""])
            writer.writerow([ts2, "legacy-site",
                           "legacy-site.old.com", "DOWN", "500", "100", "http_error"])

        results = temp_storage.read_domain_results("legacy-site", "legacy-site.old.com", hours=24)

        assert len(results) == 2
        assert all(r.protection_type is None for r in results)
        assert results[0].domain_status == DomainStatus.UP
        assert results[1].domain_status == DomainStatus.DOWN
        assert results[1].failure_type == FailureType.HTTP_ERROR

    def test_malformed_rows_are_skipped_gracefully(self, temp_storage):
        """Malformed rows should not crash the reader."""
        domain_path = temp_storage._get_domain_path("bad-site", "bad-site.test.com")
        domain_path.parent.mkdir(parents=True, exist_ok=True)

        with open(domain_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(get_fields_for_version(1))
            ts1 = recent_timestamp()
            ts2 = (datetime.now(timezone.utc) - timedelta(minutes=50)).isoformat()
            writer.writerow([ts1, "bad-site",
                           "bad-site.test.com", "UP", "200", "50", ""])  # Good row
            writer.writerow(["garbage", "data"])  # Bad row
            writer.writerow([ts2, "bad-site",
                           "bad-site.test.com", "DOWN", "500", "100", ""])  # Good row

        results = temp_storage.read_domain_results("bad-site", "bad-site.test.com", hours=24)

        # Should have 2 good results, bad row skipped
        assert len(results) == 2

    def test_empty_file_returns_empty_list(self, temp_storage):
        """Empty file should return empty results."""
        results = temp_storage.read_domain_results("empty-site", "empty-site.test.com")
        assert results == []

    def test_nonexistent_file_returns_empty_list(self, temp_storage):
        """Nonexistent file should return empty results."""
        results = temp_storage.read_domain_results("missing", "missing.test.com")
        assert results == []


class TestModelToCSVRoundTrip:
    """Test that model data survives round-trip through CSV."""

    def test_result_round_trip_with_all_fields(self, temp_storage):
        """Result with all fields should survive round-trip."""
        ts = recent_timestamp()
        original = Result(
            timestamp=ts,
            site_id="roundtrip-site",
            domain_id="roundtrip-site.example.com",
            domain_status=DomainStatus.PROTECTED,
            http_status=503,
            latency_ms=150,
            failure_type=None,
            protection_type="cloudflare"
        )

        temp_storage.append_csv(original)

        results = temp_storage.read_domain_results("roundtrip-site",
                                                   "roundtrip-site.example.com", hours=24)

        assert len(results) == 1
        result = results[0]

        assert result.timestamp == original.timestamp
        assert result.site_id == original.site_id
        assert result.domain_id == original.domain_id
        assert result.domain_status == original.domain_status
        assert result.http_status == original.http_status
        assert result.latency_ms == original.latency_ms
        assert result.failure_type == original.failure_type
        assert result.protection_type == original.protection_type

    def test_result_round_trip_with_nulls(self, temp_storage):
        """Result with null fields should survive round-trip."""
        ts = recent_timestamp()
        original = Result(
            timestamp=ts,
            site_id="null-site",
            domain_id="null-site.example.com",
            domain_status=DomainStatus.TIMEOUT,
            http_status=None,
            latency_ms=None,
            failure_type=FailureType.TIMEOUT,
            protection_type=None
        )

        temp_storage.append_csv(original)

        results = temp_storage.read_domain_results("null-site", "null-site.example.com", hours=24)

        assert len(results) == 1
        result = results[0]

        assert result.http_status is None
        assert result.latency_ms is None
        assert result.failure_type == FailureType.TIMEOUT
        assert result.protection_type is None
