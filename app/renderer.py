"""
FlatMonitor - Renderer

Generates static HTML dashboard using Jinja2 templates.
"""

import os
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.models import DomainStatus, SiteHealth
from app.aggregator import Bucket
from app.storage_backends import StorageBackend, FilesystemBackend


class Renderer:
    """Renders static HTML dashboards from aggregated data."""

    def __init__(self, templates_dir: str = "templates", output_dir: str = "public",
                 noindex: bool = False, dashboard_config=None,
                 storage_backend: StorageBackend = None, data_dir: str = "data"):
        self.templates_dir = Path(templates_dir)
        self.output_dir = Path(output_dir)
        self.noindex = noindex
        self.dashboard_config = dashboard_config or {}
        self.data_dir = data_dir

        # Initialize storage backend (default to filesystem)
        if storage_backend is None:
            self.storage = FilesystemBackend(output_dir)
        else:
            self.storage = storage_backend

        # Setup Jinja2 environment
        self.env = Environment(
            loader=FileSystemLoader(str(self.templates_dir)),
            autoescape=select_autoescape(['html', 'xml'])
        )

        # Register custom filters
        self.env.filters['status_class'] = self._status_class_filter
        self.env.filters['health_class'] = self._health_class_filter
        self.env.filters['format_time_ago'] = self._format_time_ago_filter
        self.env.filters['format_cert_expiry'] = self._format_cert_expiry_filter

        # Track last build time for throttling
        self.last_build_time = 0
        self.min_build_interval = 30  # seconds

    def should_rebuild(self) -> bool:
        """Check if enough time has passed since last build."""
        now = time.time()
        return (now - self.last_build_time) >= self.min_build_interval

    def build_static_site(self, aggregated_data: Dict) -> None:
        """Build all static HTML pages from aggregated data."""
        # Update last build time
        self.last_build_time = time.time()

        # Build index page (global dashboard)
        self._build_index(aggregated_data)

        # Build individual site pages
        sites = aggregated_data.get("sites", {})
        for site_id, site_data in sites.items():
            self._build_site_page(site_id, site_data, aggregated_data["generated_at"])

    def _build_index(self, aggregated_data: Dict) -> None:
        """Build the main index page with global dashboard."""
        template = self.env.get_template("index.html")

        # Prepare site summaries with timeline data
        sites = aggregated_data.get("sites", {})
        site_summaries = []

        for site_id, site_data in sites.items():
            # Process domains with timeline for index page
            domains_with_timeline = []
            for domain_id, domain_info in site_data["domains"].items():
                buckets = site_data["buckets"].get(domain_id, [])
                timeline = self._buckets_to_timeline(buckets)

                domains_with_timeline.append({
                    "id": domain_id,
                    "status": domain_info["status"],
                    "url": domain_info["url"],
                    "link_disabled": domain_info["link_disabled"],
                    "last_check": domain_info["last_check"],
                    "timeline": timeline
                })

            # Sort domains based on configuration
            sort_by = self.dashboard_config.get('sort_by', 'yaml_order')
            if sort_by == 'yaml_order':
                # Preserve YAML order (already in insertion order)
                pass
            elif sort_by == 'alphabetical':
                # Sort alphabetically by domain ID
                domains_with_timeline.sort(key=lambda d: d["id"])
            else:
                # severity (default fallback): sort by status severity
                def domain_sort_key(d):
                    status_priority = {
                        DomainStatus.DOWN: 0,
                        DomainStatus.TIMEOUT: 1,
                        DomainStatus.BOT_DETECTED: 2,
                        DomainStatus.UNKNOWN: 3,
                        DomainStatus.UP: 4
                    }.get(d["status"], 5)
                    return (status_priority, d["id"])

                domains_with_timeline.sort(key=domain_sort_key)

            # Calculate domain counts by status
            domain_count = len(site_data["domains"])
            domains_list = list(site_data["domains"].values())
            up_count = sum(1 for d in domains_list if d["status"] == DomainStatus.UP)
            protected_count = sum(1 for d in domains_list if d["status"] == DomainStatus.PROTECTED)
            down_count = sum(1 for d in domains_list if d["status"] == DomainStatus.DOWN)
            timeout_count = sum(1 for d in domains_list if d["status"] == DomainStatus.TIMEOUT)
            unknown_count = sum(1 for d in domains_list if d["status"] == DomainStatus.UNKNOWN)

            summary = {
                "id": site_id,
                "health": site_data["health"],
                "domains": domains_with_timeline,
                "bucket_count": site_data["bucket_count"],
                "last_check": site_data.get("last_check", ""),
                "domain_count": domain_count,
                "up_count": up_count,
                "protected_count": protected_count,
                "down_count": down_count,
                "timeout_count": timeout_count,
                "unknown_count": unknown_count
            }
            site_summaries.append(summary)

        # Sort sites based on configuration
        sort_by = self.dashboard_config.get('sort_by', 'yaml_order')
        if sort_by == 'yaml_order':
            # Preserve YAML order (sites appear in config order)
            pass
        elif sort_by == 'alphabetical':
            # Sort alphabetically by site ID
            site_summaries.sort(key=lambda s: s["id"])
        else:
            # severity: sort by health priority (DOWN first, then DEGRADED, then UP)
            health_priority = {SiteHealth.DOWN: 0, SiteHealth.DEGRADED: 1, SiteHealth.UP: 2}
            site_summaries.sort(key=lambda s: health_priority.get(s["health"], 3))

        html = template.render(
            sites=site_summaries,
            generated_at=aggregated_data["generated_at"],
            title=self.dashboard_config.get('title', 'FlatMonitor Dashboard'),
            noindex=self.noindex,
            header_text=self.dashboard_config.get('header_text'),
            announcement=self.dashboard_config.get('announcement'),
            footer_links=self.dashboard_config.get('footer_links', []),
            favicon=self.dashboard_config.get('favicon'),
            logo=self.dashboard_config.get('logo'),
            header_hint=self.dashboard_config.get('header_hint'),
            footer_explanation=self.dashboard_config.get('footer_explanation'),
            instance_label=self.dashboard_config.get('instance_label')
        )

        # Write using storage backend (returns public URL or path)
        public_url = self.storage.write_file("index.html", html, content_type="text/html")

    def _build_site_page(self, site_id: str, site_data: Dict,
                         generated_at: str) -> None:
        """Build a detailed page for a specific site."""
        template = self.env.get_template("site.html")

        # Check if using MultiStorageBackend (cloud + local)
        from app.storage_backends import MultiStorageBackend, FilesystemBackend
        using_multi = isinstance(self.storage, MultiStorageBackend)

        # Discover available archive dates for this site
        archive_dates = self._get_archive_dates(site_id)

        def build_domains_data(use_relative_paths: bool = False) -> List[Dict]:
            """Build domain data with appropriate log URLs."""
            domains = []
            for domain_id, domain_info in site_data["domains"].items():
                buckets = site_data["buckets"].get(domain_id, [])
                timeline = self._buckets_to_timeline(buckets)
                domain_name = domain_id.split(".", 1)[1] if "." in domain_id else domain_id

                if use_relative_paths:
                    # Use relative paths for local filesystem access
                    log_path = f"../data/live/{site_id}/{domain_name}.log"
                    archive_links = [
                        {"date": month, "url": f"../data/archive/{month}/{site_id}/{domain_name}.log"}
                        for month in archive_dates
                    ]
                else:
                    # Use absolute URLs from storage backend
                    log_path = self.storage.get_log_public_url(site_id, domain_name)
                    archive_links = [
                        {"date": month, "url": self.storage.get_archive_log_public_url(site_id, domain_name, month)}
                        for month in archive_dates
                    ]

                domains.append({
                    "id": domain_id,
                    "status": domain_info["status"],
                    "url": domain_info["url"],
                    "link_disabled": domain_info["link_disabled"],
                    "last_check": domain_info["last_check"],
                    "timeline": timeline,
                    "expected": domain_info.get("expected", {}),
                    "log_path": log_path,
                    "archive_links": archive_links
                })

            # Sort domains based on configuration
            sort_by = self.dashboard_config.get('sort_by', 'yaml_order')
            if sort_by == 'yaml_order':
                pass
            elif sort_by == 'alphabetical':
                domains.sort(key=lambda d: d["id"])
            else:
                def domain_sort_key(d):
                    status_priority = {
                        DomainStatus.DOWN: 0,
                        DomainStatus.TIMEOUT: 1,
                        DomainStatus.UNKNOWN: 2,
                        DomainStatus.PROTECTED: 3,
                        DomainStatus.UP: 4
                    }.get(d["status"], 5)
                    return (status_priority, d["id"])
                domains.sort(key=domain_sort_key)

            return domains

        def render_html(domains: List[Dict]) -> str:
            """Render template with given domain data."""
            return template.render(
                site_id=site_id,
                health=site_data["health"],
                domains=domains,
                generated_at=generated_at,
                title=f"{site_id} - {self.dashboard_config.get('title', 'FlatMonitor')}",
                noindex=self.noindex,
                header_text=self.dashboard_config.get('header_text'),
                announcement=self.dashboard_config.get('announcement'),
                instance_label=self.dashboard_config.get('instance_label'),
                footer_links=self.dashboard_config.get('footer_links', []),
                favicon=self.dashboard_config.get('favicon'),
                logo=self.dashboard_config.get('logo'),
                footer_explanation=self.dashboard_config.get('footer_explanation')
            )

        filename = f"{site_id}.html"

        if using_multi:
            # Generate separate HTML for cloud (absolute URLs) and local (relative paths)
            cloud_html = render_html(build_domains_data(use_relative_paths=False))
            local_html = render_html(build_domains_data(use_relative_paths=True))

            # Write cloud version to primary backend
            cloud_url = self.storage.primary.write_file(filename, cloud_html, content_type="text/html")
            # Write local version to secondary (filesystem) backend
            self.storage.secondary.write_file(filename, local_html, content_type="text/html")
        else:
            # Single backend - use its native URL format
            domains = build_domains_data(use_relative_paths=isinstance(self.storage, FilesystemBackend))
            html = render_html(domains)
            self.storage.write_file(filename, html, content_type="text/html")

    def _get_archive_dates(self, site_id: str) -> List[str]:
        """Discover available archive months for a site using the archive index.

        Returns sorted list of YYYY-MM dates (most recent first).
        Falls back to filesystem scan if index doesn't exist.
        """
        # Try to use archive index first (works for all backends)
        from app.storage import Storage
        storage = Storage(data_dir=self.data_dir)
        index = storage.read_archive_index()

        if site_id in index:
            return index[site_id][:12]  # Last 12 months

        # Fallback: scan filesystem (local deployments only)
        archive_dir = Path(self.data_dir) / "archive"
        if not archive_dir.exists():
            return []

        dates = []
        for month_dir in archive_dir.iterdir():
            if month_dir.is_dir() and (month_dir / site_id).exists():
                dates.append(month_dir.name)

        return sorted(dates, reverse=True)[:12]  # Last 12 months

    def _buckets_to_timeline(self, buckets: List[Bucket]) -> List[Dict]:
        """Convert buckets to timeline spans for visualization."""
        if not buckets:
            return []

        # Display 48 five-minute buckets (4 hours) directly - no consolidation needed
        CONSOLIDATION_FACTOR = 1

        # Status priority for consolidation (lower = worse/more important to show)
        status_priority = {
            DomainStatus.DOWN: 1,
            DomainStatus.DEGRADED: 2,
            DomainStatus.TIMEOUT: 3,
            DomainStatus.UNKNOWN: 4,
            DomainStatus.PROTECTED: 5,
            DomainStatus.UP: 6
        }

        def get_worst_status(group: List[Bucket]) -> Bucket:
            """Return bucket with the worst (highest priority) status in group."""
            return min(group, key=lambda b: status_priority.get(b.status, 99))

        timeline = []
        # Process buckets in groups of 4 (chronological order)
        for i in range(0, len(buckets), CONSOLIDATION_FACTOR):
            group = buckets[i:i + CONSOLIDATION_FACTOR]
            if not group:
                continue

            # Use worst status in group so outages aren't hidden
            representative = get_worst_status(group)

            timeline.append({
                "status": representative.status,
                "class": self._status_to_css_class(representative.status),
                "time": representative.timestamp.isoformat(),
                "iso_time": representative.timestamp.isoformat(),
                "http_status": representative.http_status,
                "failure_type": representative.failure_type,
                "latency_ms": representative.latency_ms
            })

        return timeline

    def _status_to_css_class(self, status: DomainStatus) -> str:
        """Convert status to CSS class name."""
        return {
            DomainStatus.UP: "up",
            DomainStatus.DOWN: "down",
                       DomainStatus.DEGRADED: "degraded",
            DomainStatus.PROTECTED: "protected",
            DomainStatus.TIMEOUT: "down",
            DomainStatus.UNKNOWN: "unknown"
        }.get(status, "unknown")

    def _status_class_filter(self, status: DomainStatus) -> str:
        """Jinja2 filter to convert status to CSS class."""
        return self._status_to_css_class(status)

    def _health_class_filter(self, health: SiteHealth) -> str:
        """Jinja2 filter to convert health to CSS class."""
        return {
            SiteHealth.UP: "up",
            SiteHealth.DEGRADED: "degraded",
            SiteHealth.DOWN: "down",
            SiteHealth.UNKNOWN: "unknown"
        }.get(health, "unknown")

    def _format_time_ago_filter(self, timestamp: str) -> str:
        """Jinja2 filter to format timestamp as relative time."""
        if not timestamp:
            return "never"
        try:
            dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            diff = now - dt

            if diff.total_seconds() < 60:
                return f"{int(diff.total_seconds())}s ago"
            elif diff.total_seconds() < 3600:
                return f"{int(diff.total_seconds() / 60)}m ago"
            elif diff.total_seconds() < 86400:
                return f"{int(diff.total_seconds() / 3600)}h ago"
            else:
                return f"{int(diff.total_seconds() / 86400)}d ago"
        except (ValueError, TypeError):
            return "unknown"

    def _format_cert_expiry_filter(self, cert_expiry: str) -> str:
        """Jinja2 filter to format certificate expiry with status indicator."""
        if not cert_expiry:
            return "N/A"
        try:
            from datetime import timezone
            expiry = datetime.fromisoformat(cert_expiry.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)

            # Remove timezone info for comparison if expiry has no tz
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)

            diff = expiry - now
            days = diff.days

            if days < 0:
                return f"EXPIRED ({abs(days)}d ago) ✗"
            elif days <= 7:
                return f"{expiry.strftime('%b %d')} ({days}d) ⚠"
            else:
                return f"{expiry.strftime('%b %d')} ({days}d) ✓"
        except (ValueError, TypeError):
            return "Invalid"
