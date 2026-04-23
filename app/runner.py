"""
FlatMonitor - HTTP Check Runner

Performs HTTP checks and classifies results based on priority rules.
"""

import time
import ssl
import socket
import logging
import requests
from urllib.parse import urlparse
from typing import Optional
from datetime import datetime, timezone

from app.models import DomainConfig, Result, DomainStatus, FailureType
from app.cert_storage import CertStorage
from requests.adapters import HTTPAdapter

logger = logging.getLogger(__name__)


class Runner:
    """Executes HTTP checks and classifies results."""

    # Consolidated bot protection detection patterns
    # Each entry maps protection type to detection criteria
    BOT_PROTECTION_PATTERNS = {
        "Cloudflare": {
            "status_codes": {403, 429, 503},
            "headers": ["cf-ray", "cloudflare", "cf-cache-status"],
            "body": ["cloudflare", "cf-ray", "just a moment", "checking your browser"],
            "server_header": "cloudflare"
        },
        "DDoS-Guard": {
            "status_codes": {403, 429, 503},
            "headers": ["ddos-guard", "ddosguard"],
            "body": ["ddos-guard", "ddosguard.net", "please wait", "checking your browser", "ddos protection"],
            "server_header": "ddos-guard"
        },
        "AWS WAF": {
            "status_codes": {403, 429, 503},
            "headers": ["awselb", "awsalb"],
            "body": ["aws waf", "amazon web services"]
        },
        "reCAPTCHA": {
            "status_codes": {403, 429},
            "body": ["recaptcha", "g-recaptcha", "captcha"]
        },
        "hCaptcha": {
            "status_codes": {403, 429},
            "body": ["hcaptcha", "h-captcha"]
        },
        "Akamai": {
            "status_codes": {403, 429},
            "headers": ["akamai-cache-status", "x-akamai-request-id", "akamai"]
        },
        "Fastly": {
            "status_codes": {403, 429, 503},
            "headers": ["x-served-by"],
            "header_values": ["fastly"]
        },
        "Incapsula": {
            "status_codes": {403, 429},
            "headers": ["x-iinfo", "incap-ses", "incapsula"]
        },
        "DataDome": {
            "status_codes": {403, 429},
            "headers": ["x-datadome", "datadome"]
        },
        "PerimeterX": {
            "status_codes": {403, 429},
            "headers": ["x-perimeter-x", "px-captcha", "perimeterx"]
        }
    }

    # Generic bot indicators (checked when status code matches but no specific pattern found)
    GENERIC_BOT_INDICATORS = [
        "captcha", "blocked", "access denied",
        "rate limit", "too many requests", "bot detected",
        "please wait", "checking your browser",
        "security check", "verify you are human"
    ]

    # Status codes commonly used for bot protection
    BOT_PROTECTION_STATUS_CODES = {403, 429, 503}

    def __init__(self, data_dir: str = "data"):
        self.session = requests.Session()
        # Configure connection pooling for efficient reuse
        adapter = HTTPAdapter(pool_connections=10, pool_maxsize=20)
        self.session.mount('https://', adapter)
        self.session.mount('http://', adapter)
        # Set a realistic user agent to avoid basic bot detection
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })
        # Certificate storage with 24-hour TTL
        self.cert_storage = CertStorage(data_dir=data_dir, ttl_seconds=86400)

    def _get_cert_expiry(self, url: str, site_id: str, domain_name: str) -> Optional[str]:
        """Extract SSL certificate expiry date from HTTPS URL with caching."""
        parsed = urlparse(url)
        if parsed.scheme != "https":
            return None

        hostname = parsed.hostname
        if not hostname:
            return None

        def fetch_cert():
            """Fetch fresh cert data from server."""
            try:
                port = parsed.port or 443
                context = ssl.create_default_context()
                with socket.create_connection((hostname, port), timeout=10) as sock:
                    with context.wrap_socket(sock, server_hostname=hostname) as ssock:
                        cert = ssock.getpeercert()
                        if cert and "notAfter" in cert:
                            expiry_date = ssl.cert_time_to_seconds(cert["notAfter"])
                            expiry_datetime = datetime.fromtimestamp(expiry_date, tz=timezone.utc)
                            return expiry_datetime.isoformat()
            except (ssl.SSLError, socket.error, OSError, ValueError):
                pass
            return None

        # Use cached cert storage with TTL
        return self.cert_storage.get_cert_expiry(site_id, domain_name, url, fetch_cert)

    def check(self, domain: DomainConfig) -> Result:
        """
        Perform HTTP check with classification priority:
        1. TIMEOUT: If request duration exceeds timeout
        2. Detect bot protection (informational - always logged)
        3. UP: If body_contains configured and found (site is functional, regardless of protection)
        4. DOWN: If http_status != expected
        5. DOWN: If body_contains configured but not found
        6. PROTECTED/BOT_DETECTED: If bot indicators found (when no content check)
        7. UP: Success
        """
        start_time = time.time()
        timeout_seconds = domain.timeout

        # Extract domain name for cert storage lookup
        domain_name = domain.id.split(".", 1)[1] if "." in domain.id else domain.id

        try:
            # Perform the request
            response = self.session.get(
                domain.url,
                timeout=timeout_seconds,
                allow_redirects=True,
                stream=True  # Stream to check content before downloading entire body
            )

            # Calculate latency
            latency_ms = int((time.time() - start_time) * 1000)

            # Read limited content for bot check and body_contains check
            content = ""
            try:
                # Read up to 1MB to check content
                content = response.content[:1024*1024].decode('utf-8', errors='ignore')
            except (UnicodeDecodeError, AttributeError):
                pass

            # 2. Bot Protection Detection (informational - always run and log)
            bot_detected, bot_reason, detected_indicator, protection_type = self._detect_bot_protection(domain, response, content)
            if bot_detected:
                # Log protection detection for visibility
                logger.info(
                    f"[PROTECTION] {domain.id}: "
                    f"type='{protection_type}', "
                    f"detected='{detected_indicator}', "
                    f"status_code={response.status_code}"
                )

            # 3. Bot Protection Site: Always show PROTECTED (green)
            # Content checks are skipped for protected sites (they serve protection pages, not site content)
            if bot_detected:
                # Fetch cert expiry for HTTPS protected sites
                self._get_cert_expiry(domain.url, domain.site_id, domain_name)
                return Result.create(
                    domain=domain,
                    status=DomainStatus.PROTECTED,
                    http_status=response.status_code,
                    latency_ms=latency_ms,
                    failure_type=None,
                    protection_type=protection_type
                )

            # 4. Content Check (only for non-protected sites)
            if domain.expect.body_contains:
                content_found = domain.expect.body_contains in content
                logger.info(
                    f"[CONTENT] {domain.id}: "
                    f"looking_for='{domain.expect.body_contains}', "
                    f"found={content_found}, "
                    f"content_length={len(content)}"
                )
                if content_found:
                    # Content found = UP (green)
                    self._get_cert_expiry(domain.url, domain.site_id, domain_name)
                    return Result.create(
                        domain=domain,
                        status=DomainStatus.UP,
                        http_status=response.status_code,
                        latency_ms=latency_ms,
                        protection_type=protection_type
                    )
                else:
                    # Content not found = DOWN (red) - site may be replaced with error page
                    return Result.create(
                        domain=domain,
                        status=DomainStatus.DOWN,
                        http_status=response.status_code,
                        latency_ms=latency_ms,
                        failure_type=FailureType.CONTENT_MISMATCH,
                        protection_type=protection_type
                    )

            # 5. Status Check (when no content check configured)
            if response.status_code != domain.expect.http_status:
                return Result.create(
                    domain=domain,
                    status=DomainStatus.DOWN,
                    http_status=response.status_code,
                    latency_ms=latency_ms,
                    failure_type=FailureType.HTTP_ERROR,
                    protection_type=protection_type
                )

            # 6. Latency Check (for sites without content validation)
            # Flag as DEGRADED if latency exceeds threshold and no body_contains configured
            LATENCY_THRESHOLD_MS = 3000
            if not domain.expect.body_contains and latency_ms > LATENCY_THRESHOLD_MS:
                logger.info(
                    f"[LATENCY] {domain.id}: "
                    f"latency={latency_ms}ms exceeds threshold={LATENCY_THRESHOLD_MS}ms, "
                    f"marking as DEGRADED (no content validation configured)"
                )
                return Result.create(
                    domain=domain,
                    status=DomainStatus.DEGRADED,
                    http_status=response.status_code,
                    latency_ms=latency_ms,
                    failure_type=FailureType.LATENCY_HIGH,
                    protection_type=protection_type
                )

            # 7. Success - fetch cert expiry for HTTPS domains
            self._get_cert_expiry(domain.url, domain.site_id, domain_name)
            return Result.create(
                domain=domain,
                status=DomainStatus.UP,
                http_status=response.status_code,
                latency_ms=latency_ms,
                protection_type=protection_type
            )

        except requests.exceptions.Timeout:
            # 1. Timeout (highest priority)
            return Result.create(
                domain=domain,
                status=DomainStatus.TIMEOUT,
                failure_type=FailureType.TIMEOUT
            )

        except requests.exceptions.ConnectionError as e:
            return Result.create(
                domain=domain,
                status=DomainStatus.DOWN,
                failure_type=FailureType.CONNECTION_REFUSED
            )

        except requests.exceptions.RequestException as e:
            return Result.create(
                domain=domain,
                status=DomainStatus.DOWN,
                failure_type=FailureType.UNKNOWN
            )

        except Exception as e:
            # Catch-all for unexpected errors - log full traceback for debugging
            import traceback
            logger.error(f"Unexpected error checking {domain.id}: {e}\n{traceback.format_exc()}")
            return Result.create(
                domain=domain,
                status=DomainStatus.DOWN,
                failure_type=FailureType.UNKNOWN
            )

    def _detect_bot_protection(self, domain: DomainConfig, response, content: str) -> tuple:
        """
        Detect if response indicates bot protection is active.
        Returns (detected: bool, reason: str, detected_indicator: str, protection_type: str)
        """
        content_lower = content.lower()
        status_code = response.status_code

        # Check custom bot protection string if configured (deprecated, but still supported)
        if domain.bot_protection_string and domain.bot_protection_string in content:
            protection_type = self._match_protection_pattern(response, content_lower)
            return True, f"configured indicator '{domain.bot_protection_string}' found", domain.bot_protection_string, protection_type

        # Check for bot protection patterns (on any status code - interstitial pages can be 200)
        protection_type = self._match_protection_pattern(response, content_lower)
        if protection_type:
            return True, f"{protection_type} detected", protection_type.lower(), protection_type

        # Check for generic bot indicators on protection status codes
        if status_code in self.BOT_PROTECTION_STATUS_CODES:
            for indicator in self.GENERIC_BOT_INDICATORS:
                if indicator in content_lower:
                    return True, f"status {status_code} with indicator '{indicator}'", indicator, None

        # Check for generic bot indicators on 200 responses (interstitial pages)
        if status_code == 200:
            for indicator in self.GENERIC_BOT_INDICATORS:
                if indicator in content_lower:
                    return True, f"indicator '{indicator}' found in response", indicator, None

        return False, "", None, None

    def _match_protection_pattern(self, response, content_lower: str) -> Optional[str]:
        """
        Match response against known bot protection patterns.
        Returns protection type name if matched, None otherwise.
        """
        try:
            # Get headers safely (handle Mock objects)
            headers = getattr(response, 'headers', {}) or {}
            headers_lower = {k.lower(): v.lower() for k, v in headers.items()}
            status_code = response.status_code
            server = headers_lower.get('server', '')

            # PHASE 1: Check headers first (most reliable indicator)
            for protection_name, pattern in self.BOT_PROTECTION_PATTERNS.items():
                status_codes = pattern.get("status_codes", set())
                
                # Skip if status code doesn't match and isn't 200 (interstitial)
                if status_codes and status_code not in status_codes and status_code != 200:
                    continue

                # Check server header first (most reliable)
                if pattern.get("server_header") and pattern["server_header"] in server:
                    return protection_name

                # Check specific headers
                if pattern.get("headers"):
                    for header_pattern in pattern["headers"]:
                        if header_pattern in headers_lower:
                            return protection_name
                        # Check header values too
                        for k, v in headers_lower.items():
                            if header_pattern in v:
                                return protection_name

                # Check header values list (for partial matches like Fastly)
                if pattern.get("header_values"):
                    for v in headers_lower.values():
                        for hv in pattern["header_values"]:
                            if hv in v:
                                return protection_name

            # PHASE 2: Check body content (only if no header match)
            for protection_name, pattern in self.BOT_PROTECTION_PATTERNS.items():
                status_codes = pattern.get("status_codes", set())
                
                if status_codes and status_code not in status_codes and status_code != 200:
                    continue

                if pattern.get("body"):
                    for body_pattern in pattern["body"]:
                        if body_pattern in content_lower:
                            return protection_name

        except (AttributeError, TypeError):
            pass

        return None

    def close(self):
        """Close the session."""
        self.session.close()
