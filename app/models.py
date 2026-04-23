"""
FlatMonitor - Core Data Models

Shared Pydantic/Dataclass schemas for the monitoring system.
"""

from enum import Enum
from typing import Optional
from datetime import datetime, timezone
from pydantic import BaseModel, Field


class DomainStatus(str, Enum):
    """Status classification for check results."""
    UP = "UP"
    DOWN = "DOWN"
    DEGRADED = "DEGRADED"  # Display-only: single failure in bucket (yellow)
    BOT_DETECTED = "BOT_DETECTED"  # Deprecated: no longer used, kept for backward compatibility
    PROTECTED = "PROTECTED"  # Bot protection detected (counts as UP)
    TIMEOUT = "TIMEOUT"
    UNKNOWN = "UNKNOWN"


class SiteHealth(str, Enum):
    """Overall site health classification."""
    UP = "UP"
    DEGRADED = "DEGRADED"
    DOWN = "DOWN"
    UNKNOWN = "UNKNOWN"


class FailureType(str, Enum):
    """Structured error classification for check failures."""
    TIMEOUT = "timeout"
    CONNECTION_REFUSED = "connection_refused"
    DNS_FAILURE = "dns_failure"
    SSL_ERROR = "ssl_error"
    HTTP_ERROR = "http_error"
    CONTENT_MISMATCH = "content_mismatch"
    UNEXPECTED_REDIRECT = "unexpected_redirect"
    LATENCY_HIGH = "latency_high"
    UNKNOWN = "unknown"


class ExpectConfig(BaseModel):
    """Expected response configuration."""
    http_status: int = Field(default=200, description="Expected HTTP status code")
    body_contains: Optional[str] = Field(default=None, description="Optional string to validate in response body")


class ExpectedBotProtection(BaseModel):
    """Expected bot protection signature for comparison."""
    status_code: Optional[int] = Field(default=None, description="Expected HTTP status code when bot protection active (e.g., 503, 429, 403)")
    indicator: Optional[str] = Field(default=None, description="Expected detection indicator string (e.g., 'cloudflare', 'checking your browser')")


class DomainConfig(BaseModel):
    """Configuration for a monitored domain."""
    id: str = Field(..., description="Unique identifier for the domain")
    url: str = Field(..., description="URL to monitor")
    interval_seconds: int = Field(default=60, ge=10, description="Check interval in seconds (fixed at 60 for timeline consistency)")
    expect: ExpectConfig = Field(default_factory=ExpectConfig, description="Expected response criteria")
    bot_protection_string: Optional[str] = Field(default=None, description="String indicating bot protection page")
    expected_bot_protection: Optional[ExpectedBotProtection] = Field(default=None, description="Deprecated: no longer used. Bot protection is automatically detected.")
    timeout: int = Field(default=20, ge=1, le=300, description="Request timeout in seconds (default: 20)")
    link_disabled: bool = Field(default=False, description="Disable clickable links for this domain in the UI (default: false)")

    @property
    def site_id(self) -> str:
        """Extract site_id from domain_id (assumes format: site_id.domain_name)."""
        return self.id.split(".")[0] if "." in self.id else "default"


class Result(BaseModel):
    """Result of an HTTP check."""
    timestamp: str = Field(..., description="ISO UTC timestamp string")
    site_id: str = Field(..., description="Site identifier")
    domain_id: str = Field(..., description="Domain identifier")
    domain_status: DomainStatus = Field(..., description="Status classification")
    http_status: Optional[int] = Field(default=None, description="HTTP status code (null on timeout)")
    latency_ms: Optional[int] = Field(default=None, description="Response latency in ms (null on failure)")
    failure_type: Optional[FailureType] = Field(default=None, description="Structured error classification")
    protection_type: Optional[str] = Field(default=None, description="Detected protection type (e.g., DDoS-Guard, Cloudflare)")

    @classmethod
    def create(cls, domain: DomainConfig, status: DomainStatus, http_status: Optional[int] = None,
               latency_ms: Optional[int] = None, failure_type: Optional[FailureType] = None,
               protection_type: Optional[str] = None) -> "Result":
        """Factory method to create a Result from a DomainConfig."""
        return cls(
            timestamp=datetime.now(timezone.utc).isoformat(),
            site_id=domain.site_id,
            domain_id=domain.id,
            domain_status=status,
            http_status=http_status,
            latency_ms=latency_ms,
            failure_type=failure_type,
            protection_type=protection_type
        )

    def to_csv_row(self) -> list:
        """Convert to CSV row format."""
        return [
            self.timestamp,
            self.site_id,
            self.domain_id,
            self.domain_status.value,
            str(self.http_status) if self.http_status is not None else "",
            str(self.latency_ms) if self.latency_ms is not None else "",
            self.failure_type.value if self.failure_type else "",
            self.protection_type or ""
        ]

    @classmethod
    def from_csv_row(cls, row: list) -> "Result":
        """Create from CSV row format."""
        # Parse failure_type with backward compatibility for legacy string values
        failure_type = None
        if len(row) > 6 and row[6]:
            try:
                failure_type = FailureType(row[6])
            except ValueError:
                # Legacy string value not in enum - default to UNKNOWN
                failure_type = FailureType.UNKNOWN

        # Parse protection_type (backward compatible - may not exist in older rows)
        protection_type = row[7] if len(row) > 7 and row[7] else None

        return cls(
            timestamp=row[0],
            site_id=row[1],
            domain_id=row[2],
            domain_status=DomainStatus(row[3]),
            http_status=int(row[4]) if row[4] else None,
            latency_ms=int(row[5]) if row[5] else None,
            failure_type=failure_type,
            protection_type=protection_type
        )
