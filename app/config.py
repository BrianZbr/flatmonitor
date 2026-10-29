"""
FlatMonitor - Configuration Loader

YAML loading and validation for domain configurations.
"""

import os
import re
import yaml
import logging
from typing import Dict, List
from pathlib import Path
from urllib.parse import urlparse

from app.models import DomainConfig, ExpectConfig, ExpectedBotProtection
from pydantic import BaseModel, Field
from typing import Optional, List as ListType, Dict

logger = logging.getLogger(__name__)


def expand_env_vars(value: str, context: str = "") -> str:
    """Expand environment variables in ${VAR_NAME} syntax.

    Supports:
    - ${FLATMONITOR_VAR_NAME} -> reads env var FLATMONITOR_VAR_NAME
    - Plain strings pass through unchanged

    Args:
        value: String potentially containing ${VAR_NAME} patterns
        context: Description of where this value came from (for error messages)

    Returns:
        Expanded string with env vars replaced

    Raises:
        ValueError: If an env var is not set and context indicates it's required
    """
    if not isinstance(value, str):
        return value

    unresolved = []

    def replacer(match):
        var_spec = match.group(1)
        # Support ${VAR:-default} syntax
        if ':-' in var_spec:
            var_name, default_value = var_spec.split(':-', 1)
            env_value = os.getenv(var_name)
            if env_value is not None:
                return env_value
            else:
                return default_value
        else:
            var_name = var_spec
            env_value = os.getenv(var_name)
            if env_value is not None:
                return env_value
            else:
                unresolved.append(var_name)
                return match.group(0)  # Keep original ${VAR} if not found

    result = re.sub(r'\$\{([^}]+)\}', replacer, value)

    # Log warning if any env vars were unresolved
    if unresolved:
        import logging
        logger = logging.getLogger(__name__)
        vars_str = ", ".join(f"${{{v}}}" for v in unresolved)
        msg = f"Unresolved environment variable(s) {vars_str}"
        if context:
            msg += f" in {context}"
        msg += f" (value: '{value}' -> '{result}')"
        logger.warning(msg)

    return result


class DashboardConfig(BaseModel):
    """Dashboard customization settings."""
    title: str = Field(default="FlatMonitor", description="Dashboard page title")
    header_text: Optional[str] = Field(default=None, description="Optional subtitle/description")
    announcement: Optional[str] = Field(default=None, description="Optional banner announcement")
    footer_links: ListType[Dict[str, str]] = Field(default_factory=list, description="Footer link array")
    sort_by: str = Field(default="yaml_order", description="Display order: 'yaml_order', 'severity', or 'alphabetical'")
    favicon: Optional[str] = Field(default=None, description="Favicon filename in public/assets/")
    logo: Optional[str] = Field(default=None, description="Logo filename in public/assets/")
    header_hint: Optional[str] = Field(default="Click any site title for detailed status and logs.", description="Hint shown above site grid")
    footer_explanation: Optional[str] = Field(default=None, description="Custom HTML footer text")
    instance_label: Optional[str] = Field(default=None, description="Instance label (e.g., 'US-East Primary')")
    build_interval_seconds: int = Field(default=30, ge=10, description="Minimum interval between dashboard rebuilds in seconds")
    auto_refresh_seconds: int = Field(default=120, ge=0, description="Auto-refresh interval in seconds (0 = disabled)")
    static_files: ListType[str] = Field(default_factory=list, description="Static HTML files in public/ root to upload to cloud storage")


class SiteConfig(BaseModel):
    """Display metadata for a site group. Internal IDs are unchanged."""
    name: Optional[str] = Field(default=None, description="Display name for the group")
    description: Optional[str] = Field(default=None, description="Optional short description")


