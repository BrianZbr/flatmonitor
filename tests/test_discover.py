"""
Unit tests for discover.py
Tests discovery tool functionality including header-based indicators
"""

import pytest
from unittest.mock import Mock, patch
import requests

from app.discover import DiscoveryRunner, suggest_config, format_output


class TestDiscoveryRunner:
    """Tests for DiscoveryRunner class."""

    @pytest.fixture
    def runner(self):
        return DiscoveryRunner()

    def test_probe_successful(self, runner):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b"OK"
        mock_response.headers = {'server': 'nginx'}
        mock_response.url = 'https://example.com'

        with patch.object(runner.session, 'get', return_value=mock_response):
            result = runner.probe('https://example.com')

        assert result['success'] is True
        assert result['status_code'] == 200
        assert result['content_snippet'] == 'OK'
        assert result['final_url'] == 'https://example.com'

    def test_probe_timeout(self, runner):
        with patch.object(runner.session, 'get', side_effect=requests.exceptions.Timeout()):
            result = runner.probe('https://example.com', timeout=5)

        assert result['success'] is False
        assert result['error'] == 'Request timed out after 5s'
        assert result['latency_ms'] == 5000

    def test_probe_request_exception(self, runner):
        with patch.object(runner.session, 'get', side_effect=requests.exceptions.ConnectionError('Connection refused')):
            result = runner.probe('https://example.com')

        assert result['success'] is False
        assert 'Connection refused' in result['error']

    def test_find_header_indicators_cloudflare_cf_ray(self, runner):
        headers = {'cf-ray': '8abc123def4567890-BOS'}
        indicators = runner._find_header_indicators(headers)

        assert len(indicators) == 1
        assert indicators[0]['source'] == 'header'
        assert indicators[0]['indicator'] == 'cf-ray'
        assert 'BOS' in indicators[0]['context']

    def test_find_header_indicators_cloudflare_server(self, runner):
        headers = {'server': 'cloudflare'}
        indicators = runner._find_header_indicators(headers)

        assert len(indicators) == 1
        assert indicators[0]['source'] == 'header'
        assert indicators[0]['indicator'] == 'server:cloudflare'

    def test_find_header_indicators_cloudflare_both(self, runner):
        headers = {'cf-ray': '123-BOS', 'server': 'cloudflare'}
        indicators = runner._find_header_indicators(headers)

        assert len(indicators) == 2
        sources = [i['indicator'] for i in indicators]
        assert 'cf-ray' in sources
        assert 'server:cloudflare' in sources

    def test_find_header_indicators_akamai(self, runner):
        headers = {'x-akamai-request-id': '12345'}
        indicators = runner._find_header_indicators(headers)

        assert len(indicators) == 1
        assert indicators[0]['indicator'] == 'akamai'

    def test_find_header_indicators_fastly(self, runner):
        headers = {'x-served-by': 'cache-bos1234-BOS, cache-fastly-123'}
        indicators = runner._find_header_indicators(headers)

        assert len(indicators) == 1
        assert indicators[0]['indicator'] == 'fastly'

    def test_find_header_indicators_incapsula(self, runner):
        headers = {'x-iinfo': '1234567890-12345678-12345'}
        indicators = runner._find_header_indicators(headers)

        assert len(indicators) == 1
        assert indicators[0]['indicator'] == 'incapsula'

    def test_find_header_indicators_cloudfront(self, runner):
        headers = {'x-amz-cf-id': 'abc123def456'}
        indicators = runner._find_header_indicators(headers)

        assert len(indicators) == 1
        assert indicators[0]['indicator'] == 'cloudfront'

    def test_find_header_indicators_datadome(self, runner):
        headers = {'x-datadome': 'protected'}
        indicators = runner._find_header_indicators(headers)

        assert len(indicators) == 1
        assert indicators[0]['indicator'] == 'datadome'

    def test_find_header_indicators_perimeterx(self, runner):
        headers = {'x-perimeter-x': 'blocked'}
        indicators = runner._find_header_indicators(headers)

        assert len(indicators) == 1
        assert indicators[0]['indicator'] == 'perimeterx'

    def test_find_header_indicators_no_protection(self, runner):
        headers = {'server': 'nginx', 'content-type': 'text/html'}
        indicators = runner._find_header_indicators(headers)

        assert len(indicators) == 0

    def test_find_all_indicates_includes_headers(self, runner):
        mock_response = Mock()
        mock_response.status_code = 403
        mock_response.headers = {'cf-ray': '123-BOS', 'server': 'cloudflare'}
        content = "Just a moment..."

        indicators = runner._find_all_indicators(mock_response, content)

        sources = [i['source'] for i in indicators]
        assert 'header' in sources
        assert 'status_code' in sources

    def test_find_all_indicators_cdn_error(self, runner):
        mock_response = Mock()
        mock_response.status_code = 522
        mock_response.headers = {}
        content = ""

        indicators = runner._find_all_indicators(mock_response, content)

        assert len(indicators) == 1
        assert indicators[0]['indicator'] == 'status_522'
        assert 'CDN origin error' in indicators[0]['context']


