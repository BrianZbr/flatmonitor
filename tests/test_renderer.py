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

        # DOWN site should appear before others (simple check for order)
        down_pos = content.find("down")
        degraded_pos = content.find("degraded")
        up_pos = content.find("UP")

        assert down_pos < degraded_pos or down_pos < up_pos

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
            assert '| Service Status</p>' in content
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
