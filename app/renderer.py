"""
FlatMonitor - Renderer

Generates static HTML dashboard using Jinja2 templates.
"""

import os
import json
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
                 storage_backend: StorageBackend = None, data_dir: str = "data",
                 site_configs=None):
        self.templates_dir = Path(templates_dir)
        self.output_dir = Path(output_dir)
        self.noindex = noindex
        self.dashboard_config = dashboard_config or {}
        self.site_configs = {
            site_id: (cfg.model_dump() if hasattr(cfg, "model_dump") else cfg)
            for site_id, cfg in (site_configs or {}).items()
        }
        # Auto-use logo as favicon if favicon not explicitly set
        if not self.dashboard_config.get('favicon') and self.dashboard_config.get('logo'):
            self.dashboard_config['favicon'] = self.dashboard_config['logo']
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
        self.min_build_interval = self.dashboard_config.get('build_interval_seconds', 30)

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
        
        # For FilesystemBackend, copy logs to public directory for serving
        if isinstance(self.storage, FilesystemBackend):
            from app.storage import Storage
            storage = Storage(data_dir=self.data_dir)
            data_path = Path(self.data_dir)
            self.storage.upload_logs(data_path)

    def _format_coverage(self, coverage: Dict) -> str:
        checks = int(coverage.get("checks") or 0)
        hours = float(coverage.get("hours") or 0)
        if checks == 0:
            return "no data yet"
        if hours < 1:
            minutes = max(1, int(round(hours * 60)))
            return f"{minutes}m · {checks:,} check{'s' if checks != 1 else ''}"
        return f"{hours:.1f}h · {checks:,} checks"

    def _content_verified_label(self, last_check: Dict) -> str:
        value = last_check.get("body_contains_pass")
        if value is True:
            return "yes"
        if value is False:
            return "no — challenge or failed check"
        return "not configured"

    def _domain_view(self, domain_id: str, domain_info: Dict, buckets: List[Bucket]) -> Dict:
        collecting = bool(domain_info.get("collecting"))
        status = domain_info["status"]
        coverage = domain_info.get("coverage") or {}
        return {
            "id": domain_id,
            "status": status,
            "display_status": "COLLECTING" if collecting else status.value,
            "status_class": "collecting" if collecting else self._status_to_css_class(status),
            "collecting": collecting,
            "coverage": coverage,
            "coverage_label": self._format_coverage(coverage),
            "content_verified": self._content_verified_label(domain_info.get("last_check", {})),
            "url": domain_info["url"],
            "name": domain_info.get("name") or "",
            "description": domain_info.get("description") or "",
            "section": domain_info.get("section"),
            "role": domain_info.get("role"),
            "link_disabled": domain_info["link_disabled"],
            "last_check": domain_info["last_check"],
            "expected": domain_info.get("expected", {}),
            "timeline": self._buckets_to_timeline(buckets),
        }

    def _sort_domain_views(self, views: List[Dict]) -> List[Dict]:
        sort_by = self.dashboard_config.get("sort_by", "yaml_order")
        if sort_by == "alphabetical":
            return sorted(views, key=lambda d: d["id"])
        if sort_by == "severity":
            priority = {
                DomainStatus.DOWN: 0,
                DomainStatus.TIMEOUT: 1,
                DomainStatus.UNKNOWN: 2,
                DomainStatus.DEGRADED: 2,
                DomainStatus.PROTECTED: 3,
                DomainStatus.UP: 4,
            }
            return sorted(views, key=lambda d: (0 if d["collecting"] else priority.get(d["status"], 5), d["id"]))
        return views

    def _count_views(self, views: List[Dict]) -> Dict:
        counts = {"total": len(views), "up": 0, "protected": 0, "down": 0,
                  "timeout": 0, "unknown": 0, "collecting": 0}
        for view in views:
            if view.get("collecting"):
                counts["collecting"] += 1
                continue
            status = view["status"]
            if status == DomainStatus.UP:
                counts["up"] += 1
            elif status == DomainStatus.PROTECTED:
                counts["protected"] += 1
            elif status == DomainStatus.DOWN:
                counts["down"] += 1
            elif status == DomainStatus.TIMEOUT:
                counts["timeout"] += 1
            else:
                counts["unknown"] += 1
        return counts

    def _counts_label(self, counts: Dict) -> str:
        parts = [f"{counts['total']} domain{'s' if counts['total'] != 1 else ''}"]
        for key, label in (("up", "UP"), ("protected", "PROTECTED"), ("down", "DOWN"),
                           ("timeout", "TIMEOUT"), ("unknown", "UNKNOWN"),
                           ("collecting", "COLLECTING")):
            if counts.get(key):
                parts.append(f"{counts[key]} {label}")
        return " · ".join(parts)

    def _group_site_domains(self, site_meta: Dict, views: List[Dict]):
        sections_cfg = site_meta.get("sections") or {}
        known_sections = set(sections_cfg)
        default_domains = [v for v in views if not v.get("section") or v.get("section") not in known_sections]
        sections = []
        for section_id, section_cfg in sections_cfg.items():
            section_domains = [v for v in views if v.get("section") == section_id]
            if not section_domains:
                continue
            counts = self._count_views(section_domains)
            sections.append({
                "id": section_id,
                "name": section_cfg.get("name") or section_id,
                "description": section_cfg.get("description") or "",
                "domains": section_domains,
                "counts": counts,
                "counts_label": self._counts_label(counts),
            })
        return default_domains, sections

    def _write_status_json(self, site_summaries: List[Dict], generated_at: str) -> None:
        payload = {
            "generated_at": generated_at,
            "instance_label": self.dashboard_config.get("instance_label"),
            "sites": [],
        }
        for site in site_summaries:
            domains = []
            for domain in site["domains"] + [d for section in site.get("sections", []) for d in section["domains"]]:
                domains.append({
                    "id": domain["id"],
                    "url": domain["url"],
                    "name": domain.get("name") or None,
                    "description": domain.get("description") or None,
                    "section": domain.get("section"),
                    "role": domain.get("role"),
                    "status": domain["status"].value,
                    "display_status": domain["display_status"],
                    "collecting": domain["collecting"],
                    "coverage": domain["coverage"],
                    "last_check": domain["last_check"],
                })
            payload["sites"].append({
                "id": site["id"],
                "name": site["name"],
                "description": site["description"],
                "health": site["health"].value,
                "domains": domains,
            })
        self.storage.write_file(
            "status.json",
            json.dumps(payload, indent=2),
            content_type="application/json",
        )

    def _build_index(self, aggregated_data: Dict) -> None:
        """Build the main index page with global dashboard."""
        template = self.env.get_template("index.html")

        # Prepare site summaries with timeline data
        sites = aggregated_data.get("sites", {})
        site_summaries = []

        for site_id, site_data in sites.items():
            # Process domains with timeline for index page
            views = []
            for domain_id, domain_info in site_data["domains"].items():
                buckets = site_data["buckets"].get(domain_id, [])
                views.append(self._domain_view(domain_id, domain_info, buckets))

            views = self._sort_domain_views(views)
            site_meta = self.site_configs.get(site_id, {})
            default_domains, sections = self._group_site_domains(site_meta, views)
            counts = self._count_views(views)

            summary = {
                "id": site_id,
                "name": site_meta.get("name") or site_id,
                "description": site_meta.get("description") or "",
                "health": site_data["health"],
                "domains": default_domains,
                "sections": sections,
                "bucket_count": site_data["bucket_count"],
                "last_check": site_data.get("last_check", ""),
                "domain_count": counts["total"],
                "up_count": counts["up"],
                "protected_count": counts["protected"],
                "down_count": counts["down"],
                "timeout_count": counts["timeout"],
                "unknown_count": counts["unknown"],
                "collecting_count": counts["collecting"],
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
            instance_label=self.dashboard_config.get('instance_label'),
            auto_refresh_seconds=self.dashboard_config.get('auto_refresh_seconds', 0)
        )

        # Write using storage backend (returns public URL or path)
        public_url = self.storage.write_file("index.html", html, content_type="text/html")
        self._write_status_json(site_summaries, aggregated_data["generated_at"])

    def _build_site_page(self, site_id: str, site_data: Dict,
                         generated_at: str) -> None:
        """Build a detailed page for a specific site."""
        template = self.env.get_template("site.html")

        # Check if using MultiStorageBackend (cloud + local)
        from app.storage_backends import MultiStorageBackend, FilesystemBackend
        using_multi = isinstance(self.storage, MultiStorageBackend)

        site_meta = self.site_configs.get(site_id, {})
        site_name = site_meta.get("name") or site_id
        site_description = site_meta.get("description") or ""

        # Discover available archive dates for this site
        archive_dates = self._get_archive_dates(site_id)

        def build_domain_groups(use_relative_paths: bool = False):
            """Build flat and grouped domain views with storage-specific log URLs."""
            views = []
            for domain_id, domain_info in site_data["domains"].items():
                buckets = site_data["buckets"].get(domain_id, [])
                view = self._domain_view(domain_id, domain_info, buckets)
                domain_name = domain_id

                if use_relative_paths:
                    from app.models import get_log_filename
                    log_name = get_log_filename(site_id, domain_id)
                    log_path = f"logs/{site_id}/{log_name}.log"
                    archive_links = [
                        {"date": month, "url": f"logs/archive/{month}/{site_id}/{log_name}.log"}
                        for month in archive_dates
                    ]
                else:
                    log_path = self.storage.get_log_public_url(site_id, domain_name)
                    archive_links = [
                        {"date": month, "url": self.storage.get_archive_log_public_url(site_id, domain_name, month)}
                        for month in archive_dates
                    ]

                view["log_path"] = log_path
                view["archive_links"] = archive_links
                views.append(view)

            views = self._sort_domain_views(views)
            default_domains, sections = self._group_site_domains(site_meta, views)
            return views, default_domains, sections

        def render_html(domains: List[Dict], default_domains: List[Dict], sections: List[Dict]) -> str:
            """Render template with given domain data."""
            return template.render(
                site_id=site_id,
                site_name=site_name,
                site_description=site_description,
                health=site_data["health"],
                domains=domains,
                default_domains=default_domains,
                sections=sections,
                generated_at=generated_at,
                title=f"{site_name} - {self.dashboard_config.get('title', 'FlatMonitor')}",
                noindex=self.noindex,
                header_text=self.dashboard_config.get('header_text'),
                announcement=self.dashboard_config.get('announcement'),
                instance_label=self.dashboard_config.get('instance_label'),
                footer_links=self.dashboard_config.get('footer_links', []),
                favicon=self.dashboard_config.get('favicon'),
                logo=self.dashboard_config.get('logo'),
                footer_explanation=self.dashboard_config.get('footer_explanation'),
                auto_refresh_seconds=self.dashboard_config.get('auto_refresh_seconds', 0)
            )

        filename = f"{site_id}.html"

        if using_multi:
            # Use relative paths for both versions since logs are served from same domain
            # This avoids CORS issues when fetching logs via JavaScript
            domains, default_domains, sections = build_domain_groups(use_relative_paths=True)
            html = render_html(domains, default_domains, sections)

            # Write to both backends
            self.storage.primary.write_file(filename, html, content_type="text/html")
            self.storage.secondary.write_file(filename, html, content_type="text/html")
        else:
            # Single backend - use its native URL format
            domains, default_domains, sections = build_domain_groups(
                use_relative_paths=isinstance(self.storage, FilesystemBackend)
            )
            html = render_html(domains, default_domains, sections)
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