class StorageConfig(BaseModel):
    """Storage backend configuration."""
    type: str = Field(default="filesystem", description="Storage type: 'filesystem', 'r2', or 's3'")
    upload_logs: bool = Field(default=True, description="Upload log files to object storage")
    filesystem: Dict = Field(default_factory=lambda: {"output_dir": "public"}, description="Filesystem backend settings")
    r2: Optional[Dict] = Field(default=None, description="R2 backend settings")
    s3: Optional[Dict] = Field(default=None, description="S3 backend settings")


class HealthConfig(BaseModel):
    """Health check configuration."""
    heartbeat_url: Optional[str] = Field(default=None, description="URL to send heartbeat GET requests to")
    heartbeat_interval_seconds: int = Field(default=60, ge=10, description="Interval between heartbeat pings in seconds")


def parse_site_configs(config_data: Dict) -> Dict[str, SiteConfig]:
    """Parse the optional top-level `sites:` display metadata map."""
    raw_sites = (config_data or {}).get("sites") or {}
    if not isinstance(raw_sites, dict):
        raise ValueError("Configuration 'sites' must be a mapping of site_id -> {name, description}")

    sites: Dict[str, SiteConfig] = {}
    for site_id, raw in raw_sites.items():
        if not isinstance(raw, dict):
            raise ValueError(f"Configuration site '{site_id}' must be a mapping")
        sites[str(site_id)] = SiteConfig(
            name=raw.get("name"),
            description=raw.get("description"),
        )
    return sites


