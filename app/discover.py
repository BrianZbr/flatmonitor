"""
FlatMonitor - Discovery Tool

Probes domains to discover their protection patterns and suggests configuration.
"""

import sys
import argparse
import logging
from typing import Optional, List, Dict, Any
import requests
from urllib.parse import urlparse

from app.models import DomainConfig, ExpectConfig, ExpectedBotProtection
from app.runner import Runner


logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)


class DiscoveryRunner(Runner):
    """Extended runner that captures detailed detection information."""

    def probe(self, url: str, timeout: int = 20) -> Dict[str, Any]:
        """
        Probe a URL and return detailed findings without classification.

        Returns dict with:
        - status_code: HTTP status code
        - indicators_found: List of detected bot protection indicators
        - content_snippet: First 500 chars of response body
        - headers_snippet: Relevant response headers
        - latency_ms: Response time
        - content_length: Body size
        - final_url: URL after redirects
        """
        start_time = __import__('time').time()

        try:
            response = self.session.get(
                url,
                timeout=timeout,
                allow_redirects=True,
                stream=True
            )

            latency_ms = int((__import__('time').time() - start_time) * 1000)

            # Read limited content
            content = ""
            try:
                content = response.content[:1024 * 1024].decode('utf-8', errors='ignore')
            except (UnicodeDecodeError, AttributeError):
                pass

            # Detect indicators (reusing parent's detection logic)
            indicators = self._find_all_indicators(response, content)

            # Extract relevant headers
            relevant_headers = {}
            for key in ['server', 'cf-ray', 'x-cache', 'x-powered-by', 'via']:
                if key in response.headers:
                    relevant_headers[key] = response.headers[key]

            return {
                'status_code': response.status_code,
                'indicators_found': indicators,
                'content_snippet': content[:500].replace('\n', ' ').replace('\r', ''),
                'headers': relevant_headers,
                'latency_ms': latency_ms,
                'content_length': len(content),
                'final_url': response.url,
                'success': True,
                'error': None
            }

        except requests.exceptions.Timeout:
            return {
                'status_code': None,
                'indicators_found': [],
                'content_snippet': "",
                'headers': {},
                'latency_ms': timeout * 1000,
                'content_length': 0,
                'final_url': url,
                'success': False,
                'error': f"Request timed out after {timeout}s"
            }

        except requests.exceptions.RequestException as e:
            return {
                'status_code': None,
                'indicators_found': [],
                'content_snippet': "",
                'headers': {},
                'latency_ms': None,
                'content_length': 0,
                'final_url': url,
                'success': False,
                'error': str(e)
            }

    # CDN error codes indicating origin is down (not bot protection)
    CDN_ERROR_CODES = {520, 521, 522, 523, 524, 525, 526, 527, 530}

    def _find_all_indicators(self, response, content: str) -> List[Dict[str, str]]:
        """
        Find all bot protection indicators in the response.
        Returns list of dicts with 'source' (status_code or body) and 'indicator'.
        """
        found = []
        content_lower = content.lower()

        # Skip bot protection detection for CDN error pages (origin is down)
        if response.status_code in self.CDN_ERROR_CODES:
            found.append({
                'source': 'status_code',
                'indicator': f"status_{response.status_code}",
                'context': f"HTTP {response.status_code} indicates CDN origin error (site may be down)"
            })
            return found

        # Check for indicators in content
        for indicator in self.GENERIC_BOT_INDICATORS:
            if indicator in content_lower:
                found.append({
                    'source': 'body',
                    'indicator': indicator,
                    'context': self._get_context(content_lower, indicator)
                })

        # Check if status code suggests bot protection
        if response.status_code in self.BOT_PROTECTION_STATUS_CODES:
            found.append({
                'source': 'status_code',
                'indicator': f"status_{response.status_code}",
                'context': f"HTTP {response.status_code} is commonly used for bot protection"
            })

        # Check for CDN/protection signatures in headers
        header_indicators = self._find_header_indicators(response.headers)
        found.extend(header_indicators)

        return found

    def _get_context(self, content: str, indicator: str, window: int = 40) -> str:
        """Get context around where indicator appears in content."""
        idx = content.find(indicator)
        if idx == -1:
            return ""
        start = max(0, idx - window)
        end = min(len(content), idx + len(indicator) + window)
        return content[start:end].strip()

    def _find_header_indicators(self, headers) -> List[Dict[str, str]]:
        """
        Find bot protection indicators in HTTP response headers.
        Returns list of dicts with 'source' (header), 'indicator', and 'context'.
        """
        found = []
        headers_lower = {k.lower(): v for k, v in headers.items()}

        # Cloudflare signatures
        if 'cf-ray' in headers_lower:
            cf_ray = headers_lower['cf-ray']
            # Extract location code from cf-ray (last 3 chars before any suffix)
            location = cf_ray.split('-')[-1][:3] if '-' in cf_ray else 'unknown'
            found.append({
                'source': 'header',
                'indicator': 'cf-ray',
                'context': f"Cloudflare proxy detected (location: {location.upper()})"
            })

        if 'server' in headers_lower:
            server = headers_lower['server'].lower()
            if 'cloudflare' in server:
                found.append({
                    'source': 'header',
                    'indicator': 'server:cloudflare',
                    'context': f"Server header indicates Cloudflare: {headers_lower['server']}"
                })

        # Akamai signatures
        if 'x-akamai-request-id' in headers_lower or 'akamai-cache-status' in headers_lower:
            found.append({
                'source': 'header',
                'indicator': 'akamai',
                'context': "Akamai CDN detected"
            })

        # Fastly signatures
        if 'x-served-by' in headers_lower and 'fastly' in headers_lower.get('x-served-by', '').lower():
            found.append({
                'source': 'header',
                'indicator': 'fastly',
                'context': f"Fastly CDN detected: {headers_lower['x-served-by']}"
            })

        # Incapsula/Imperva signatures
        if 'x-iinfo' in headers_lower or 'incap-ses' in headers_lower:
            found.append({
                'source': 'header',
                'indicator': 'incapsula',
                'context': "Imperva Incapsula protection detected"
            })

        # AWS CloudFront
        if 'x-amz-cf-id' in headers_lower or 'x-amz-cf-pop' in headers_lower:
            found.append({
                'source': 'header',
                'indicator': 'cloudfront',
                'context': "AWS CloudFront CDN detected"
            })

        # DataDome
        if 'x-datadome' in headers_lower or 'datadome' in headers_lower.get('server', '').lower():
            found.append({
                'source': 'header',
                'indicator': 'datadome',
                'context': "DataDome bot protection detected"
            })

        # PerimeterX (now HUMAN)
        if 'x-perimeter-x' in headers_lower or 'px-captcha' in headers_lower:
            found.append({
                'source': 'header',
                'indicator': 'perimeterx',
                'context': "PerimeterX (HUMAN) bot protection detected"
            })

        return found


