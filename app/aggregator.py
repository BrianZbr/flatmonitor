"""
FlatMonitor - Aggregator

Forward-fills buckets and determines UP/DOWN states for sites and domains.
"""

import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple, Optional
from collections import defaultdict

from app.models import DomainConfig, Result, DomainStatus, SiteHealth
from app.cert_storage import CertStorage


class Bucket:
    """Represents a time bucket with status information."""

    def __init__(self, timestamp: datetime, status: DomainStatus = DomainStatus.UNKNOWN,
                 http_status: Optional[int] = None, failure_type: Optional[str] = None,
                 latency_ms: Optional[int] = None):
        self.timestamp = timestamp
        self.status = status
        self.http_status = http_status
        self.failure_type = failure_type
        self.latency_ms = latency_ms

    def __repr__(self):
        return f"Bucket({self.timestamp.isoformat()}, {self.status.value})"


class Aggregator:
    """Aggregates results into time buckets and determines health states."""

    def __init__(self, bucket_minutes: int = 5, history_hours: int = 4, data_dir: str = "data"):
        self.bucket_minutes = bucket_minutes
        self.history_hours = history_hours
        self.total_buckets = int((history_hours * 60) / bucket_minutes)  # 48 buckets for 4 hours
        self.cert_storage = CertStorage(data_dir=data_dir, ttl_seconds=86400)

    def process_recent_data(self, storage, sites: Dict[str, List[DomainConfig]]) -> Dict:
        """
        Process recent data for all sites and return aggregated state.
        Returns a dict with site health, domain states, and bucket data.
        """
        result = {
            "sites": {},
            "generated_at": datetime.now(timezone.utc).isoformat()
        }

        for site_id, domains in sites.items():
            site_data = self._process_site(storage, site_id, domains)
            result["sites"][site_id] = site_data

        return result

    def _process_site(self, storage, site_id: str,
                      domains: List[DomainConfig]) -> Dict:
        """Process data for a single site."""
        domain_buckets = {}
        domain_states = {}
        last_check_times = {}

        for domain in domains:
            # Read results for this domain
            results = storage.read_domain_results(
                site_id, domain.id, self.history_hours
            )

            # Aggregate into buckets with forward-fill
            buckets = self._aggregate_to_buckets(results, domain)
            domain_buckets[domain.id] = buckets

            # Determine current state
            current_status = self._get_current_state(buckets, domain)

            # Get last check details from most recent result
            last_check = self._get_last_check_details(results, domain)
            last_check_times[domain.id] = last_check.get("timestamp", "")

            domain_states[domain.id] = {
                "status": current_status,
                "url": domain.url,
                "link_disabled": domain.link_disabled,
                "last_check": last_check,
                "expected": {
                    "http_status": domain.expect.http_status,
                    "body_contains": domain.expect.body_contains,
                    "bot_protection": {
                        "status_code": domain.expected_bot_protection.status_code if domain.expected_bot_protection else None,
                        "indicator": domain.expected_bot_protection.indicator if domain.expected_bot_protection else None
                    } if domain.expected_bot_protection else None
                }
            }

        # Determine site health
        site_health = self._determine_site_health(domain_states)

        # Get most recent check time across all domains
        most_recent_check = self._get_most_recent_check(last_check_times)

        return {
            "health": site_health,
            "domains": domain_states,
            "buckets": domain_buckets,
            "bucket_count": self.total_buckets,
            "last_check": most_recent_check
        }

    def _get_last_check_details(self, results: List[Result],
                                domain: DomainConfig) -> Dict:
        """Extract details from the most recent check result."""
        # Get cert info from separate cert storage
        domain_name = domain.id.split(".", 1)[1] if "." in domain.id else domain.id
        cert_info = self.cert_storage.get_cert_info(domain.site_id, domain_name)
        cert_expiry = cert_info.get('cert_expiry') if cert_info else None

        if not results:
            return {
                "timestamp": "",
                "http_status": None,
                "latency_ms": None,
                "cert_expiry": cert_expiry,
                "body_contains_pass": None,
                "protection_type": None
            }

        # Get most recent result
        sorted_results = sorted(
            results,
            key=lambda r: datetime.fromisoformat(r.timestamp.replace("Z", "+00:00")),
            reverse=True
        )
        last_result = sorted_results[0]

        # Determine if body_contains check passed
        body_contains_pass = None
        if domain.expect.body_contains:
            body_contains_pass = last_result.domain_status == DomainStatus.UP

        return {
            "timestamp": last_result.timestamp,
            "http_status": last_result.http_status,
            "latency_ms": last_result.latency_ms,
            "cert_expiry": cert_expiry,
            "body_contains_pass": body_contains_pass,
            "protection_type": last_result.protection_type
        }

    def _get_most_recent_check(self, last_check_times: Dict[str, str]) -> str:
        """Get the most recent check timestamp across all domains."""
        if not last_check_times:
            return ""

        valid_times = [
            t for t in last_check_times.values() if t
        ]
        if not valid_times:
            return ""

        sorted_times = sorted(
            valid_times,
            key=lambda t: datetime.fromisoformat(t.replace("Z", "+00:00")),
            reverse=True
        )
        return sorted_times[0]

    def _aggregate_to_buckets(self, results: List[Result],
                              domain: DomainConfig) -> List[Bucket]:
        """
        Aggregate results into time buckets with failure threshold:
        - 0 failures: UP, 1 failure: DEGRADED, 2+ failures: DOWN
        """
        now = datetime.now(timezone.utc)

        # Create buckets at consistent intervals (aligned to bucket_minutes boundaries)
        bucket_map: Dict[datetime, List[Result]] = {}
        buckets = []
        current_bucket = now.replace(second=0, microsecond=0)
        # Round down to nearest bucket boundary
        current_minute = current_bucket.minute
        bucket_minute = (current_minute // self.bucket_minutes) * self.bucket_minutes
        current_bucket = current_bucket.replace(minute=bucket_minute)

        for i in range(self.total_buckets):
            bucket_time = current_bucket - timedelta(minutes=i * self.bucket_minutes)
            bucket = Bucket(bucket_time)
            buckets.append(bucket)
            bucket_map[bucket_time] = []

        buckets.sort(key=lambda b: b.timestamp)
        failure_statuses = {DomainStatus.DOWN, DomainStatus.TIMEOUT, DomainStatus.UNKNOWN}

        # Assign results to buckets by finding matching time range
        for result in results:
            result_time = datetime.fromisoformat(result.timestamp.replace("Z", "+00:00"))
            for bucket in buckets:
                bucket_end = bucket.timestamp + timedelta(minutes=self.bucket_minutes)
                if bucket.timestamp <= result_time < bucket_end:
                    bucket_map[bucket.timestamp].append(result)
                    break

        # Determine status per bucket
        for bucket in buckets:
            bucket_results = bucket_map[bucket.timestamp]
            if not bucket_results:
                continue

            failure_count = sum(1 for r in bucket_results if r.domain_status in failure_statuses)
            representative = max(bucket_results, key=lambda r: r.timestamp)

            # Determine status based on failure count, preserving PROTECTED when no failures
            if failure_count >= 2:
                bucket.status = DomainStatus.DOWN
            elif failure_count == 1:
                bucket.status = DomainStatus.DEGRADED
            elif representative.domain_status == DomainStatus.PROTECTED:
                bucket.status = DomainStatus.PROTECTED
            else:
                bucket.status = DomainStatus.UP
            bucket.http_status = representative.http_status
            bucket.failure_type = representative.failure_type
            bucket.latency_ms = representative.latency_ms

        return buckets

    def _get_current_state(self, buckets: List[Bucket],
                          domain: DomainConfig) -> DomainStatus:
        """Get the current state from the most recent non-UNKNOWN bucket."""
        if not buckets:
            return DomainStatus.UNKNOWN

        # Find most recent bucket with actual data (not UNKNOWN)
        # Buckets are sorted oldest first, so iterate in reverse
        for bucket in reversed(buckets):
            if bucket.status != DomainStatus.UNKNOWN:
                return bucket.status

        return DomainStatus.UNKNOWN

    def _determine_site_health(self, domain_states: Dict[str, Dict]) -> SiteHealth:
        """
        Determine site health based on domain states:
        - DOWN: Any domain is DOWN or TIMEOUT (2+ consecutive failures)
        - DEGRADED: Any domain is DEGRADED (1 failure)
        - UP: All domains are UP or PROTECTED
        - UNKNOWN: All domains have no data (UNKNOWN)
        """
        if not domain_states:
            return SiteHealth.UNKNOWN

        has_degraded = False
        has_unknown = False
        has_up_protected = False

        for state in domain_states.values():
            if state["status"] in (DomainStatus.DOWN, DomainStatus.TIMEOUT):
                return SiteHealth.DOWN
            if state["status"] == DomainStatus.DEGRADED:
                has_degraded = True
            if state["status"] == DomainStatus.UNKNOWN:
                has_unknown = True
            if state["status"] in (DomainStatus.UP, DomainStatus.PROTECTED):
                has_up_protected = True

        # All unknown = gray (new site with no data)
        if has_unknown and not has_up_protected and not has_degraded:
            return SiteHealth.UNKNOWN
        # Mixed unknown + up = down (partial data is problematic)
        if has_unknown:
            return SiteHealth.DOWN
        if has_degraded:
            return SiteHealth.DEGRADED

        return SiteHealth.UP
