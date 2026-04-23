"""
Unit tests for runner.py
Tests HTTP check logic, status classification, timeout handling with mocked responses
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import requests

from app.runner import Runner
from app.models import DomainConfig, DomainStatus, ExpectConfig, FailureType


class TestRunner:
    """Tests for Runner class."""

    @pytest.fixture
    def runner(self):
        return Runner()

    @pytest.fixture
    def basic_domain(self):
        return DomainConfig(
            id="test.site",
            url="https://example.com",
            expect=ExpectConfig(http_status=200)
        )

    def test_successful_check(self, runner, basic_domain):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b"OK"

        with patch.object(runner.session, 'get', return_value=mock_response):
            result = runner.check(basic_domain)

        assert result.domain_status == DomainStatus.UP
        assert result.http_status == 200
        assert result.latency_ms is not None
        assert result.failure_type is None

    def test_wrong_status_code(self, runner, basic_domain):
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.content = b"Server Error"

        with patch.object(runner.session, 'get', return_value=mock_response):
            result = runner.check(basic_domain)

        assert result.domain_status == DomainStatus.DOWN
        assert result.http_status == 500
        assert result.failure_type == FailureType.HTTP_ERROR

    def test_body_contains_success(self, runner):
        domain = DomainConfig(
            id="test.site",
            url="https://example.com",
            expect=ExpectConfig(http_status=200, body_contains="Expected Content")
        )

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b"Some Expected Content here"

        with patch.object(runner.session, 'get', return_value=mock_response):
            result = runner.check(domain)

        assert result.domain_status == DomainStatus.UP

    def test_body_contains_failure(self, runner):
        domain = DomainConfig(
            id="test.site",
            url="https://example.com",
            expect=ExpectConfig(http_status=200, body_contains="Expected Content")
        )

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b"Different content"

        with patch.object(runner.session, 'get', return_value=mock_response):
            result = runner.check(domain)

        assert result.domain_status == DomainStatus.DOWN
        assert result.failure_type == FailureType.CONTENT_MISMATCH

    def test_bot_protection_detected(self, runner):
        """Bot protection always returns PROTECTED (green), never BOT_DETECTED."""
        domain = DomainConfig(
            id="test.site",
            url="https://example.com",
            expect=ExpectConfig(http_status=200)
        )

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b"Cloudflare Ray ID: 12345"
        mock_response.headers = {}

        with patch.object(runner.session, 'get', return_value=mock_response):
            result = runner.check(domain)

        assert result.domain_status == DomainStatus.PROTECTED
        assert result.http_status == 200
        assert result.failure_type == FailureType.HTTP_ERROR

    def test_timeout(self, runner, basic_domain):
        with patch.object(runner.session, 'get', side_effect=requests.exceptions.Timeout):
            result = runner.check(basic_domain)

        assert result.domain_status == DomainStatus.TIMEOUT
        assert result.http_status is None
        assert result.latency_ms is None
        assert result.failure_type == FailureType.TIMEOUT

    def test_connection_error(self, runner, basic_domain):
        with patch.object(runner.session, 'get', side_effect=requests.exceptions.ConnectionError("Connection refused")):
            result = runner.check(basic_domain)

        assert result.domain_status == DomainStatus.DOWN
        assert result.failure_type == FailureType.CONNECTION_REFUSED

    def test_general_request_exception(self, runner, basic_domain):
        with patch.object(runner.session, 'get', side_effect=requests.exceptions.RequestException("Some error")):
            result = runner.check(basic_domain)

        assert result.domain_status == DomainStatus.DOWN
        assert result.failure_type == FailureType.UNKNOWN

    def test_unexpected_exception(self, runner, basic_domain):
        with patch.object(runner.session, 'get', side_effect=Exception("Unexpected")):
            result = runner.check(basic_domain)

        assert result.domain_status == DomainStatus.DOWN
        assert result.failure_type == FailureType.UNKNOWN

    def test_protection_skips_content_check(self, runner):
        """Bot protection detected: content check is skipped, returns PROTECTED."""
        domain = DomainConfig(
            id="test.site",
            url="https://example.com",
            expect=ExpectConfig(http_status=200, body_contains="Expected Content")
        )

        mock_response = Mock()
        mock_response.status_code = 200
        # Bot protection detected - content check skipped regardless of content
        mock_response.content = b"Expected Content here Cloudflare Ray ID: 12345"
        mock_response.headers = {}

        with patch.object(runner.session, 'get', return_value=mock_response):
            result = runner.check(domain)

        # Protection takes precedence - PROTECTED even if content would match
        assert result.domain_status == DomainStatus.PROTECTED
        assert result.http_status == 200

    def test_protection_status_any_bot_detection(self, runner):
        """All bot protection returns PROTECTED - no signature matching needed."""
        domain = DomainConfig(
            id="test.site",
            url="https://example.com",
            expect=ExpectConfig(http_status=503)
        )

        mock_response = Mock()
        mock_response.status_code = 503
        mock_response.content = b"Cloudflare Ray ID: 12345 checking your browser"
        mock_response.headers = {}

        with patch.object(runner.session, 'get', return_value=mock_response):
            result = runner.check(domain)

        assert result.domain_status == DomainStatus.PROTECTED
        assert result.http_status == 503
        assert result.failure_type == FailureType.HTTP_ERROR

    def test_timeout_priority(self, runner, basic_domain):
        """Timeout should be detected before any other checks."""
        with patch.object(runner.session, 'get', side_effect=requests.exceptions.Timeout):
            result = runner.check(basic_domain)

        assert result.domain_status == DomainStatus.TIMEOUT

    def test_protection_overrides_content_mismatch(self, runner):
        """Bot protection detected: returns PROTECTED, content mismatch ignored."""
        domain = DomainConfig(
            id="test.site",
            url="https://example.com",
            expect=ExpectConfig(http_status=200, body_contains="Missing Content")
        )

        mock_response = Mock()
        mock_response.status_code = 200
        # Bot protection detected - content check skipped
        mock_response.content = b"Cloudflare Ray ID: 12345 blocked"
        mock_response.headers = {}

        with patch.object(runner.session, 'get', return_value=mock_response):
            result = runner.check(domain)

        # Protection detected - PROTECTED, not DOWN for content mismatch
        assert result.domain_status == DomainStatus.PROTECTED
        assert result.failure_type == FailureType.HTTP_ERROR

    def test_close_session(self, runner):
        with patch.object(runner.session, 'close') as mock_close:
            runner.close()
            mock_close.assert_called_once()

    def test_cloudflare_protection_type_detected(self, runner):
        """Test that Cloudflare protection type is detected via cf-ray header."""
        domain = DomainConfig(
            id="test.site",
            url="https://example.com",
            expect=ExpectConfig(http_status=403)
        )

        mock_response = Mock()
        mock_response.status_code = 403
        mock_response.content = b"cloudflare captcha required"
        mock_response.headers = {'cf-ray': '7d1234567bfc1234-BOS'}

        with patch.object(runner.session, 'get', return_value=mock_response):
            result = runner.check(domain)

        assert result.domain_status == DomainStatus.PROTECTED
        assert result.protection_type == "Cloudflare"

    def test_cloudflare_server_header_protection_type(self, runner):
        """Test Cloudflare detection via Server header."""
        domain = DomainConfig(
            id="test.site",
            url="https://example.com",
            expect=ExpectConfig(http_status=200)
        )

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b"Cloudflare Ray ID: 12345 cloudflare detected"
        mock_response.headers = {'Server': 'cloudflare'}

        with patch.object(runner.session, 'get', return_value=mock_response):
            result = runner.check(domain)

        assert result.domain_status == DomainStatus.PROTECTED
        assert result.protection_type == "Cloudflare"

    def test_ddos_guard_protection_type_detected(self, runner):
        """Test that DDoS-Guard protection type is detected via Server header."""
        domain = DomainConfig(
            id="test.site",
            url="https://example.com",
            expect=ExpectConfig(http_status=403)
        )

        mock_response = Mock()
        mock_response.status_code = 403
        mock_response.content = b"ddos protection active - ddosguard.net"
        mock_response.headers = {'Server': 'ddos-guard'}

        with patch.object(runner.session, 'get', return_value=mock_response):
            result = runner.check(domain)

        assert result.domain_status == DomainStatus.PROTECTED
        assert result.protection_type == "DDoS-Guard"

    def test_protection_type_in_protected_result(self, runner):
        """Test that protection type is included in PROTECTED status results."""
        domain = DomainConfig(
            id="test.site",
            url="https://example.com",
            expect=ExpectConfig(http_status=503)
        )

        mock_response = Mock()
        mock_response.status_code = 503
        mock_response.content = b"Some error page"
        mock_response.headers = {'cf-ray': '7d1234567bfc1234-BOS', 'Server': 'cloudflare'}

        with patch.object(runner.session, 'get', return_value=mock_response):
            result = runner.check(domain)

        assert result.domain_status == DomainStatus.PROTECTED
        assert result.protection_type == "Cloudflare"

    def test_no_protection_type_when_no_bot_detected(self, runner):
        """Test that protection_type is None when no bot protection detected."""
        domain = DomainConfig(
            id="test.site",
            url="https://example.com",
            expect=ExpectConfig(http_status=200)
        )

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b"Normal response without any protection"
        mock_response.headers = {}

        with patch.object(runner.session, 'get', return_value=mock_response):
            result = runner.check(domain)

        assert result.domain_status == DomainStatus.UP
        assert result.protection_type is None

    def test_recaptcha_protection_type_detected(self, runner):
        """Test that reCAPTCHA protection type is detected via content."""
        domain = DomainConfig(
            id="test.site",
            url="https://example.com",
            expect=ExpectConfig(http_status=403)
        )

        mock_response = Mock()
        mock_response.status_code = 403
        mock_response.content = b'<html>captcha <div class="g-recaptcha"></div></html>'
        mock_response.headers = {}

        with patch.object(runner.session, 'get', return_value=mock_response):
            result = runner.check(domain)

        assert result.domain_status == DomainStatus.PROTECTED
        assert result.protection_type == "reCAPTCHA"

    def test_aws_waf_protection_type_detected(self, runner):
        """Test that AWS WAF protection type is detected via headers."""
        domain = DomainConfig(
            id="test.site",
            url="https://example.com",
            expect=ExpectConfig(http_status=403)
        )

        mock_response = Mock()
        mock_response.status_code = 403
        mock_response.content = b"captcha Access denied"
        mock_response.headers = {'Server': 'awselb/2.0'}

        with patch.object(runner.session, 'get', return_value=mock_response):
            result = runner.check(domain)

        assert result.domain_status == DomainStatus.PROTECTED
        assert result.protection_type == "AWS WAF"

    def test_header_based_cf_ray_detection(self, runner):
        """Test that cf-ray header is detected as bot protection indicator."""
        domain = DomainConfig(
            id="test.site",
            url="https://example.com",
            expect=ExpectConfig(http_status=403)
        )

        mock_response = Mock()
        mock_response.status_code = 403
        mock_response.content = b"Access denied"
        mock_response.headers = {'cf-ray': '7d1234567bfc1234-BOS'}

        with patch.object(runner.session, 'get', return_value=mock_response):
            result = runner.check(domain)

        assert result.domain_status == DomainStatus.PROTECTED
        assert result.protection_type == "Cloudflare"

    def test_header_based_cloudflare_server_detection(self, runner):
        """Test that cloudflare server header is detected as bot protection indicator."""
        domain = DomainConfig(
            id="test.site",
            url="https://example.com",
            expect=ExpectConfig(http_status=403)
        )

        mock_response = Mock()
        mock_response.status_code = 403
        mock_response.content = b"<html> blocked </html>"
        mock_response.headers = {'Server': 'cloudflare'}

        with patch.object(runner.session, 'get', return_value=mock_response):
            result = runner.check(domain)

        assert result.domain_status == DomainStatus.PROTECTED
        assert result.protection_type == "Cloudflare"

    def test_header_based_ddos_guard_detection(self, runner):
        """Test that ddos-guard server header is detected as bot protection indicator."""
        domain = DomainConfig(
            id="test.site",
            url="https://example.com",
            expect=ExpectConfig(http_status=403)
        )

        mock_response = Mock()
        mock_response.status_code = 403
        mock_response.content = b"<html> checking your browser </html>"
        mock_response.headers = {'Server': 'ddos-guard'}

        with patch.object(runner.session, 'get', return_value=mock_response):
            result = runner.check(domain)

        assert result.domain_status == DomainStatus.PROTECTED
        assert result.protection_type == "DDoS-Guard"

    def test_header_based_datadome_detection(self, runner):
        """Test that x-datadome header is detected as bot protection indicator."""
        domain = DomainConfig(
            id="test.site",
            url="https://example.com",
            expect=ExpectConfig(http_status=403)
        )

        mock_response = Mock()
        mock_response.status_code = 403
        mock_response.content = b"Access denied"
        mock_response.headers = {'x-datadome': 'protected'}

        with patch.object(runner.session, 'get', return_value=mock_response):
            result = runner.check(domain)

        assert result.domain_status == DomainStatus.PROTECTED

    def test_header_based_no_protection_when_headers_missing(self, runner):
        """Test that no bot protection is detected when no relevant headers present."""
        domain = DomainConfig(
            id="test.site",
            url="https://example.com",
            expect=ExpectConfig(http_status=200)  # Expect 200, get 403
        )

        mock_response = Mock()
        mock_response.status_code = 403
        # No bot indicators in body or headers
        mock_response.content = b"Normal error page"
        mock_response.headers = {'Server': 'nginx'}

        with patch.object(runner.session, 'get', return_value=mock_response):
            result = runner.check(domain)

        # Should be DOWN due to status mismatch (403 != 200)
        # and NOT BOT_DETECTED since no bot protection indicators present
        assert result.domain_status == DomainStatus.DOWN

    def test_match_protection_pattern_method(self, runner):
        """Test the _match_protection_pattern method directly."""
        # Test cf-ray detection (Cloudflare)
        mock_response = Mock()
        mock_response.headers = {'cf-ray': '12345-BOS', 'Server': 'cloudflare'}
        mock_response.status_code = 403
        result = runner._match_protection_pattern(mock_response, "")
        assert result == 'Cloudflare'

        # Test server header detection (DDoS-Guard)
        mock_response.headers = {'Server': 'ddos-guard'}
        mock_response.status_code = 403
        result = runner._match_protection_pattern(mock_response, "")
        assert result == 'DDoS-Guard'

        # Test no headers
        mock_response.headers = {}
        result = runner._match_protection_pattern(mock_response, "")
        assert result is None

        # Test None headers
        mock_response.headers = None
        result = runner._match_protection_pattern(mock_response, "")
        assert result is None