def suggest_config(findings: Dict[str, Any], all_indicators: Optional[set] = None) -> Optional[ExpectedBotProtection]:
    """
    Suggest expected_bot_protection configuration based on findings.

    Strategy:
    - If status code is 503/429/403 and there's a body indicator, use both
    - If status code is 200 but body has indicators, use status 200 + first indicator
    - If minimal content with 503/429/403, use just status code
    - If all_indicators provided (from multiple checks), use any indicator seen across checks
    """
    status_code = findings['status_code']
    indicators = [i for i in findings['indicators_found'] if i['source'] in ('body', 'header')]

    if not findings['success']:
        return None

    # CDN error codes indicate site is down, not bot protection
    cdn_error_codes = {520, 521, 522, 523, 524, 525, 526, 527, 530}

    # Status codes suggesting protection
    protection_codes = {503, 429, 403}

    # If it's a CDN error, don't suggest bot protection config
    if status_code in cdn_error_codes:
        return None

    # Use indicators from all checks if provided (for sporadic detection)
    indicator_list = indicators if indicators else []
    if all_indicators and not indicator_list:
        # Create synthetic indicator entries from the set
        indicator_list = [{'indicator': ind} for ind in sorted(all_indicators)]

    if status_code in protection_codes:
        if indicator_list:
            # Use status code + first indicator
            return ExpectedBotProtection(
                status_code=status_code,
                indicator=indicator_list[0]['indicator']
            )
        else:
            # Just the status code (minimal response)
            return ExpectedBotProtection(status_code=status_code)

    elif indicator_list:
        # Protection indicators found in body with any status code
        # (e.g., 200 for challenge pages, 522 for Cloudflare errors, etc.)
        return ExpectedBotProtection(
            status_code=status_code,
            indicator=indicator_list[0]['indicator']
        )

    return None