class TestSuggestConfig:
    """Tests for suggest_config function."""

    def test_suggest_config_with_status_and_body_indicator(self):
        findings = {
            'success': True,
            'status_code': 503,
            'indicators_found': [
                {'source': 'body', 'indicator': 'captcha', 'context': 'captcha here'},
                {'source': 'status_code', 'indicator': 'status_503'}
            ]
        }

        result = suggest_config(findings)

        assert result is not None
        assert result.status_code == 503
        assert result.indicator == 'captcha'

    def test_suggest_config_with_only_status_code(self):
        findings = {
            'success': True,
            'status_code': 403,
            'indicators_found': [
                {'source': 'status_code', 'indicator': 'status_403'}
            ]
        }

        result = suggest_config(findings)

        assert result is not None
        assert result.status_code == 403
        assert result.indicator is None

    def test_suggest_config_cdn_error_no_config(self):
        findings = {
            'success': True,
            'status_code': 522,
            'indicators_found': [
                {'source': 'status_code', 'indicator': 'status_522'}
            ]
        }

        result = suggest_config(findings)

        assert result is None

    def test_suggest_config_with_header_indicators(self):
        findings = {
            'success': True,
            'status_code': 200,
            'indicators_found': [
                {'source': 'header', 'indicator': 'cf-ray', 'context': 'Cloudflare'},
                {'source': 'header', 'indicator': 'server:cloudflare', 'context': 'Cloudflare'}
            ]
        }

        result = suggest_config(findings)

        assert result is not None
        assert result.status_code == 200
        assert result.indicator == 'cf-ray'

    def test_suggest_config_all_indicators_set(self):
        findings = {
            'success': True,
            'status_code': 403,
            'indicators_found': []
        }
        all_indicators = {'cf-ray', 'status_403'}

        result = suggest_config(findings, all_indicators)

        assert result is not None
        assert result.status_code == 403
        assert result.indicator in all_indicators

    def test_suggest_config_failure_returns_none(self):
        findings = {
            'success': False,
            'status_code': None,
            'indicators_found': []
        }

        result = suggest_config(findings)

        assert result is None


class TestFormatOutput:
    """Tests for format_output function."""

    def test_format_output_success_with_headers(self):
        findings = {
            'success': True,
            'final_url': 'https://example.com',
            'status_code': 403,
            'latency_ms': 100,
            'content_length': 500,
            'headers': {'cf-ray': '123-BOS', 'server': 'cloudflare'},
            'indicators_found': [
                {'source': 'status_code', 'indicator': 'status_403', 'context': 'HTTP 403'},
                {'source': 'header', 'indicator': 'cf-ray', 'context': 'Cloudflare detected'}
            ],
            'content_snippet': 'Just a moment...'
        }

        output = format_output('https://example.com', findings)

        assert 'https://example.com' in output
        assert '403' in output
        assert 'cf-ray' in output
        assert '[header]' in output
        assert '[status_code]' in output
        assert 'Cloudflare detected' in output

    def test_format_output_no_indicators(self):
        findings = {
            'success': True,
            'final_url': 'https://example.com',
            'status_code': 200,
            'latency_ms': 50,
            'content_length': 1000,
            'headers': {},
            'indicators_found': [],
            'content_snippet': 'OK'
        }

        output = format_output('https://example.com', findings)

        assert 'No bot protection detected' in output or 'No bot protection indicators detected' in output

    def test_format_output_failed_request(self):
        findings = {
            'success': False,
            'error': 'Connection refused',
            'final_url': 'https://example.com',
            'status_code': None,
            'latency_ms': None,
            'content_length': 0,
            'headers': {},
            'indicators_found': [],
            'content_snippet': ''
        }

        output = format_output('https://example.com', findings)

        assert 'Request failed' in output
        assert 'Connection refused' in output

    def test_format_output_with_protection_detected(self):
        """Test that discovery output shows bot protection detection."""
        from app.models import ExpectedBotProtection

        findings = {
            'success': True,
            'final_url': 'https://example.com',
            'status_code': 503,
            'latency_ms': 100,
            'content_length': 500,
            'headers': {},
            'indicators_found': [
                {'source': 'status_code', 'indicator': 'status_503'},
                {'source': 'body', 'indicator': 'captcha', 'context': 'captcha page'}
            ],
            'content_snippet': 'Captcha required'
        }
        suggested = ExpectedBotProtection(status_code=503, indicator='captcha')

        output = format_output('https://example.com', findings, suggested=suggested)

        assert 'Bot Protection Detected' in output
        assert 'Type: captcha' in output
        assert 'Status Code: 503' in output
        assert 'Monitoring Behavior:' in output
