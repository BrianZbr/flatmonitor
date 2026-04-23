"""
Unit tests for models.py
Tests Pydantic model validation, CSV serialization/deserialization
"""

import pytest
from datetime import datetime
from app.models import (
    DomainConfig, Result, ExpectConfig,
    DomainStatus, SiteHealth, FailureType
)


class TestDomainStatus:
    """Tests for DomainStatus enum."""

    def test_status_values(self):
        assert DomainStatus.UP.value == "UP"
        assert DomainStatus.DOWN.value == "DOWN"
        assert DomainStatus.BOT_DETECTED.value == "BOT_DETECTED"
        assert DomainStatus.PROTECTED.value == "PROTECTED"
        assert DomainStatus.TIMEOUT.value == "TIMEOUT"
        assert DomainStatus.UNKNOWN.value == "UNKNOWN"


class TestSiteHealth:
    """Tests for SiteHealth enum."""

    def test_health_values(self):
        assert SiteHealth.UP.value == "UP"
        assert SiteHealth.DEGRADED.value == "DEGRADED"
        assert SiteHealth.DOWN.value == "DOWN"


class TestExpectConfig:
    """Tests for ExpectConfig model."""

    def test_default_values(self):
        config = ExpectConfig()
        assert config.http_status == 200
        assert config.body_contains is None

    def test_custom_values(self):
        config = ExpectConfig(http_status=404, body_contains="Error")
        assert config.http_status == 404
        assert config.body_contains == "Error"


class TestDomainConfig:
    """Tests for DomainConfig model."""

    def test_default_values(self):
        domain = DomainConfig(id="test.site", url="https://example.com")
        assert domain.id == "test.site"
        assert domain.url == "https://example.com"
        assert domain.interval_seconds == 60
        assert domain.timeout == 20
        assert domain.bot_protection_string is None
        assert domain.link_disabled == False

    def test_custom_values(self):
        domain = DomainConfig(
            id="prod.api",
            url="https://api.example.com",
            expect=ExpectConfig(http_status=200, body_contains="OK"),
            bot_protection_string="Cloudflare",
            timeout=10,
            link_disabled=True
        )
        assert domain.interval_seconds == 60  # Fixed at 60
        assert domain.expect.http_status == 200
        assert domain.expect.body_contains == "OK"
        assert domain.bot_protection_string == "Cloudflare"
        assert domain.timeout == 10
        assert domain.link_disabled == True

    def test_site_id_extraction(self):
        domain = DomainConfig(id="acme.www", url="https://www.acme.com")
        assert domain.site_id == "acme"

    def test_site_id_default(self):
        domain = DomainConfig(id="single", url="https://example.com")
        assert domain.site_id == "default"

    def test_interval_validation(self):
        with pytest.raises(ValueError):
            DomainConfig(id="test", url="https://example.com", interval_seconds=5)

    def test_timeout_validation_min(self):
        with pytest.raises(ValueError):
            DomainConfig(id="test", url="https://example.com", timeout=0)

    def test_timeout_validation_max(self):
        with pytest.raises(ValueError):
            DomainConfig(id="test", url="https://example.com", timeout=400)


class TestResult:
    """Tests for Result model."""

    def test_create_success(self):
        domain = DomainConfig(id="test.site", url="https://example.com")
        result = Result.create(
            domain=domain,
            status=DomainStatus.UP,
            http_status=200,
            latency_ms=150
        )
        assert result.site_id == "test"
        assert result.domain_id == "test.site"
        assert result.domain_status == DomainStatus.UP
        assert result.http_status == 200
        assert result.latency_ms == 150
        assert result.failure_type is None
        assert result.timestamp.endswith("+00:00")

    def test_create_failure(self):
        domain = DomainConfig(id="test.site", url="https://example.com")
        result = Result.create(
            domain=domain,
            status=DomainStatus.DOWN,
            failure_type=FailureType.CONNECTION_REFUSED
        )
        assert result.domain_status == DomainStatus.DOWN
        assert result.http_status is None
        assert result.latency_ms is None
        assert result.failure_type == FailureType.CONNECTION_REFUSED

    def test_to_csv_row_full(self):
        result = Result(
            timestamp="2024-01-01T12:00:00Z",
            site_id="test",
            domain_id="test.site",
            domain_status=DomainStatus.UP,
            http_status=200,
            latency_ms=100,
            failure_type=None,
            protection_type=None
        )
        row = result.to_csv_row()
        assert row == [
            "2024-01-01T12:00:00Z",
            "test",
            "test.site",
            "UP",
            "200",
            "100",
            "",
            ""  # protection_type
        ]

    def test_to_csv_row_failure(self):
        result = Result(
            timestamp="2024-01-01T12:00:00Z",
            site_id="test",
            domain_id="test.site",
            domain_status=DomainStatus.TIMEOUT,
            http_status=None,
            latency_ms=None,
            failure_type=FailureType.TIMEOUT,
            protection_type=None
        )
        row = result.to_csv_row()
        assert row == [
            "2024-01-01T12:00:00Z",
            "test",
            "test.site",
            "TIMEOUT",
            "",
            "",
            "timeout",  # FailureType value
            ""  # protection_type
        ]

    def test_from_csv_row_full(self):
        row = ["2024-01-01T12:00:00Z", "test", "test.site", "UP", "200", "100", ""]
        result = Result.from_csv_row(row)
        assert result.timestamp == "2024-01-01T12:00:00Z"
        assert result.site_id == "test"
        assert result.domain_id == "test.site"
        assert result.domain_status == DomainStatus.UP
        assert result.http_status == 200
        assert result.latency_ms == 100
        assert result.failure_type is None

    def test_from_csv_row_failure(self):
        row = ["2024-01-01T12:00:00Z", "test", "test.site", "DOWN", "", "", "Connection error"]
        result = Result.from_csv_row(row)
        assert result.domain_status == DomainStatus.DOWN
        assert result.http_status is None
        assert result.latency_ms is None
        assert result.failure_type == FailureType.UNKNOWN  # Legacy string defaults to UNKNOWN

    def test_timestamp_is_timezone_aware(self):
        """Test that Result.create generates timezone-aware UTC timestamps."""
        from datetime import timezone
        domain = DomainConfig(id="test.site", url="https://example.com")
        result = Result.create(
            domain=domain,
            status=DomainStatus.UP,
            http_status=200,
            latency_ms=100
        )
        # Parse the timestamp and verify it's timezone-aware
        parsed = datetime.fromisoformat(result.timestamp.replace("Z", "+00:00"))
        assert parsed.tzinfo is not None
        assert parsed.tzinfo == timezone.utc

    def test_timestamp_format_contains_utc_offset(self):
        """Test that timestamp format includes UTC offset (ends with +00:00 or Z)."""
        domain = DomainConfig(id="test.site", url="https://example.com")
        result = Result.create(domain=domain, status=DomainStatus.UP)
        # Should end with +00:00 (timezone-aware format)
        assert "+00:00" in result.timestamp or result.timestamp.endswith("Z")

    def test_from_csv_row_backward_compatibility(self):
        """Test that old CSV rows with extra cert_expiry field are handled gracefully."""
        # Old CSV format had cert_expiry as 8th column - we should ignore it
        row = ["2024-01-01T12:00:00Z", "test", "test.site", "UP", "200", "100", "", "2025-12-31T23:59:59Z"]
        result = Result.from_csv_row(row)
        assert result.timestamp == "2024-01-01T12:00:00Z"
        assert result.domain_status == DomainStatus.UP
        assert result.timestamp == "2024-01-01T12:00:00Z"
        assert result.domain_status == DomainStatus.UP