def format_output(url: str, findings: Dict[str, Any], num_checks: int = 1,
                  suggested: Optional[ExpectedBotProtection] = None,
                  all_indicators: Optional[set] = None) -> str:
    """Format discovery results for CLI output."""
    lines = []
    lines.append(f"\n{'=' * 60}")
    lines.append(f"Discovery Results: {url}")
    lines.append(f"{'=' * 60}\n")

    if not findings['success']:
        lines.append(f"❌ Request failed: {findings['error']}")
        return '\n'.join(lines)

    # Basic info
    lines.append(f"Final URL: {findings['final_url']}")
    lines.append(f"Status Code: {findings['status_code']}")
    lines.append(f"Latency: {findings['latency_ms']}ms")
    lines.append(f"Content Length: {findings['content_length']} bytes")
    lines.append("")

    # Headers
    if findings['headers']:
        lines.append("Relevant Headers:")
        for key, value in findings['headers'].items():
            lines.append(f"  {key}: {value}")
        lines.append("")

    # Indicators
    if findings['indicators_found']:
        lines.append(f"Bot Protection Indicators Found ({len(findings['indicators_found'])}):")
        for i, ind in enumerate(findings['indicators_found'], 1):
            lines.append(f"  {i}. [{ind['source']}] '{ind['indicator']}'")
            if ind.get('context') and ind['source'] in ('body', 'header'):
                ctx = ind['context'][:60] + "..." if len(ind['context']) > 60 else ind['context']
                lines.append(f"     Context: \"{ctx}\"")
        lines.append("")
    else:
        lines.append("No bot protection indicators detected.")
        lines.append("")

    # Content snippet
    if findings['content_snippet']:
        lines.append("Content Snippet (first 500 chars):")
        lines.append("-" * 40)
        snippet = findings['content_snippet'][:500]
        lines.append(snippet)
        lines.append("-" * 40)
        lines.append("")

    # Suggested config
    if suggested is None:
        suggested = suggest_config(findings)
    status_code = findings.get('status_code')

    # CDN error codes indicate origin is unreachable (site is down)
    cdn_error_codes = {520, 521, 522, 523, 524, 525, 526, 527, 530}

    if suggested:
        lines.append("✅ Bot Protection Detected")
        lines.append(f"   Type: {suggested.indicator or 'Unknown'}")
        lines.append(f"   Status Code: {suggested.status_code}")
        lines.append("")
        lines.append("📝 Monitoring Behavior:")
        lines.append("   Bot protection sites automatically show PROTECTED (green)")
        lines.append("   Content checks are skipped (protection pages don't contain site content)")

        # Note if indicator was sporadic (from multiple checks but not in this specific check)
        if all_indicators and suggested.indicator:
            body_indicators = [i['indicator'] for i in findings.get('indicators_found', [])
                             if i.get('source') == 'body']
            if suggested.indicator not in body_indicators:
                lines.append("")
                lines.append(f"⚠️  Note: '{suggested.indicator}' was found in some checks but not this one.")
                lines.append("   Protection may be sporadic - this is normal for many sites.")
    elif status_code in cdn_error_codes:
        lines.append(f"⚠️  CDN origin error detected (status {status_code}).")
        lines.append("The site appears to be down - the CDN cannot reach the origin server.")
        lines.append("This is not bot protection, it's a server availability issue.")
    else:
        lines.append("💡 No bot protection detected")
        lines.append("")
        lines.append("⚠️  RECOMMENDATION: Add body_contains to your config!")
        lines.append("   Without bot protection, the site only checks HTTP status 200.")
        lines.append("   If the site breaks and returns 200 with an error page,")
        lines.append("   you'll get false positives showing the site as UP.")
        lines.append("")
        lines.append("   Suggested config based on content snippet:")
        snippet = findings.get('content_snippet', '')[:100]
        if snippet:
            # Extract a unique-looking phrase (avoid generic text)
            words = [w for w in snippet.split() if len(w) > 4 and w.isalnum()]
            if words:
                suggested_phrase = ' '.join(words[:3])
                lines.append(f"     body_contains: \"{suggested_phrase}\"")
        lines.append("")
        lines.append("   For example, you might use:")
        lines.append('     body_contains: "expected content phrase"')

    return '\n'.join(lines)