class ConfigLoader:
    """Loads and validates domain configurations from YAML files."""

    def __init__(self, config_path: str = "config/domains.yaml"):
        self.config_path = Path(config_path)
        self.config_data: Dict = {}
        self.domains: List[DomainConfig] = []
        self.site_configs: Dict[str, SiteConfig] = {}
        self.validation_warnings: List[str] = []
        self.rotation_interval: int = 86400  # Default: 24 hours (daily rotation)
        self.retention_days: int = 7  # Default: 7 days of archive retention
        self.noindex: bool = False  # Default: allow search engine indexing
        self.dashboard: DashboardConfig = DashboardConfig()  # Default dashboard settings
        self.storage: StorageConfig = StorageConfig()  # Default filesystem storage
        self.health: HealthConfig = HealthConfig()  # Default health settings

    def load(self) -> List[DomainConfig]:
        """Load domain configurations from YAML file."""
        if not self.config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {self.config_path}")

        with open(self.config_path, "r") as f:
            try:
                self.config_data = yaml.safe_load(f)
            except yaml.YAMLError as e:
                error_msg = f"YAML syntax error in {self.config_path}\n"
                if hasattr(e, 'problem_mark'):
                    mark = e.problem_mark
                    error_msg += f"  Error at line {mark.line + 1}, column {mark.column + 1}\n"
                    error_msg += f"  {e.problem}\n"
                    error_msg += f"  Hint: Check indentation and ensure list items use consistent '- ' syntax"
                else:
                    error_msg += str(e)
                raise ValueError(error_msg) from e

        if not self.config_data or "domains" not in self.config_data:
            raise ValueError("Configuration must contain a 'domains' key")

        # Validate the raw structure and warn about unknown fields before parsing.
        self.validation_warnings = []
        self._validate_raw_config()
        self.site_configs = parse_site_configs(self.config_data)

        # Parse settings
        settings = self.config_data.get('settings', {})
        self.rotation_interval = settings.get('rotation_interval_seconds', 86400)
        self.retention_days = settings.get('retention_days', 7)
        self.noindex = settings.get('noindex', False)

        # Parse dashboard customization
        dashboard_settings = settings.get('dashboard', {})
        self.dashboard = DashboardConfig(
            title=dashboard_settings.get('title', 'FlatMonitor'),
            header_text=dashboard_settings.get('header_text'),
            announcement=dashboard_settings.get('announcement'),
            footer_links=dashboard_settings.get('footer_links', []),
            sort_by=dashboard_settings.get('sort_by', 'yaml_order'),
            favicon=dashboard_settings.get('favicon'),
            logo=dashboard_settings.get('logo'),
            header_hint=dashboard_settings.get('header_hint', 'Click any site title for detailed status and logs.'),
            footer_explanation=dashboard_settings.get('footer_explanation'),
            instance_label=dashboard_settings.get('instance_label'),
            build_interval_seconds=dashboard_settings.get('build_interval_seconds', 30),
            auto_refresh_seconds=dashboard_settings.get('auto_refresh_seconds', 120),
            static_files=dashboard_settings.get('static_files', [])
        )

        # Allow FLATMONITOR_INSTANCE_LABEL env var to override YAML config
        env_instance_label = os.environ.get('FLATMONITOR_INSTANCE_LABEL')
        if env_instance_label:
            self.dashboard.instance_label = env_instance_label
            logger.info(f"Instance label overridden by FLATMONITOR_INSTANCE_LABEL: {env_instance_label}")

        # Parse storage configuration
        storage_settings = settings.get('storage', {})
        self.storage = self._parse_storage_config(storage_settings)

        # Parse health configuration
        health_settings = settings.get('health', {})
        raw_heartbeat_url = health_settings.get('heartbeat_url')
        self.health = HealthConfig(
            heartbeat_url=expand_env_vars(raw_heartbeat_url, 'settings.health.heartbeat_url') if raw_heartbeat_url else None,
            heartbeat_interval_seconds=health_settings.get('heartbeat_interval_seconds', 60),
        )

        # Check for duplicate domain IDs before parsing
        seen_ids = set()
        duplicates = []
        for raw_domain in self.config_data["domains"]:
            domain_id = raw_domain.get("id")
            if domain_id in seen_ids:
                duplicates.append(domain_id)
            seen_ids.add(domain_id)

        if duplicates:
            raise ValueError(
                f"Duplicate domain ID(s) found in configuration: {', '.join(duplicates)}. "
                f"Each domain must have a unique 'id' field."
            )

        self.domains = []
        for raw_domain in self.config_data["domains"]:
            domain = self._parse_domain(raw_domain)
            self.domains.append(domain)

        return self.domains

    def _validate_raw_config(self) -> None:
        """Validate the raw YAML structure.

        Errors raise ValueError so a bad config cannot be deployed. Unknown keys are
        warnings so older/newer config files still load, but typos are visible.
        """
        data = self.config_data or {}
        errors: List[str] = []
        warnings: List[str] = []

        def err(msg: str) -> None:
            errors.append(msg)

        def warn(msg: str) -> None:
            warnings.append(msg)

        if not isinstance(data, dict):
            raise ValueError("Configuration root must be a mapping")

        for key in sorted(set(data) - {"settings", "sites", "domains"}):
            warn(f"unknown top-level key '{key}'")

        # settings
        settings = data.get("settings", {}) or {}
        if not isinstance(settings, dict):
            err("'settings' must be a mapping")
            settings = {}
        for key in sorted(set(settings) - {
            "rotation_interval_seconds", "retention_days", "noindex",
            "health", "dashboard", "storage"
        }):
            warn(f"unknown settings key '{key}'")
        for key, minimum in (("rotation_interval_seconds", 1), ("retention_days", 1)):
            if key in settings:
                value = settings[key]
                if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
                    err(f"settings.{key} must be an integer >= {minimum}")
        if "noindex" in settings and not isinstance(settings["noindex"], bool):
            err("settings.noindex must be a boolean")

        # dashboard
        dashboard = settings.get("dashboard", {}) or {}
        if not isinstance(dashboard, dict):
            err("settings.dashboard must be a mapping")
            dashboard = {}
        for key in sorted(set(dashboard) - set(DashboardConfig.model_fields)):
            warn(f"unknown dashboard key 'settings.dashboard.{key}'")
        if "sort_by" in dashboard and dashboard["sort_by"] not in (
            "yaml_order", "severity", "alphabetical"
        ):
            err("settings.dashboard.sort_by must be 'yaml_order', 'severity', or 'alphabetical'")
        for key, minimum in (("build_interval_seconds", 10), ("auto_refresh_seconds", 0)):
            if key in dashboard:
                value = dashboard[key]
                if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
                    err(f"settings.dashboard.{key} must be an integer >= {minimum}")
        if "footer_links" in dashboard:
            links = dashboard["footer_links"]
            if not isinstance(links, list):
                err("settings.dashboard.footer_links must be a list")
            else:
                for i, link in enumerate(links):
                    if not isinstance(link, dict) or "text" not in link or "url" not in link:
                        err(f"settings.dashboard.footer_links[{i}] must contain 'text' and 'url'")

        # health
        health = settings.get("health", {}) or {}
        if not isinstance(health, dict):
            err("settings.health must be a mapping")
            health = {}
        for key in sorted(set(health) - {"heartbeat_url", "heartbeat_interval_seconds"}):
            warn(f"unknown health key 'settings.health.{key}'")
        if "heartbeat_interval_seconds" in health:
            value = health["heartbeat_interval_seconds"]
            if not isinstance(value, int) or isinstance(value, bool) or value < 10:
                err("settings.health.heartbeat_interval_seconds must be an integer >= 10")

        # storage
        storage = settings.get("storage", {}) or {}
        if not isinstance(storage, dict):
            err("settings.storage must be a mapping")
            storage = {}
        for key in sorted(set(storage) - {"type", "upload_logs", "filesystem", "r2", "s3"}):
            warn(f"unknown storage key 'settings.storage.{key}'")
        if "type" in storage and storage["type"] not in ("filesystem", "r2", "s3"):
            err("settings.storage.type must be 'filesystem', 'r2', or 's3'")

        # sites (optional display metadata)
        sites = data.get("sites", {}) or {}
        if not isinstance(sites, dict):
            err("'sites' must be a mapping of site_id -> {name, description}")
            sites = {}
        for site_id, raw in sites.items():
            if not isinstance(raw, dict):
                err(f"sites.{site_id} must be a mapping")
                continue
            for key in sorted(set(raw) - {"name", "description"}):
                warn(f"unknown sites.{site_id} key '{key}'")
            for key in ("name", "description"):
                if key in raw and raw[key] is not None and not isinstance(raw[key], str):
                    err(f"sites.{site_id}.{key} must be a string")

        # domains
        domains = data.get("domains")
        if not isinstance(domains, list) or not domains:
            err("'domains' must be a non-empty list")
            domains = []

        allowed_domain_keys = set(DomainConfig.model_fields)
        seen_ids: Dict[str, int] = {}
        duplicates: List[str] = []
        domain_site_ids = set()

        for i, raw in enumerate(domains):
            context = f"domains[{i}]"
            if not isinstance(raw, dict):
                err(f"{context} must be a mapping")
                continue

            domain_id = raw.get("id")
            if not isinstance(domain_id, str) or not domain_id.strip():
                err(f"{context}.id must be a non-empty string")
            else:
                context = f"{context} (id={domain_id})"
                if domain_id in seen_ids:
                    duplicates.append(domain_id)
                seen_ids[domain_id] = i
                if "." in domain_id:
                    domain_site_ids.add(domain_id.split(".", 1)[0])
                else:
                    domain_site_ids.add("default")
                    warn(f"{context}.id has no '.'; it will be grouped under 'default'")

            for key in sorted(set(raw) - allowed_domain_keys):
                warn(f"unknown {context} key '{key}'")

            url = raw.get("url")
            if not isinstance(url, str) or not url.strip():
                err(f"{context}.url must be a non-empty string")
            else:
                parsed = urlparse(url)
                if parsed.scheme not in ("http", "https") or not parsed.netloc:
                    err(f"{context}.url must be an absolute http(s) URL")

            if "interval_seconds" in raw:
                value = raw["interval_seconds"]
                if not isinstance(value, int) or isinstance(value, bool) or value < 10:
                    err(f"{context}.interval_seconds must be an integer >= 10")
            if "timeout" in raw:
                value = raw["timeout"]
                if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 300:
                    err(f"{context}.timeout must be an integer between 1 and 300")
            if "role" in raw and raw["role"] not in (None, "core", "supplementary"):
                warn(f"{context}.role '{raw['role']}' is not 'core' or 'supplementary'")

            expect = raw.get("expect", {}) or {}
            if not isinstance(expect, dict):
                err(f"{context}.expect must be a mapping")
                expect = {}
            for key in sorted(set(expect) - set(ExpectConfig.model_fields)):
                warn(f"unknown {context}.expect key '{key}'")
            if "http_status" in expect:
                value = expect["http_status"]
                if not isinstance(value, int) or isinstance(value, bool) or not 100 <= value <= 599:
                    err(f"{context}.expect.http_status must be an integer between 100 and 599")
            if "body_contains" in expect and expect["body_contains"] is not None and not isinstance(expect["body_contains"], str):
                err(f"{context}.expect.body_contains must be a string or null")

            ebp = raw.get("expected_bot_protection")
            if ebp is not None:
                if not isinstance(ebp, dict):
                    err(f"{context}.expected_bot_protection must be a mapping")
                else:
                    for key in sorted(set(ebp) - set(ExpectedBotProtection.model_fields)):
                        warn(f"unknown {context}.expected_bot_protection key '{key}'")
                    if "status_code" in ebp and ebp["status_code"] is not None:
                        value = ebp["status_code"]
                        if not isinstance(value, int) or isinstance(value, bool) or not 100 <= value <= 599:
                            err(f"{context}.expected_bot_protection.status_code must be an integer between 100 and 599")
                    if "indicator" in ebp and ebp["indicator"] is not None and not isinstance(ebp["indicator"], str):
                        err(f"{context}.expected_bot_protection.indicator must be a string or null")

        if duplicates:
            err(
                "Duplicate domain ID(s) found in configuration: "
                + ", ".join(duplicates)
                + ". Each domain must have a unique 'id' field."
            )

        for site_id in sorted(set(sites) - domain_site_ids):
            warn(f"sites.{site_id} has no matching domains")

        self.validation_warnings = warnings
        for warning in warnings:
            logger.warning(f"Config warning: {warning}")
        if errors:
            raise ValueError("Configuration validation failed:\n  - " + "\n  - ".join(errors))

    def _parse_domain(self, raw: dict) -> DomainConfig:
        """Parse raw dict into DomainConfig with validation."""
        # Parse expect config if present
        expect_data = raw.get("expect", {})
        expect = ExpectConfig(
            http_status=expect_data.get("http_status", 200),
            body_contains=expect_data.get("body_contains")
        )

        # Parse expected_bot_protection if present
        expected_protection = None
        ebp_data = raw.get("expected_bot_protection")
        if ebp_data:
            expected_protection = ExpectedBotProtection(
                status_code=ebp_data.get("status_code"),
                indicator=ebp_data.get("indicator")
            )

        return DomainConfig(
            id=raw["id"],
            url=raw["url"],
            role=raw.get("role"),
            expect=expect,
            bot_protection_string=raw.get("bot_protection_string"),
            timeout=raw.get("timeout", 20),
            interval_seconds=raw.get("interval_seconds", 60),
            expected_bot_protection=expected_protection
        )

    def get_sites(self) -> Dict[str, List[DomainConfig]]:
        """Group domains by site_id."""
        sites: Dict[str, List[DomainConfig]] = {}
        for domain in self.domains:
            if domain.site_id not in sites:
                sites[domain.site_id] = []
            sites[domain.site_id].append(domain)
        return sites

    def get_domain_by_id(self, domain_id: str) -> DomainConfig:
        """Get a domain by its ID."""
        for domain in self.domains:
            if domain.id == domain_id:
                return domain
        raise ValueError(f"Domain not found: {domain_id}")

    def _parse_storage_config(self, storage_settings: Dict) -> StorageConfig:
        """Parse storage configuration from YAML settings."""
        storage_type = storage_settings.get('type', 'filesystem')

        # Allow FLATMONITOR_STORAGE_TYPE env var to override YAML config
        env_override = os.environ.get('FLATMONITOR_STORAGE_TYPE')
        if env_override:
            storage_type = env_override
            logger.info(f"Storage type overridden by FLATMONITOR_STORAGE_TYPE env var: {storage_type}")

        upload_logs = storage_settings.get('upload_logs', True)

        # Parse filesystem config
        fs_config = storage_settings.get('filesystem', {})
        filesystem = {
            'output_dir': fs_config.get('output_dir', 'public'),
            'enabled': fs_config.get('enabled', True)  # Always write locally by default
        }

        # Parse R2 config (with env var support via ${VAR} syntax)
        # Only expand env vars when R2 is the active storage type to avoid noisy warnings
        r2_config = None
        if 'r2' in storage_settings:
            r2_raw = storage_settings['r2']
            if storage_type == 'r2':
                r2_config = {
                    'account_id': expand_env_vars(r2_raw.get('account_id', '${FLATMONITOR_R2_ACCOUNT_ID}'), 'settings.storage.r2.account_id'),
                    'access_key_id': expand_env_vars(r2_raw.get('access_key_id', '${FLATMONITOR_R2_ACCESS_KEY_ID}'), 'settings.storage.r2.access_key_id'),
                    'secret_access_key': expand_env_vars(r2_raw.get('secret_access_key', '${FLATMONITOR_R2_SECRET_ACCESS_KEY}'), 'settings.storage.r2.secret_access_key'),
                    'bucket_name': expand_env_vars(r2_raw.get('bucket_name', '${FLATMONITOR_R2_BUCKET_NAME}'), 'settings.storage.r2.bucket_name'),
                    'public_domain': expand_env_vars(r2_raw.get('public_domain'), 'settings.storage.r2.public_domain') if r2_raw.get('public_domain') else None,
                    'endpoint_url': r2_raw.get('endpoint_url'),
                    'region': r2_raw.get('region', 'auto'),
                    'cache_max_age': r2_raw.get('cache_max_age', 60)
                }
            else:
                # Pass through raw values without env expansion when inactive
                r2_config = {k: v for k, v in r2_raw.items()}

        # Parse S3 config (with env var support via ${VAR} syntax)
        s3_config = None
        if 's3' in storage_settings:
            s3_raw = storage_settings['s3']
            if storage_type == 's3':
                s3_config = {
                    'access_key_id': expand_env_vars(s3_raw.get('access_key_id', '${FLATMONITOR_AWS_ACCESS_KEY_ID}'), 'settings.storage.s3.access_key_id'),
                    'secret_access_key': expand_env_vars(s3_raw.get('secret_access_key', '${FLATMONITOR_AWS_SECRET_ACCESS_KEY}'), 'settings.storage.s3.secret_access_key'),
                    'bucket_name': expand_env_vars(s3_raw.get('bucket_name', '${FLATMONITOR_S3_BUCKET_NAME}'), 'settings.storage.s3.bucket_name'),
                    'region': s3_raw.get('region', 'us-east-1'),
                    'endpoint_url': s3_raw.get('endpoint_url'),
                    'public_domain': expand_env_vars(s3_raw.get('public_domain')) if s3_raw.get('public_domain') else None,
                    'cache_max_age': s3_raw.get('cache_max_age', 60)
                }
            else:
                # Pass through raw values without env expansion when inactive
                s3_config = {k: v for k, v in s3_raw.items()}

        return StorageConfig(
            type=storage_type,
            upload_logs=upload_logs,
            filesystem=filesystem,
            r2=r2_config,
            s3=s3_config
        )
