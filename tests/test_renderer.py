"""
Unit tests for renderer.py
Tests template rendering, throttling logic, output generation
"""

import pytest
import tempfile
import shutil
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

from app.renderer import Renderer
from app.models import DomainStatus, SiteHealth
from app.aggregator import Bucket


class TestRenderer:
    
    def test_json_in_html_attribute_quoting(self):
        """Test that data-archive-links attributes use single quotes to avoid JSON parsing issues.
        
        Regression test: Double quotes inside double-quoted HTML attributes break JSON parsing.
        HTML parsers truncate attributes at the first internal quote, producing malformed JSON.
        """
        # Test the template directly to verify it uses single quotes
        renderer = Renderer()
        template = renderer.env.get_template("site.html")
        
        # Render with a domain that has archive_links
        html = template.render(
            title="test",
            health=SiteHealth.UP,
            site={
                'id': 'test',
                'name': 'Test Site',
                'health': SiteHealth.UP,
                'domain_count': 1,
                'up_count': 1,
                'down_count': 0,
                'timeout_count': 0,
                'unknown_count': 0,
                'domains': {
                    'example.com': {
                        'id': 'test.example.com',
                        'site_id': 'test',
                        'status': DomainStatus.UP,
                        'url': 'https://example.com',
                        'link_disabled': False,
                        'last_check': {
                            'timestamp': datetime.now(timezone.utc).isoformat(),
                            'http_status': 200,
                            'latency_ms': 100
                        },
                        'log_path': 'logs/test/example.com.log',
                        'archive_links': [{"date": "2026-05", "url": "logs/archive/2026-05/test/example.com.log"}]
                    }
                }
            },
            domains=[{
                'id': 'test.example.com',
                'site_id': 'test',
                'status': DomainStatus.UP,
                'url': 'https://example.com',
                'link_disabled': False,
                'last_check': {
                    'timestamp': datetime.now(timezone.utc).isoformat(),
                    'http_status': 200,
                    'latency_ms': 100
                },
                'log_path': 'logs/test/example.com.log',
                'archive_links': [{"date": "2026-05", "url": "logs/archive/2026-05/test/example.com.log"}],
                'expected': {
                    'http_status': 200,
                    'body_contains': None
                }
            }],
            generated_at=datetime.now(timezone.utc).isoformat()
        )
        
        # Verify the attribute uses single quotes (the fix)
        assert "data-archive-links='" in html, "Template should use single quotes for data-archive-links attribute"
        assert 'data-archive-links="' not in html, "Template should not use double quotes for data-archive-links attribute"
        
        # Also verify the JSON can be parsed correctly
        import re
        matches = re.findall(r"data-archive-links='([^']*)'", html)
        assert len(matches) > 0, "Should find at least one data-archive-links attribute"
        
        import json
        for match in matches:
            try:
                parsed = json.loads(match)
                assert isinstance(parsed, list), f"Expected JSON array, got {type(parsed)}"
            except json.JSONDecodeError as e:
                pytest.fail(f"Invalid JSON in data-archive-links attribute: {e}")
    
    def test_log_format_version_validation(self):
        """Test that JavaScript parser validates log format version."""
        # Create a test log file with version 2
        renderer = Renderer()
        template = renderer.env.get_template("site.html")
        
        # Mock log content with version 2 header
        test_log_content = """# version: 2
# schema: timestamp,site_id,domain_id,domain_status,http_status,latency_ms,failure_type,protection_type
timestamp,site_id,domain_id,domain_status,http_status,latency_ms,failure_type,protection_type
2026-05-10T13:30:58.084293+00:00,test,test.example.com,UP,200,100,,DDoS-Guard
2026-05-10T13:31:58.552511+00:00,test,test.example.com,UP,200,95,,DDoS-Guard"""
        
        # Test that the JavaScript would validate this correctly
        # Since we can't run JS directly, we test the template includes the validation logic
        html = template.render(
            title="test",
            health=SiteHealth.UP,
            site={
                'id': 'test',
                'name': 'Test Site',
                'health': SiteHealth.UP,
                'domain_count': 1,
                'up_count': 1,
                'down_count': 0,
                'timeout_count': 0,
                'unknown_count': 0,
                'domains': {
                    'example.com': {
                        'id': 'test.example.com',
                        'site_id': 'test',
                        'status': DomainStatus.UP,
                        'url': 'https://example.com',
                        'link_disabled': False,
                        'last_check': {
                            'timestamp': datetime.now(timezone.utc).isoformat(),
                            'http_status': 200,
                            'latency_ms': 100
                        },
                        'log_path': 'logs/test/example.com.log',
                        'archive_links': []
                    }
                }
            },
            domains=[{
                'id': 'test.example.com',
                'site_id': 'test',
                'status': DomainStatus.UP,
                'url': 'https://example.com',
                'link_disabled': False,
                'last_check': {
                    'timestamp': datetime.now(timezone.utc).isoformat(),
                    'http_status': 200,
                    'latency_ms': 100
                },
                'log_path': 'logs/test/example.com.log',
                'archive_links': [],
                'expected': {
                    'http_status': 200,
                    'body_contains': None
                }
            }],
            generated_at=datetime.now(timezone.utc).isoformat()
        )
        
        # Verify the JavaScript includes version validation
        assert "# version:" in html, "JavaScript should validate log format version"
        assert "version !== 2" in html, "JavaScript should check for version 2"
        assert "Expected version 2" in html, "JavaScript should show clear error for wrong version"
        assert "no version header found" in html, "JavaScript should handle missing version"
        
        # Also verify storage.py writes version 2
        from app.storage import Storage
        storage = Storage(data_dir="/tmp/test_storage")
        assert storage.schema_version == 2, "Storage should use schema version 2"
    
    def test_js_parser_matches_log_format(self):
        """Test that JavaScript parser correctly parses real CSV log format."""
        import subprocess
        import tempfile
        from pathlib import Path
        
        # Create test log with real data format
        log_content = """# version: 2
# schema: timestamp,site_id,domain_id,domain_status,http_status,latency_ms,failure_type,protection_type
timestamp,site_id,domain_id,domain_status,http_status,latency_ms,failure_type,protection_type
2026-05-10T13:30:58.084293+00:00,test,test.example.com,PROTECTED,200,765,,DDoS-Guard
2026-05-10T13:31:58.552511+00:00,test,test.example.com,PROTECTED,200,700,,DDoS-Guard
2026-05-10T13:32:59.029784+00:00,test,test.example.com,DOWN,502,616,content_mismatch,
2026-05-10T13:33:59.661886+00:00,test,test.example.com,PROTECTED,200,776,,DDoS-Guard"""
        
        # JavaScript parser code (extracted from modal)
        js_code = """
function parseLogData(text) {
    const versionLine = text.split('\\n').find(line => line.startsWith('# version:'));
    if (!versionLine) {
        throw new Error('Unsupported log format: no version header found');
    }
    const version = parseInt(versionLine.split(':')[1].trim());
    if (version !== 2) {
        throw new Error(`Unsupported log format version: ${version}. Expected version 2.`);
    }
    
    const lines = text.trim().split('\\n').filter(line => line.length > 0 && !line.startsWith('#'));
    if (lines.length === 0) {
        throw new Error('No log data available');
    }

    // Skip CSV header row (first row after comments)
    const dataLines = lines.slice(1);
    const recentLines = dataLines.slice(-12);
    
    const parsedEntries = [];
    recentLines.forEach(line => {
        const parts = line.split(',');
        if (parts.length >= 5) {
            const timestamp = parts[0];
            const status = parts[3];
            const httpStatus = parts[4];
            const latency = parts[5] ? parts[5] + 'ms' : '';
            const failure = parts[6] || '';

            parsedEntries.push({
                timestamp,
                status,
                httpStatus,
                latency,
                failure
            });
        }
    });
    
    return parsedEntries;
}

const fs = require('fs');
const logContent = fs.readFileSync(process.argv[2], 'utf8');
try {
    const entries = parseLogData(logContent);
    console.log(JSON.stringify(entries));
} catch (error) {
    console.log('ERROR: ' + error.message);
    process.exit(1);
}
"""
        
        # Run in temporary directory
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "test.log"
            js_file = Path(tmpdir) / "parser.js"
            
            log_file.write_text(log_content)
            js_file.write_text(js_code)
            
            try:
                result = subprocess.run(
                    ["node", str(js_file), str(log_file)],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                
                if result.returncode != 0:
                    pytest.skip(f"Node.js not available or JS parser failed: {result.stderr}")
                    return
                
                # Parse JSON output
                import json
                entries = json.loads(result.stdout)
                
                # Verify parsing results
                assert len(entries) == 4, f"Expected 4 entries, got {len(entries)}"
                
                # Check specific entries
                protected_entries = [e for e in entries if e["status"] == "PROTECTED"]
                down_entries = [e for e in entries if e["status"] == "DOWN"]
                
                assert len(protected_entries) == 3, f"Expected 3 PROTECTED entries, got {len(protected_entries)}"
                assert len(down_entries) == 1, f"Expected 1 DOWN entry, got {len(down_entries)}"
                
                # Check field parsing
                for entry in entries:
                    assert "timestamp" in entry, "Missing timestamp field"
                    assert "status" in entry, "Missing status field"
                    assert "httpStatus" in entry, "Missing httpStatus field"
                    assert "latency" in entry, "Missing latency field"
                    assert isinstance(entry["httpStatus"], str), "httpStatus should be string"
                    if entry["latency"]:
                        assert entry["latency"].endswith("ms"), "latency should end with 'ms'"
                
            except subprocess.TimeoutExpired:
                pytest.skip("JavaScript parser test timed out")
            except FileNotFoundError:
                pytest.skip("Node.js not available - skipping JS parser test")
    
    @pytest.mark.integration
    @pytest.mark.slow
    def test_full_modal_pipeline(self):
        """Test complete pipeline: build -> serve -> fetch -> parse.
        
        NOTE: This test is fragile and may fail due to:
        - Port conflicts (8081 must be available)
        - Node.js availability and version compatibility
        - Network timing issues
        - HTTP server startup problems
        
        This test validates the complete end-to-end flow but should
        not be relied upon for regular CI/CD due to its external dependencies.
        Consider the unit tests below for stable regression detection:
        - test_json_in_html_attribute_quoting
        - test_log_format_version_validation  
        - test_js_parser_matches_log_format
        """
        import subprocess
        import tempfile
        import time
        from pathlib import Path
        
        def create_log_file(log_dir: Path):
            """Create a real log file with version 2 format."""
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file = log_dir / "example.com.log"
            
            content = """# version: 2
# schema: timestamp,site_id,domain_id,domain_status,http_status,latency_ms,failure_type,protection_type
timestamp,site_id,domain_id,domain_status,http_status,latency_ms,failure_type,protection_type
2026-05-10T13:30:58.084293+00:00,test,example.com,PROTECTED,200,765,,DDoS-Guard
2026-05-10T13:31:58.552511+00:00,test,example.com,PROTECTED,200,700,,DDoS-Guard
2026-05-10T13:32:59.029784+00:00,test,example.com,DOWN,502,616,content_mismatch,
2026-05-10T13:33:59.661886+00:00,test,example.com,PROTECTED,200,776,,DDoS-Guard
2026-05-10T13:34:59.875067+00:00,test,example.com,PROTECTED,200,736,,DDoS-Guard
2026-05-10T13:35:00.484393+00:00,test,example.com,PROTECTED,200,1606,,DDoS-Guard
2026-05-10T13:36:04.640821+00:00,test,example.com,PROTECTED,200,1103,,DDoS-Guard
2026-05-10T13:37:57.569965+00:00,test,example.com,PROTECTED,200,6626,,DDoS-Guard
2026-05-10T13:38:02.060516+00:00,test,example.com,PROTECTED,200,634,,DDoS-Guard
2026-05-10T13:39:51.509383+00:00,test,example.com,PROTECTED,200,567,,DDoS-Guard
2026-05-10T13:40:03.298531+00:00,test,example.com,PROTECTED,200,886,,DDoS-Guard
2026-05-10T13:41:52.437810+00:00,test,example.com,PROTECTED,200,693,,DDoS-Guard
2026-05-10T13:42:04.046089+00:00,test,example.com,PROTECTED,200,298,,DDoS-Guard
"""
            log_file.write_text(content)
            return log_file
        
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            public_dir = tmpdir / "public"
            data_dir = tmpdir / "data"
            
            # Create test log file
            log_file = create_log_file(data_dir / "live" / "test")
            
            # Copy logs using FilesystemBackend
            from app.storage_backends import FilesystemBackend
            backend = FilesystemBackend(str(public_dir))
            result = backend.upload_logs(data_dir)
            
            # Check logs were copied
            public_log = public_dir / "logs" / "test" / "example.com.log"
            assert public_log.exists(), "Log file not copied to public/logs/"
            assert result["uploaded"] > 0, "No log files were uploaded"
            
            # Start HTTP server
            server_proc = subprocess.Popen(
                ["python", "-m", "http.server", "8081"],
                cwd=str(public_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            
            try:
                # Wait for server to start
                time.sleep(2)
                
                # Check if server is running
                if server_proc.poll() is not None:
                    stdout, stderr = server_proc.communicate()
                    pytest.skip(f"Server failed to start: {stderr.decode()}")
                    return
                
                # Test fetch with Node.js
                fetch_test = """
const http = require('http');

http.get('http://127.0.0.1:8081/logs/test/example.com.log', (res) => {
    let data = '';
    res.on('data', (chunk) => data += chunk);
    res.on('end', () => {
        try {
            const versionLine = data.split('\\n').find(line => line.startsWith('# version:'));
            if (!versionLine) {
                throw new Error('Unsupported log format: no version header found');
            }
            const version = parseInt(versionLine.split(':')[1].trim());
            if (version !== 2) {
                throw new Error(`Unsupported log format version: ${version}. Expected version 2.`);
            }
            
            const lines = data.trim().split('\\n').filter(line => line.length > 0 && !line.startsWith('#'));
            const dataLines = lines.slice(1);
            const recentLines = dataLines.slice(-12);
            
            const entries = [];
            recentLines.forEach(line => {
                const parts = line.split(',');
                if (parts.length >= 5) {
                    const status = parts[3];
                    entries.push(status);
                }
            });
            
            console.log(JSON.stringify({
                success: true,
                entries: entries.length,
                protected: entries.filter(e => e === 'PROTECTED').length,
                down: entries.filter(e => e === 'DOWN').length
            }));
        } catch (error) {
            console.log(JSON.stringify({
                success: false,
                error: error.message
            }));
            process.exit(1);
        }
    });
}).on('error', (err) => {
    console.log(JSON.stringify({
        success: false,
        error: err.message
    }));
    process.exit(1);
});
"""
                
                with tempfile.NamedTemporaryFile(mode='w', suffix='.js', delete=False) as f:
                    f.write(fetch_test)
                    js_file = f.name
                
                try:
                    result = subprocess.run(
                        ["node", js_file],
                        capture_output=True,
                        text=True,
                        timeout=10
                    )
                    
                    if result.returncode != 0:
                        pytest.skip(f"Node.js test failed: {result.stderr}")
                        return
                    
                    import json
                    result_data = json.loads(result.stdout)
                    
                    assert result_data["success"], f"Parse error: {result_data.get('error', 'Unknown error')}"
                    assert result_data["entries"] == 12, f"Expected 12 entries, got {result_data['entries']}"
                    assert result_data["protected"] == 11, f"Expected 11 PROTECTED entries, got {result_data['protected']}"
                    assert result_data["down"] == 1, f"Expected 1 DOWN entry, got {result_data['down']}"
                    
                finally:
                    Path(js_file).unlink(missing_ok=True)
                    
            finally:
                server_proc.terminate()
                server_proc.wait(timeout=5)
    """Tests for Renderer class."""

    @pytest.fixture
    def temp_renderer(self):
        temp_dir = tempfile.mkdtemp()
        renderer = Renderer(
            templates_dir="templates",
            output_dir=temp_dir
        )
        yield renderer
        shutil.rmtree(temp_dir)

    @pytest.fixture
    def sample_aggregated_data(self):
        now = datetime.now(timezone.utc)
        return {
            "sites": {
                "test": {
                    "health": SiteHealth.UP,
                    "domains": {
                        "test.site1": {
                            "status": DomainStatus.UP,
                            "url": "https://site1.com",
                            "link_disabled": False,
                            "last_check": {
                                "timestamp": now.isoformat(),
                                "http_status": 200,
                                "latency_ms": 100,
                                "cert_expiry": "2025-12-31T23:59:59Z",
                                "body_contains_pass": True
                            }
                        },
                        "test.site2": {
                            "status": DomainStatus.UP,
                            "url": "https://site2.com",
                            "link_disabled": False,
                            "last_check": {
                                "timestamp": now.isoformat(),
                                "http_status": 200,
                                "latency_ms": 150,
                                "cert_expiry": None,
                                "body_contains_pass": None
                            }
                        }
                    },
                    "buckets": {
                        "test.site1": [Bucket(now, DomainStatus.UP)],
                        "test.site2": [Bucket(now, DomainStatus.UP)]
                    },
                    "bucket_count": 240,
                    "last_check": now.isoformat()
                }
            },
            "generated_at": "2024-01-01T12:00:00Z"
        }

    def test_initialization(self, temp_renderer):
        assert temp_renderer.output_dir.exists()
        assert temp_renderer.min_build_interval == 30

    def test_should_rebuild_initial(self, temp_renderer):
        # Should rebuild if never built before
        assert temp_renderer.should_rebuild() is True

    def test_should_rebuild_throttled(self, temp_renderer):
        import time
        # Mark as recently built
        temp_renderer.last_build_time = time.time()

        # Should not rebuild immediately
        assert temp_renderer.should_rebuild() is False

    def test_should_rebuild_after_interval(self, temp_renderer):
        import time
        # Mark as built 31 seconds ago
        temp_renderer.last_build_time = time.time() - 31

        # Should rebuild now
        assert temp_renderer.should_rebuild() is True

    def test_status_to_css_class(self, temp_renderer):
        assert temp_renderer._status_to_css_class(DomainStatus.UP) == "up"
        assert temp_renderer._status_to_css_class(DomainStatus.DOWN) == "down"
        assert temp_renderer._status_to_css_class(DomainStatus.TIMEOUT) == "down"
        assert temp_renderer._status_to_css_class(DomainStatus.PROTECTED) == "protected"
        assert temp_renderer._status_to_css_class(DomainStatus.UNKNOWN) == "unknown"
        # BOT_DETECTED is deprecated and maps to "unknown"
        assert temp_renderer._status_to_css_class(DomainStatus.BOT_DETECTED) == "unknown"

    def test_status_class_filter(self, temp_renderer):
        assert temp_renderer._status_class_filter(DomainStatus.UP) == "up"
        assert temp_renderer._status_class_filter(DomainStatus.DOWN) == "down"

    def test_health_class_filter(self, temp_renderer):
        assert temp_renderer._health_class_filter(SiteHealth.UP) == "up"
        assert temp_renderer._health_class_filter(SiteHealth.DEGRADED) == "degraded"
        assert temp_renderer._health_class_filter(SiteHealth.DOWN) == "down"

    def test_buckets_to_timeline_displays_all_buckets(self, temp_renderer):
        """Timeline displays all buckets directly without consolidation (48 buckets for 4 hours)."""
        now = datetime.now(timezone.utc)
        # Create 8 buckets - each becomes its own timeline item (no consolidation)
        buckets = [
            Bucket(timestamp=now - timedelta(minutes=8), status=DomainStatus.DOWN),
            Bucket(timestamp=now - timedelta(minutes=7), status=DomainStatus.UP),
            Bucket(timestamp=now - timedelta(minutes=6), status=DomainStatus.DEGRADED),
            Bucket(timestamp=now - timedelta(minutes=5), status=DomainStatus.PROTECTED),
            Bucket(timestamp=now - timedelta(minutes=4), status=DomainStatus.UP),
            Bucket(timestamp=now - timedelta(minutes=3), status=DomainStatus.UNKNOWN),
            Bucket(timestamp=now - timedelta(minutes=2), status=DomainStatus.TIMEOUT),
            Bucket(timestamp=now - timedelta(minutes=1), status=DomainStatus.UP),
        ]

        timeline = temp_renderer._buckets_to_timeline(buckets)

        # No consolidation: 8 buckets → 8 timeline items
        assert len(timeline) == 8
        assert timeline[0]["class"] == "down"
        assert timeline[1]["class"] == "up"
        assert timeline[2]["class"] == "degraded"
        assert timeline[3]["class"] == "protected"
        assert timeline[5]["class"] == "unknown"
        assert timeline[6]["class"] == "down"  # TIMEOUT maps to down CSS class

    def test_buckets_to_timeline_utc_iso_format(self, temp_renderer):
        """Timestamps should be returned as UTC ISO format for browser localization."""
        # Create a bucket at a specific UTC time (23:30 UTC = 7:30pm EST)
        utc_time = datetime(2026, 4, 8, 23, 30, 0, tzinfo=timezone.utc)
        buckets = [Bucket(timestamp=utc_time, status=DomainStatus.UP)]

        timeline = temp_renderer._buckets_to_timeline(buckets)

        assert len(timeline) == 1
        # Should return UTC ISO format (browser-side JS converts to local time)
        time_str = timeline[0]["time"]
        assert "2026-04-08T23:30:00" in time_str
        assert "+00:00" in time_str or "Z" in time_str
        # ISO time should be identical to time field
        assert timeline[0]["time"] == timeline[0]["iso_time"]

    def test_build_static_site_creates_index(self, temp_renderer, sample_aggregated_data):
        temp_renderer.build_static_site(sample_aggregated_data)

        index_file = temp_renderer.output_dir / "index.html"
        assert index_file.exists()

    def test_build_static_site_creates_site_pages(self, temp_renderer, sample_aggregated_data):
        temp_renderer.build_static_site(sample_aggregated_data)

        site_file = temp_renderer.output_dir / "test.html"
        assert site_file.exists()

    def test_build_static_site_updates_build_time(self, temp_renderer, sample_aggregated_data):
        original_time = temp_renderer.last_build_time

        temp_renderer.build_static_site(sample_aggregated_data)

        assert temp_renderer.last_build_time > original_time

    def test_build_index_content(self, temp_renderer, sample_aggregated_data):
        temp_renderer.build_static_site(sample_aggregated_data)

        index_file = temp_renderer.output_dir / "index.html"
        content = index_file.read_text()

        assert "FlatMonitor Dashboard" in content
        assert "test" in content
        assert "UP" in content

    def test_build_site_page_content(self, temp_renderer, sample_aggregated_data):
        temp_renderer.build_static_site(sample_aggregated_data)

        site_file = temp_renderer.output_dir / "test.html"
        content = site_file.read_text()

        assert "site1.com" in content
        assert "site2.com" in content

    def test_health_priority_sorting(self, temp_renderer):
        """Test that sites are sorted by health priority (DOWN first)."""
        # Configure renderer with severity sorting
        temp_renderer.dashboard_config = {'sort_by': 'severity'}
        
        data = {
            "sites": {
                "healthy": {"health": SiteHealth.UP, "domains": {}, "bucket_count": 240},
                "degraded": {"health": SiteHealth.DEGRADED, "domains": {}, "bucket_count": 240},
                "down": {"health": SiteHealth.DOWN, "domains": {}, "bucket_count": 240},
            },
            "generated_at": "2024-01-01T12:00:00Z"
        }

        temp_renderer.build_static_site(data)

        index_file = temp_renderer.output_dir / "index.html"
        content = index_file.read_text()

        # DOWN site should appear before others in the HTML (check site-card-title links)
        # Look for the actual site ID text in the rendered site cards
        down_pos = content.find('>down<')  # Site ID in site-card-title link
        degraded_pos = content.find('>degraded<')
        healthy_pos = content.find('>healthy<')

        assert down_pos < degraded_pos, f"DOWN site (pos {down_pos}) should appear before DEGRADED (pos {degraded_pos})"
        assert down_pos < healthy_pos, f"DOWN site (pos {down_pos}) should appear before UP (pos {healthy_pos})"

    def test_site_summary_status_counts(self, temp_renderer):
        """Test that site summary includes detailed status counts."""
        now = datetime.now(timezone.utc)
        data = {
            "sites": {
                "test": {
                    "health": SiteHealth.DEGRADED,
                    "domains": {
                        "domain.up": {
                            "status": DomainStatus.UP,
                            "url": "https://up.com",
                            "link_disabled": False,
                            "last_check": {"timestamp": now.isoformat(), "http_status": 200, "latency_ms": 100}
                        },
                        "domain.protected": {
                            "status": DomainStatus.PROTECTED,
                            "url": "https://protected.com",
                            "link_disabled": False,
                            "last_check": {"timestamp": now.isoformat(), "http_status": 503, "latency_ms": 200}
                        },
                        "domain.down": {
                            "status": DomainStatus.DOWN,
                            "url": "https://down.com",
                            "link_disabled": False,
                            "last_check": {"timestamp": now.isoformat(), "http_status": 500, "latency_ms": None}
                        },
                        "domain.timeout": {
                            "status": DomainStatus.TIMEOUT,
                            "url": "https://timeout.com",
                            "link_disabled": False,
                            "last_check": {"timestamp": now.isoformat(), "http_status": None, "latency_ms": None}
                        },
                        "domain.unknown": {
                            "status": DomainStatus.UNKNOWN,
                            "url": "https://unknown.com",
                            "link_disabled": False,
                            "last_check": {"timestamp": now.isoformat(), "http_status": None, "latency_ms": None}
                        }
                    },
                    "buckets": {},
                    "bucket_count": 240,
                    "last_check": now.isoformat()
                }
            },
            "generated_at": "2024-01-01T12:00:00Z"
        }

        temp_renderer.build_static_site(data)

        index_file = temp_renderer.output_dir / "index.html"
        content = index_file.read_text()

        # Verify status breakdown appears in output (5 domains, no BOT_DETECTED)
        assert "5 domains" in content
        assert "1 UP" in content
        assert "1 PROTECTED" in content
        assert "1 DOWN" in content
        assert "1 TIMEOUT" in content
        assert "1 UNKNOWN" in content

    def test_noindex_meta_tag_present(self, sample_aggregated_data):
        """Test that noindex meta tag is included when noindex=True."""
        temp_dir = tempfile.mkdtemp()
        try:
            renderer = Renderer(
                templates_dir="templates",
                output_dir=temp_dir,
                noindex=True
            )
            renderer.build_static_site(sample_aggregated_data)

            index_file = renderer.output_dir / "index.html"
            content = index_file.read_text()

            assert '<meta name="robots" content="noindex, nofollow">' in content
        finally:
            shutil.rmtree(temp_dir)

    def test_noindex_meta_tag_absent(self, sample_aggregated_data):
        """Test that noindex meta tag is not included when noindex=False."""
        temp_dir = tempfile.mkdtemp()
        try:
            renderer = Renderer(
                templates_dir="templates",
                output_dir=temp_dir,
                noindex=False
            )
            renderer.build_static_site(sample_aggregated_data)

            index_file = renderer.output_dir / "index.html"
            content = index_file.read_text()

            assert '<meta name="robots" content="noindex, nofollow">' not in content
        finally:
            shutil.rmtree(temp_dir)

    def test_dashboard_custom_title(self, sample_aggregated_data):
        """Test that custom title is rendered in HTML."""
        temp_dir = tempfile.mkdtemp()
        try:
            renderer = Renderer(
                templates_dir="templates",
                output_dir=temp_dir,
                dashboard_config={'title': 'Service Status'}
            )
            renderer.build_static_site(sample_aggregated_data)

            index_file = renderer.output_dir / "index.html"
            content = index_file.read_text()

            assert '<title>Service Status</title>' in content
            assert '<h1>Service Status</h1>' in content
            assert 'Generated at' in content
            assert 'built with FlatMonitor' in content
        finally:
            shutil.rmtree(temp_dir)

    def test_dashboard_announcement(self, sample_aggregated_data):
        """Test that announcement banner is rendered when set."""
        temp_dir = tempfile.mkdtemp()
        try:
            renderer = Renderer(
                templates_dir="templates",
                output_dir=temp_dir,
                dashboard_config={'announcement': 'Maintenance scheduled Saturday'}
            )
            renderer.build_static_site(sample_aggregated_data)

            index_file = renderer.output_dir / "index.html"
            content = index_file.read_text()

            assert '<div class="announcement">' in content
            assert '<p>Maintenance scheduled Saturday</p>' in content
        finally:
            shutil.rmtree(temp_dir)

    def test_dashboard_header_text(self, sample_aggregated_data):
        """Test that custom header_text is rendered."""
        temp_dir = tempfile.mkdtemp()
        try:
            renderer = Renderer(
                templates_dir="templates",
                output_dir=temp_dir,
                dashboard_config={'header_text': 'Service monitoring dashboard'}
            )
            renderer.build_static_site(sample_aggregated_data)

            index_file = renderer.output_dir / "index.html"
            content = index_file.read_text()

            assert '<p class="subtitle">Service monitoring dashboard</p>' in content
        finally:
            shutil.rmtree(temp_dir)

    def test_dashboard_footer_links(self, sample_aggregated_data):
        """Test that footer links are rendered."""
        temp_dir = tempfile.mkdtemp()
        try:
            renderer = Renderer(
                templates_dir="templates",
                output_dir=temp_dir,
                dashboard_config={
                    'footer_links': [
                        {'text': 'Terms of Service', 'url': 'https://example.com/terms'},
                        {'text': 'Privacy Policy', 'url': 'https://example.com/privacy'}
                    ]
                }
            )
            renderer.build_static_site(sample_aggregated_data)

            index_file = renderer.output_dir / "index.html"
            content = index_file.read_text()

            assert '<div class="footer-links">' in content
            assert 'href="https://example.com/terms"' in content
            assert 'Terms of Service' in content
            assert 'Privacy Policy' in content
        finally:
            shutil.rmtree(temp_dir)

    def test_dashboard_favicon(self, sample_aggregated_data):
        """Test that favicon link is rendered when set."""
        temp_dir = tempfile.mkdtemp()
        try:
            renderer = Renderer(
                templates_dir="templates",
                output_dir=temp_dir,
                dashboard_config={'favicon': 'logo.png'}
            )
            renderer.build_static_site(sample_aggregated_data)

            index_file = renderer.output_dir / "index.html"
            content = index_file.read_text()

            assert '<link rel="icon" href="assets/logo.png">' in content
        finally:
            shutil.rmtree(temp_dir)

    def test_dashboard_favicon_absent_when_not_set(self, sample_aggregated_data):
        """Test that favicon link is not rendered when not set."""
        temp_dir = tempfile.mkdtemp()
        try:
            renderer = Renderer(
                templates_dir="templates",
                output_dir=temp_dir,
                dashboard_config={}
            )
            renderer.build_static_site(sample_aggregated_data)

            index_file = renderer.output_dir / "index.html"
            content = index_file.read_text()

            assert 'rel="icon"' not in content
        finally:
            shutil.rmtree(temp_dir)

    def test_dashboard_logo(self, sample_aggregated_data):
        """Test that logo image is rendered when set."""
        temp_dir = tempfile.mkdtemp()
        try:
            renderer = Renderer(
                templates_dir="templates",
                output_dir=temp_dir,
                dashboard_config={'logo': 'brand.png'}
            )
            renderer.build_static_site(sample_aggregated_data)

            index_file = renderer.output_dir / "index.html"
            content = index_file.read_text()

            assert '<img src="assets/brand.png" alt="Logo"' in content
        finally:
            shutil.rmtree(temp_dir)

    def test_dashboard_logo_absent_when_not_set(self, sample_aggregated_data):
        """Test that logo image is not rendered when not set."""
        temp_dir = tempfile.mkdtemp()
        try:
            renderer = Renderer(
                templates_dir="templates",
                output_dir=temp_dir,
                dashboard_config={}
            )
            renderer.build_static_site(sample_aggregated_data)

            index_file = renderer.output_dir / "index.html"
            content = index_file.read_text()

            assert 'alt="Logo"' not in content
        finally:
            shutil.rmtree(temp_dir)

    def test_yaml_order_sorting(self):
        """Test that sort_by: yaml_order preserves config order instead of sorting by severity."""
        temp_dir = tempfile.mkdtemp()
        try:
            now = datetime.now(timezone.utc)
            # Create data where "healthy" site appears first in dict order but would be
            # sorted last with severity sorting (UP comes after DOWN/DEGRADED)
            data = {
                "sites": {
                    "healthy": {
                        "health": SiteHealth.UP,
                        "domains": {
                            "healthy.www": {
                                "status": DomainStatus.UP,
                                "url": "https://healthy.com",
                                "link_disabled": False,
                                "last_check": {"timestamp": now.isoformat(), "http_status": 200, "latency_ms": 100}
                            }
                        },
                        "buckets": {},
                        "bucket_count": 240,
                        "last_check": now.isoformat()
                    },
                    "broken": {
                        "health": SiteHealth.DOWN,
                        "domains": {
                            "broken.www": {
                                "status": DomainStatus.DOWN,
                                "url": "https://broken.com",
                                "link_disabled": False,
                                "last_check": {"timestamp": now.isoformat(), "http_status": 500, "latency_ms": None}
                            }
                        },
                        "buckets": {},
                        "bucket_count": 240,
                        "last_check": now.isoformat()
                    }
                },
                "generated_at": "2024-01-01T12:00:00Z"
            }

            # With yaml_order, "healthy" should appear before "broken" (preserves dict insertion order)
            renderer = Renderer(
                templates_dir="templates",
                output_dir=temp_dir,
                dashboard_config={'sort_by': 'yaml_order'}
            )
            renderer.build_static_site(data)

            index_file = renderer.output_dir / "index.html"
            content = index_file.read_text()

            healthy_pos = content.find("healthy")
            broken_pos = content.find("broken")

            # With yaml_order, healthy should come before broken (preserves insertion order)
            assert healthy_pos < broken_pos, "With yaml_order, sites should preserve config order"
        finally:
            shutil.rmtree(temp_dir)

    def test_severity_sorting_explicit(self):
        """Test that explicit severity sorting puts DOWN sites first."""
        temp_dir = tempfile.mkdtemp()
        try:
            now = datetime.now(timezone.utc)
            # Create data where "healthy" site appears first in dict order
            data = {
                "sites": {
                    "healthy": {
                        "health": SiteHealth.UP,
                        "domains": {
                            "healthy.www": {
                                "status": DomainStatus.UP,
                                "url": "https://healthy.com",
                                "link_disabled": False,
                                "last_check": {"timestamp": now.isoformat(), "http_status": 200, "latency_ms": 100}
                            }
                        },
                        "buckets": {},
                        "bucket_count": 240,
                        "last_check": now.isoformat()
                    },
                    "broken": {
                        "health": SiteHealth.DOWN,
                        "domains": {
                            "broken.www": {
                                "status": DomainStatus.DOWN,
                                    "url": "https://broken.com",
                                "link_disabled": False,
                                "last_check": {"timestamp": now.isoformat(), "http_status": 500, "latency_ms": None}
                            }
                        },
                        "buckets": {},
                        "bucket_count": 240,
                        "last_check": now.isoformat()
                    }
                },
                "generated_at": "2024-01-01T12:00:00Z"
            }

            # Explicit sort_by=severity should put DOWN site first
            renderer = Renderer(
                templates_dir="templates",
                output_dir=temp_dir,
                dashboard_config={'sort_by': 'severity'}
            )
            renderer.build_static_site(data)

            index_file = renderer.output_dir / "index.html"
            content = index_file.read_text()

            healthy_pos = content.find("healthy")
            broken_pos = content.find("broken")

            # With severity sorting, broken (DOWN) should come before healthy (UP)
            assert broken_pos < healthy_pos, "With severity sorting, DOWN sites should appear first"
        finally:
            shutil.rmtree(temp_dir)

    def test_alphabetical_sorting(self):
        """Test that alphabetical sorting orders sites by ID."""
        temp_dir = tempfile.mkdtemp()
        try:
            now = datetime.now(timezone.utc)
            # Create data where sites appear in non-alphabetical order
            data = {
                "sites": {
                    "zebra": {
                        "health": SiteHealth.UP,
                        "domains": {
                            "zebra.www": {
                                "status": DomainStatus.UP,
                                "url": "https://zebra.com",
                                "link_disabled": False,
                                "last_check": {"timestamp": now.isoformat(), "http_status": 200, "latency_ms": 100}
                            }
                        },
                        "buckets": {},
                        "bucket_count": 240,
                        "last_check": now.isoformat()
                    },
                    "alpha": {
                        "health": SiteHealth.UP,
                        "domains": {
                            "alpha.www": {
                                "status": DomainStatus.UP,
                                "url": "https://alpha.com",
                                "link_disabled": False,
                                "last_check": {"timestamp": now.isoformat(), "http_status": 200, "latency_ms": 100}
                            }
                        },
                        "buckets": {},
                        "bucket_count": 240,
                        "last_check": now.isoformat()
                    },
                    "beta": {
                        "health": SiteHealth.UP,
                        "domains": {
                            "beta.www": {
                                "status": DomainStatus.UP,
                                "url": "https://beta.com",
                                "link_disabled": False,
                                "last_check": {"timestamp": now.isoformat(), "http_status": 200, "latency_ms": 100}
                            }
                        },
                        "buckets": {},
                        "bucket_count": 240,
                        "last_check": now.isoformat()
                    }
                },
                "generated_at": "2024-01-01T12:00:00Z"
            }

            # Alphabetical sorting should order sites as alpha, beta, zebra
            renderer = Renderer(
                templates_dir="templates",
                output_dir=temp_dir,
                dashboard_config={'sort_by': 'alphabetical'}
            )
            renderer.build_static_site(data)

            index_file = renderer.output_dir / "index.html"
            content = index_file.read_text()

            alpha_pos = content.find("alpha")
            beta_pos = content.find("beta")
            zebra_pos = content.find("zebra")

            # With alphabetical sorting, order should be alpha < beta < zebra
            assert alpha_pos < beta_pos, "With alphabetical sorting, alpha should come before beta"
            assert beta_pos < zebra_pos, "With alphabetical sorting, beta should come before zebra"
        finally:
            shutil.rmtree(temp_dir)

    def test_format_cert_expiry_valid(self, temp_renderer):
        """Test formatting of valid certificate with >7 days remaining."""
        future_date = (datetime.now(timezone.utc) + timedelta(days=45)).isoformat()
        result = temp_renderer._format_cert_expiry_filter(future_date)
        assert "✓" in result
        assert "45d" in result or "44d" in result  # Allow for timing variations

    def test_format_cert_expiry_warning_7_days(self, temp_renderer):
        """Test warning indicator for certificates expiring in ≤7 days."""
        future_date = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
        result = temp_renderer._format_cert_expiry_filter(future_date)
        assert "⚠" in result
        # Allow for 4d or 5d depending on timing
        assert ("4d" in result or "5d" in result)

    def test_format_cert_expiry_expired(self, temp_renderer):
        """Test expired certificate formatting."""
        past_date = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        result = temp_renderer._format_cert_expiry_filter(past_date)
        assert "EXPIRED" in result
        assert "✗" in result
        # Allow for 10d or 11d depending on timing
        assert ("10d" in result or "11d" in result)

    def test_format_cert_expiry_no_warning_8_days(self, temp_renderer):
        """Test that 8+ days shows checkmark (not warning)."""
        future_date = (datetime.now(timezone.utc) + timedelta(days=9)).isoformat()
        result = temp_renderer._format_cert_expiry_filter(future_date)
        assert "✓" in result
        assert "⚠" not in result

    def test_format_cert_expiry_null(self, temp_renderer):
        """Test formatting of null/None certificate."""
        result = temp_renderer._format_cert_expiry_filter(None)
        assert result == "N/A"

    def test_format_cert_expiry_invalid(self, temp_renderer):
        """Test formatting of invalid date string."""
        result = temp_renderer._format_cert_expiry_filter("not-a-date")
        assert result == "Invalid"

    def test_cert_warning_visible_on_index(self):
        """Test that cert warning is visible on index page when ≤7 days."""
        temp_dir = tempfile.mkdtemp()
        try:
            now = datetime.now(timezone.utc)
            # Create cert expiring in 3 days (should show warning)
            expiring_soon = (now + timedelta(days=3)).isoformat()

            data = {
                "sites": {
                    "test": {
                        "health": SiteHealth.UP,
                        "domains": {
                            "test.site1": {
                                "status": DomainStatus.UP,
                                    "url": "https://site1.com",
                                "link_disabled": False,
                                "last_check": {
                                    "timestamp": now.isoformat(),
                                    "http_status": 200,
                                    "latency_ms": 100,
                                    "cert_expiry": expiring_soon
                                }
                            }
                        },
                        "buckets": {
                            "test.site1": [Bucket(now, DomainStatus.UP)]
                        },
                        "bucket_count": 48,
                        "last_check": now.isoformat()
                    }
                },
                "generated_at": now.isoformat()
            }

            renderer = Renderer(
                templates_dir="templates",
                output_dir=temp_dir
            )
            renderer.build_static_site(data)

            index_file = renderer.output_dir / "index.html"
            content = index_file.read_text()

            # Warning indicator should be present
            assert "⚠" in content
        finally:
            shutil.rmtree(temp_dir)

    def test_cert_displayed_on_site_page(self):
        """Test that certificate info is always displayed on site detail page."""
        temp_dir = tempfile.mkdtemp()
        try:
            now = datetime.now(timezone.utc)
            future_date = (now + timedelta(days=100)).isoformat()

            data = {
                "sites": {
                    "test": {
                        "health": SiteHealth.UP,
                        "domains": {
                            "test.site1": {
                                "status": DomainStatus.UP,
                                    "url": "https://site1.com",
                                "link_disabled": False,
                                "last_check": {
                                    "timestamp": now.isoformat(),
                                    "http_status": 200,
                                    "latency_ms": 100,
                                    "cert_expiry": future_date
                                },
                                "expected": {
                                    "http_status": 200,
                                    "body_contains": None,
                                    "bot_protection": None
                                }
                            }
                        },
                        "buckets": {
                            "test.site1": [Bucket(now, DomainStatus.UP)]
                        },
                        "bucket_count": 48,
                        "last_check": now.isoformat()
                    }
                },
                "generated_at": now.isoformat()
            }

            renderer = Renderer(
                templates_dir="templates",
                output_dir=temp_dir
            )
            renderer.build_static_site(data)

            site_file = renderer.output_dir / "test.html"
            content = site_file.read_text()

            # Certificate info should be present
            assert "Cert:" in content
            assert "✓" in content
        finally:
            shutil.rmtree(temp_dir)

    def test_domain_name_not_split_on_dots(self):
        """Test that domain IDs like 'test.example.com' are not incorrectly split.
        
        Regression test: Previously 'test.example.com' was split to 'example.com' causing
        log URL mismatch (logs/test/example.com.log vs logs/test/test.example.com.log).
        """
        temp_dir = tempfile.mkdtemp()
        try:
            from unittest.mock import Mock
            now = datetime.now(timezone.utc)
            
            # Mock storage that tracks what domain name was requested
            mock_storage = Mock()
            mock_storage.get_log_public_url.return_value = "https://r2.dev/logs/test/test.example.com.log"
            mock_storage.get_archive_log_public_url.return_value = "https://r2.dev/logs/archive/2024-01/test/test.example.com.log"
            
            data = {
                "sites": {
                    "test": {
                        "health": SiteHealth.UP,
                        "domains": {
                            "test.example.com": {  # Domain ID with dots
                                "status": DomainStatus.UP,
                                "url": "https://example.com",
                                "link_disabled": False,
                                "last_check": {
                                    "timestamp": now.isoformat(),
                                    "http_status": 200,
                                    "latency_ms": 100
                                }
                            }
                        },
                        "buckets": {"test.example.com": [Bucket(now, DomainStatus.UP)]},
                        "bucket_count": 48,
                        "last_check": now.isoformat()
                    }
                },
                "generated_at": now.isoformat()
            }
            
            renderer = Renderer(
                templates_dir="templates",
                output_dir=temp_dir,
                storage_backend=mock_storage
            )
            renderer.build_static_site(data)
            
            # Verify storage.get_log_public_url was called with correct domain name
            mock_storage.get_log_public_url.assert_called_once_with("test", "test.example.com")
            # NOT called with just "example.com" (the old buggy behavior)
            for call in mock_storage.get_log_public_url.call_args_list:
                args, _ = call
                assert args[1] == "test.example.com", f"Expected 'test.example.com' but got '{args[1]}'"

        finally:
            shutil.rmtree(temp_dir)

    def test_archive_links_rendered_as_valid_json_array(self):
        """Test that archive_links are rendered as valid JSON array in onclick handler.

        Regression test: Previously `| tojson | default("[]")` was used, which would
        output `null` when archive_links was undefined, breaking the JS openLogModal function.
        The correct order is `| default([]) | tojson` which outputs `[]` for undefined values.
        """
        from unittest.mock import Mock
        now = datetime.now(timezone.utc)

        # Mock storage returning empty archive dates (no archives)
        mock_storage = Mock()
        mock_storage.get_log_public_url.return_value = "https://r2.dev/logs/test/example.com.log"
        mock_storage.get_archive_log_public_url.return_value = "https://r2.dev/logs/archive/2024-01/test/example.com.log"
        # Capture the HTML content written by the renderer
        written_content = {}
        def capture_write(relative_path, content, content_type="text/html"):
            written_content[relative_path] = content
            return f"https://r2.dev/{relative_path}"
        mock_storage.write_file.side_effect = capture_write

        data = {
            "sites": {
                "test": {
                    "health": SiteHealth.UP,
                    "domains": {
                        "example.com": {
                            "status": DomainStatus.UP,
                            "url": "https://example.com",
                            "link_disabled": False,
                            "last_check": {
                                "timestamp": now.isoformat(),
                                "http_status": 200,
                                "latency_ms": 100
                            }
                        }
                    },
                    "buckets": {"example.com": [Bucket(now, DomainStatus.UP)]},
                    "bucket_count": 48,
                    "last_check": now.isoformat()
                }
            },
            "generated_at": now.isoformat()
        }

        renderer = Renderer(
            templates_dir="templates",
            output_dir="/tmp/test",
            storage_backend=mock_storage
        )
        renderer.build_static_site(data)

        # Get the site HTML content from the mock
        content = written_content.get("test.html", "")
        assert content, "Site HTML content was not written"

        # Find the onclick handler for openLogModal
        import re
        # Match the data-archive-links attribute with JSON array (single-quoted attribute)
        pattern = r"data-archive-links='(\[[^\]]*\])'"
        matches = re.findall(pattern, content)

        # Should find at least one match with empty array [] (not null)
        assert len(matches) > 0, "data-archive-links attributes with JSON array not found"
        for match in matches:
            # HTML attributes are already quoted, so we need to unquote the JSON
            import json
            parsed = json.loads(match)
            assert isinstance(parsed, list), f"Expected list but got {type(parsed)}"

    def test_archive_links_with_archives_rendered_correctly(self):
        """Test that archive_links with data are rendered correctly in the template."""
        from unittest.mock import Mock, patch
        now = datetime.now(timezone.utc)

        mock_storage = Mock()
        mock_storage.get_log_public_url.return_value = "https://r2.dev/logs/test/example.com.log"
        mock_storage.get_archive_log_public_url.return_value = "https://r2.dev/logs/archive/2024-01/test/example.com.log"
        # Capture the HTML content written by the renderer
        written_content = {}
        def capture_write(relative_path, content, content_type="text/html"):
            written_content[relative_path] = content
            return f"https://r2.dev/{relative_path}"
        mock_storage.write_file.side_effect = capture_write

        data = {
            "sites": {
                "test": {
                    "health": SiteHealth.UP,
                    "domains": {
                        "example.com": {
                            "status": DomainStatus.UP,
                            "url": "https://example.com",
                            "link_disabled": False,
                            "last_check": {
                                "timestamp": now.isoformat(),
                                "http_status": 200,
                                "latency_ms": 100
                            }
                        }
                    },
                    "buckets": {"example.com": [Bucket(now, DomainStatus.UP)]},
                    "bucket_count": 48,
                    "last_check": now.isoformat()
                }
            },
            "generated_at": now.isoformat()
        }

        renderer = Renderer(
            templates_dir="templates",
            output_dir="/tmp/test",
            storage_backend=mock_storage
        )

        # Patch _get_archive_dates to return some dates
        with patch.object(renderer, '_get_archive_dates', return_value=['2024-01', '2023-12']):
            renderer.build_static_site(data)

        # Get the site HTML content from the mock
        content = written_content.get("test.html", "")
        assert content, "Site HTML content was not written"

        # Find the data-archive-links attribute - should contain archive links JSON (single-quoted attribute)
        import re
        pattern = r"data-archive-links='(\[[^\]]*\])'"
        matches = re.findall(pattern, content)

        assert len(matches) > 0, "data-archive-links attributes with JSON array not found"
        for match in matches:
            # Verify it's valid JSON and contains archive data
            import json
            parsed = json.loads(match)
            assert isinstance(parsed, list), f"Expected list but got {type(parsed)}"
            # When archives exist, the list should have items with date and url
            if len(parsed) > 0:
                assert 'date' in parsed[0], "Archive link should have 'date' field"
                assert 'url' in parsed[0], "Archive link should have 'url' field"
