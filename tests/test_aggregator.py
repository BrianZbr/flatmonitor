"""
Unit tests for aggregator.py
Tests bucket aggregation, forward-fill logic, site health determination
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

from app.aggregator import Aggregator, Bucket
from app.models import DomainConfig, DomainStatus, SiteHealth, Result


class TestBucket:
    """Tests for Bucket class."""

    def test_default_status(self):
        bucket = Bucket(timestamp=datetime.now(timezone.utc))
        assert bucket.status == DomainStatus.UNKNOWN

    def test_custom_status(self):
        bucket = Bucket(timestamp=datetime.now(timezone.utc), status=DomainStatus.UP)
        assert bucket.status == DomainStatus.UP

    def test_failure_details_storage(self):
        """Bucket should store http_status, failure_type, and latency_ms."""
        bucket = Bucket(
            timestamp=datetime.now(timezone.utc),
            status=DomainStatus.DOWN,
            http_status=503,
            failure_type="timeout",
            latency_ms=5000
        )
        assert bucket.status == DomainStatus.DOWN
        assert bucket.http_status == 503
        assert bucket.failure_type == "timeout"
        assert bucket.latency_ms == 5000

    def test_default_failure_details_are_none(self):
        """Bucket should default failure details to None."""
        bucket = Bucket(timestamp=datetime.now(timezone.utc))
        assert bucket.http_status is None
        assert bucket.failure_type is None
        assert bucket.latency_ms is None


class TestAggregator:
    """Tests for Aggregator class."""

    @pytest.fixture
    def aggregator(self):
        return Aggregator(bucket_minutes=5, history_hours=1)

    @pytest.fixture
    def mock_storage(self):
        return Mock()

    @pytest.fixture
    def sample_domain(self):
        return DomainConfig(
            id="test.site",
            url="https://example.com"
        )

    def test_aggregator_initialization(self, aggregator):
        assert aggregator.bucket_minutes == 5
        assert aggregator.history_hours == 1
        assert aggregator.total_buckets == 12  # 60 minutes / 5 minute buckets

    def test_aggregate_to_buckets_empty(self, aggregator, sample_domain):
        buckets = aggregator._aggregate_to_buckets([], sample_domain)
        assert len(buckets) == 12  # 60 minutes / 5 minute buckets
        all_unknown = all(b.status == DomainStatus.UNKNOWN for b in buckets)
        assert all_unknown

    def test_aggregate_to_buckets_single_result(self, aggregator, sample_domain):
        now = datetime.now(timezone.utc)
        result = Result(
            timestamp=now.isoformat(),
            site_id="test",
            domain_id="test.site",
            domain_status=DomainStatus.UP,
            http_status=200,
            latency_ms=100,
            failure_type=None
        )

        buckets = aggregator._aggregate_to_buckets([result], sample_domain)

        # Most recent bucket should be UP
        assert buckets[-1].status == DomainStatus.UP

    def test_single_failure_shows_degraded(self, aggregator, sample_domain):
        """Test that 1 failure in a bucket shows DEGRADED (yellow)."""
        # Align to 5-minute bucket boundary
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        # Go back 2 minutes to be in middle of a 5-min bucket
        bucket_middle = now - timedelta(minutes=2)

        # Create 5 results all within same 5-minute bucket (within 2.5 min of center)
        results = []
        for i in range(5):
            # Spread across 2 minutes: -1, -0.5, 0, +0.5, +1 from bucket_middle
            offset = (i - 2) * 30  # 30 second intervals
            result_time = bucket_middle + timedelta(seconds=offset)
            status = DomainStatus.DOWN if i == 2 else DomainStatus.UP  # 1 failure in middle
            results.append(Result(
                timestamp=result_time.isoformat(),
                site_id="test",
                domain_id="test.site",
                domain_status=status,
                http_status=503 if status == DomainStatus.DOWN else 200,
                latency_ms=100,
                failure_type="http_error" if status == DomainStatus.DOWN else None
            ))

        buckets = aggregator._aggregate_to_buckets(results, sample_domain)

        # Find the bucket with results and check it's DEGRADED
        for bucket in reversed(buckets):
            if bucket.status != DomainStatus.UNKNOWN:
                assert bucket.status == DomainStatus.DEGRADED
                return
        assert False, "No bucket with data found"

    def test_two_failures_shows_down(self, aggregator, sample_domain):
        """Test that 2 failures in a bucket shows DOWN (red)."""
        # Align to 5-minute bucket boundary
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        # Go back 2 minutes to be in middle of a 5-min bucket
        bucket_middle = now - timedelta(minutes=2)

        # Create 5 results all within same 5-minute bucket (within 2.5 min of center)
        results = []
        for i in range(5):
            # Spread across 2 minutes: -1, -0.5, 0, +0.5, +1 from bucket_middle
            offset = (i - 2) * 30  # 30 second intervals
            result_time = bucket_middle + timedelta(seconds=offset)
            # 2 failures: indices 2 and 3
            status = DomainStatus.DOWN if i in (2, 3) else DomainStatus.UP
            results.append(Result(
                timestamp=result_time.isoformat(),
                site_id="test",
                domain_id="test.site",
                domain_status=status,
                http_status=503 if status == DomainStatus.DOWN else 200,
                latency_ms=100,
                failure_type="http_error" if status == DomainStatus.DOWN else None
            ))

        buckets = aggregator._aggregate_to_buckets(results, sample_domain)

        # Find the bucket with results and check it's DOWN
        for bucket in reversed(buckets):
            if bucket.status != DomainStatus.UNKNOWN:
                assert bucket.status == DomainStatus.DOWN
                return
        assert False, "No bucket with data found"

    def test_all_up_shows_up(self, aggregator, sample_domain):
        """Test that all UP results show UP (green)."""
        now = datetime.now(timezone.utc)

        results = []
        for i in range(5):
            result_time = now - timedelta(minutes=i*1)
            results.append(Result(
                timestamp=result_time.isoformat(),
                site_id="test",
                domain_id="test.site",
                domain_status=DomainStatus.UP,
                http_status=200,
                latency_ms=100,
                failure_type=None
            ))

        buckets = aggregator._aggregate_to_buckets(results, sample_domain)

        # Most recent bucket should be UP (0 failures)
        assert buckets[-1].status == DomainStatus.UP

    def test_all_protected_shows_protected(self, aggregator, sample_domain):
        """Test that all PROTECTED results show PROTECTED (green)."""
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        bucket_middle = now - timedelta(minutes=2, seconds=30)

        results = []
        for i in range(5):
            offset = i - 2
            result_time = bucket_middle + timedelta(minutes=offset)
            results.append(Result(
                timestamp=result_time.isoformat(),
                site_id="test",
                domain_id="test.site",
                domain_status=DomainStatus.PROTECTED,
                http_status=503,
                latency_ms=100,
                failure_type=None,
                protection_type="DDoS-Guard"
            ))

        buckets = aggregator._aggregate_to_buckets(results, sample_domain)

        # Find the bucket with results and check it's PROTECTED
        for bucket in reversed(buckets):
            if bucket.status != DomainStatus.UNKNOWN:
                assert bucket.status == DomainStatus.PROTECTED
                return
        assert False, "No bucket with data found"

    def test_protected_overrides_up_in_bucket(self, aggregator, sample_domain):
        """Test that PROTECTED status from representative is preserved even with UP results."""
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        bucket_middle = now - timedelta(minutes=2, seconds=30)

        results = []
        for i in range(5):
            offset = i - 2
            result_time = bucket_middle + timedelta(minutes=offset)
            # Most recent (i=4) is PROTECTED, rest are UP
            status = DomainStatus.PROTECTED if i == 4 else DomainStatus.UP
            results.append(Result(
                timestamp=result_time.isoformat(),
                site_id="test",
                domain_id="test.site",
                domain_status=status,
                http_status=503 if status == DomainStatus.PROTECTED else 200,
                latency_ms=100,
                failure_type=None
            ))

        buckets = aggregator._aggregate_to_buckets(results, sample_domain)

        # Find the bucket with results - should be PROTECTED (most recent)
        for bucket in reversed(buckets):
            if bucket.status != DomainStatus.UNKNOWN:
                assert bucket.status == DomainStatus.PROTECTED
                return
        assert False, "No bucket with data found"

    def test_get_current_state(self, aggregator, sample_domain):
        now = datetime.now(timezone.utc)
        buckets = [
            Bucket(timestamp=now - timedelta(minutes=20), status=DomainStatus.UP),
            Bucket(timestamp=now - timedelta(minutes=10), status=DomainStatus.DOWN),
            Bucket(timestamp=now, status=DomainStatus.UP),
        ]

        current = aggregator._get_current_state(buckets, sample_domain)
        assert current == DomainStatus.UP  # Most recent bucket

    def test_get_current_state_skips_unknown(self, aggregator, sample_domain):
        """Test that current state uses most recent non-UNKNOWN bucket."""
        now = datetime.now(timezone.utc)
        buckets = [
            Bucket(timestamp=now - timedelta(minutes=20), status=DomainStatus.UP),
            Bucket(timestamp=now - timedelta(minutes=10), status=DomainStatus.DOWN),
            Bucket(timestamp=now, status=DomainStatus.UNKNOWN),  # Current bucket empty
        ]

        current = aggregator._get_current_state(buckets, sample_domain)
        assert current == DomainStatus.DOWN  # Should use most recent non-UNKNOWN

    def test_determine_site_health_all_up(self, aggregator):
        domain_states = {
            "domain1.site": {"status": DomainStatus.UP},
            "domain2.site": {"status": DomainStatus.UP},
            "domain3.site": {"status": DomainStatus.UP},
        }

        health = aggregator._determine_site_health(domain_states)
        assert health == SiteHealth.UP

    def test_determine_site_health_any_down(self, aggregator):
        domain_states = {
            "domain1.site": {"status": DomainStatus.DOWN},
            "domain2.site": {"status": DomainStatus.UP},
            "domain3.site": {"status": DomainStatus.UP},
        }

        health = aggregator._determine_site_health(domain_states)
        assert health == SiteHealth.DOWN

    def test_determine_site_health_any_timeout(self, aggregator):
        domain_states = {
            "domain1.site": {"status": DomainStatus.TIMEOUT},
            "domain2.site": {"status": DomainStatus.UP},
        }

        health = aggregator._determine_site_health(domain_states)
        assert health == SiteHealth.DOWN

    def test_determine_site_health_down_results_in_down(self, aggregator):
        """Test that any domain DOWN results in DOWN site health."""
        domain_states = {
            "domain1.site": {"status": DomainStatus.UP},
            "domain2.site": {"status": DomainStatus.UP},
            "domain3.site": {"status": DomainStatus.DOWN},
        }

        health = aggregator._determine_site_health(domain_states)
        assert health == SiteHealth.DOWN

    def test_determine_site_health_any_degraded(self, aggregator):
        """Test that any domain DEGRADED results in DEGRADED site health."""
        domain_states = {
            "domain1.site": {"status": DomainStatus.DEGRADED},
            "domain2.site": {"status": DomainStatus.UP},
        }

        health = aggregator._determine_site_health(domain_states)
        assert health == SiteHealth.DEGRADED

    def test_determine_site_health_any_degraded_2(self, aggregator):
        """Test that any domain DEGRADED results in DEGRADED site health."""
        domain_states = {
            "domain1.site": {"status": DomainStatus.UP},
            "domain2.site": {"status": DomainStatus.DEGRADED},
        }

        health = aggregator._determine_site_health(domain_states)
        assert health == SiteHealth.DEGRADED

    def test_bot_detected_deprecated_counts_as_up(self, aggregator):
        """BOT_DETECTED is deprecated and now treated as UP for health calculation."""
        domain_states = {
            "domain1.site": {"status": DomainStatus.UP},
            "domain2.site": {"status": DomainStatus.BOT_DETECTED},
        }

        health = aggregator._determine_site_health(domain_states)
        assert health == SiteHealth.UP

    def test_determine_site_health_protected_counts_as_up(self, aggregator):
        """PROTECTED (expected bot protection) should count as UP, not degraded."""
        domain_states = {
            "domain1.site": {"status": DomainStatus.UP},
            "domain2.site": {"status": DomainStatus.PROTECTED},
        }

        health = aggregator._determine_site_health(domain_states)
        assert health == SiteHealth.UP

    def test_determine_site_health_any_unknown(self, aggregator):
        """Any domain UNKNOWN should result in DOWN."""
        domain_states = {
            "domain1.site": {"status": DomainStatus.UNKNOWN},
            "domain2.site": {"status": DomainStatus.UP},
        }

        health = aggregator._determine_site_health(domain_states)
        assert health == SiteHealth.DOWN

    def test_determine_site_health_unknown_results_in_down(self, aggregator):
        """Any domain UNKNOWN should result in DOWN."""
        domain_states = {
            "domain1.site": {"status": DomainStatus.UP},
            "domain2.site": {"status": DomainStatus.UNKNOWN},
        }

        health = aggregator._determine_site_health(domain_states)
        assert health == SiteHealth.DOWN

    def test_determine_site_health_all_unknown(self, aggregator):
        """All domains UNKNOWN should result in UNKNOWN (gray, not red)."""
        domain_states = {
            "domain1.site": {"status": DomainStatus.UNKNOWN},
            "domain2.site": {"status": DomainStatus.UNKNOWN},
        }

        health = aggregator._determine_site_health(domain_states)
        assert health == SiteHealth.UNKNOWN

    def test_determine_site_health_empty_domains(self, aggregator):
        """Empty domain states should result in UNKNOWN."""
        health = aggregator._determine_site_health({})
        assert health == SiteHealth.UNKNOWN

    def test_process_site(self, aggregator, mock_storage, sample_domain):
        now = datetime.now(timezone.utc)
        mock_storage.read_domain_results.return_value = [
            Result(
                timestamp=now.isoformat(),
                site_id="test",
                domain_id="test.site",
                domain_status=DomainStatus.UP,
                http_status=200,
                latency_ms=100,
                failure_type=None
            )
        ]

        sites = {"test": [sample_domain]}
        result = aggregator.process_recent_data(mock_storage, sites)

        assert "sites" in result
        assert "test" in result["sites"]
        assert result["sites"]["test"]["health"] == SiteHealth.UP
        assert "generated_at" in result