def run_multiple_checks(runner: DiscoveryRunner, url: str, count: int = 3) -> List[Dict[str, Any]]:
    """Run multiple checks and return all findings."""
    findings = []
    for i in range(count):
        result = runner.probe(url)
        findings.append(result)
        # Small delay between checks
        if i < count - 1:
            __import__('time').sleep(0.5)
    return findings


def main():
    parser = argparse.ArgumentParser(
        description='Discover bot protection patterns for a domain',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m app.discover https://httpbin.org/get
  python -m app.discover https://example.org --checks 5
  python -m app.discover https://example.com --timeout 30
        """
    )
    parser.add_argument('url', help='URL to probe')
    parser.add_argument('--checks', '-n', type=int, default=1,
                        help='Number of checks to run (default: 1)')
    parser.add_argument('--timeout', '-t', type=int, default=20,
                        help='Request timeout in seconds (default: 20)')
    parser.add_argument('--json', '-j', action='store_true',
                        help='Output raw JSON instead of formatted text')

    args = parser.parse_args()

    # Validate URL
    parsed = urlparse(args.url)
    if not parsed.scheme:
        args.url = f"https://{args.url}"
        parsed = urlparse(args.url)

    if parsed.scheme not in ('http', 'https'):
        logger.error(f"Invalid URL scheme: {parsed.scheme}")
        sys.exit(1)

    runner = DiscoveryRunner()

    try:
        if args.checks == 1:
            findings = runner.probe(args.url, args.timeout)
            print(format_output(args.url, findings))
        else:
            print(f"\nRunning {args.checks} checks against {args.url}...")
            all_findings = run_multiple_checks(runner, args.url, args.checks)

            # Analyze consistency
            status_codes = [f['status_code'] for f in all_findings if f['success']]
            all_indicators = set()
            for f in all_findings:
                for ind in f['indicators_found']:
                    all_indicators.add(ind['indicator'])

            print(f"\n{'=' * 60}")
            print(f"Summary across {args.checks} checks:")
            print(f"{'=' * 60}")

            if status_codes:
                from collections import Counter
                status_counts = Counter(status_codes)
                print(f"Status codes: {dict(status_counts)}")

            if all_indicators:
                print(f"All indicators seen: {sorted(all_indicators)}")
            else:
                print("No indicators seen in any check")

            # Use the first successful check for config suggestion
            # but include all indicators seen across any check
            successful = [f for f in all_findings if f['success']]
            if successful:
                suggested = suggest_config(successful[0], all_indicators)
                print(format_output(args.url, successful[0], args.checks, suggested, all_indicators))

    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        sys.exit(130)
    finally:
        runner.close()


if __name__ == "__main__":
    main()
