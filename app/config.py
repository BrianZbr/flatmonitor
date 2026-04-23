"""
FlatMonitor - Configuration Loader

YAML loading and validation for domain configurations.
"""

import os
import yaml
from typing import Dict, List
from pathlib import Path

from app.models import DomainConfig, ExpectConfig, ExpectedBotProtection
from dataclasses import dataclass, field
from typing import Optional, List as ListType
import re


def expand_env_vars(value: str) -> str:
    """Expand environment variables in ${VAR_NAME} syntax.

    Supports:
    - ${FLATMONITOR_VAR_NAME} -> reads env var FLATMONITOR_VAR_NAME
    - Plain strings pass through unchanged
    """
    if not isinstance(value, str):
        return value

    def replacer(match):
        var_name = match.group(1)
        env_value = os.getenv(var_name)
        return env_value if env_value is not None else match.group(0)

    return re.sub(r'\$\{([^}]+)\}', replacer, value)


@dataclass
class DashboardConfig:
    """Dashboard customization settings."""
    title: str = "FlatMonitor"
    header_text: Optional[str] = None
    announcement: Optional[str] = None
    footer_links: ListType[Dict[str, str]] = field(default_factory=list)
    sort_by: str = "yaml_order"  # Options: 'yaml_order', 'severity', 'alphabetical'
    favicon: Optional[str] = None  # Filename in public/assets/
    logo: Optional[str] = None  # Filename in public/assets/ (header logo image)
    header_hint: Optional[str] = "Click any site title for detailed status and logs."
    footer_explanation: Optional[str] = None  # Custom footer explanation text (None = use default)
    instance_label: Optional[str] = None  # Optional label for this instance (e.g., "US-East Primary")


@dataclass
class StorageConfig:
    """Storage backend configuration."""
    type: str = "filesystem"  # Options: 'filesystem', 'r2', 's3'
    upload_logs: bool = True  # Upload log files to object storage (R2/S3)
    filesystem: Dict = field(default_factory=lambda: {"output_dir": "public"})
    r2: Optional[Dict] = None
    s3: Optional[Dict] = None


class ConfigLoader:
    """Loads and validates domain configurations from YAML files."""

    def __init__(self, config_path: str = "config/domains.yaml"):
        self.config_path = Path(config_path)
        self.config_data: Dict = {}
        self.domains: List[DomainConfig] = []
        self.rotation_interval: int = 86400  # Default: 24 hours (daily rotation)
        self.retention_days: int = 7  # Default: 7 days of archive retention
        self.noindex: bool = True  # Default: privacy-by-default, no search engine indexing
        self.dashboard: DashboardConfig = DashboardConfig()  # Default dashboard settings
        self.storage: StorageConfig = StorageConfig()  # Default filesystem storage

    def load(self) -> List[DomainConfig]:
        """Load domain configurations from YAML file."""
        if not self.config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {self.config_path}")

        with open(self.config_path, "r") as f:
            self.config_data = yaml.safe_load(f)

        if not self.config_data or "domains" not in self.config_data:
            raise ValueError("Configuration must contain a 'domains' key")

        # Parse settings
        settings = self.config_data.get('settings', {})
        self.rotation_interval = settings.get('rotation_interval_seconds', 86400)
        self.retention_days = settings.get('retention_days', 7)
        self.noindex = settings.get('noindex', True)

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
            instance_label=dashboard_settings.get('instance_label')
        )

        # Parse storage configuration
        storage_settings = settings.get('storage', {})
        self.storage = self._parse_storage_config(storage_settings)

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
            expect=expect,
            bot_protection_string=raw.get("bot_protection_string"),
            timeout=raw.get("timeout", 20),
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
        upload_logs = storage_settings.get('upload_logs', True)

        # Parse filesystem config
        fs_config = storage_settings.get('filesystem', {})
        filesystem = {
            'output_dir': fs_config.get('output_dir', 'public'),
            'enabled': fs_config.get('enabled', True)  # Always write locally by default
        }

        # Parse R2 config (with env var support via ${VAR} syntax)
        r2_config = None
        if 'r2' in storage_settings:
            r2_raw = storage_settings['r2']
            r2_config = {
                'account_id': expand_env_vars(r2_raw.get('account_id', '${FLATMONITOR_R2_ACCOUNT_ID}')),
                'access_key_id': expand_env_vars(r2_raw.get('access_key_id', '${FLATMONITOR_R2_ACCESS_KEY_ID}')),
                'secret_access_key': expand_env_vars(r2_raw.get('secret_access_key', '${FLATMONITOR_R2_SECRET_ACCESS_KEY}')),
                'bucket_name': expand_env_vars(r2_raw.get('bucket_name', '${FLATMONITOR_R2_BUCKET_NAME}')),
                'public_domain': r2_raw.get('public_domain'),
                'endpoint_url': r2_raw.get('endpoint_url'),
                'region': r2_raw.get('region', 'auto'),
                'cache_max_age': r2_raw.get('cache_max_age', 60)
            }

        # Parse S3 config (with env var support via ${VAR} syntax)
        s3_config = None
        if 's3' in storage_settings:
            s3_raw = storage_settings['s3']
            s3_config = {
                'access_key_id': expand_env_vars(s3_raw.get('access_key_id', '${FLATMONITOR_AWS_ACCESS_KEY_ID}')),
                'secret_access_key': expand_env_vars(s3_raw.get('secret_access_key', '${FLATMONITOR_AWS_SECRET_ACCESS_KEY}')),
                'bucket_name': expand_env_vars(s3_raw.get('bucket_name', '${FLATMONITOR_S3_BUCKET_NAME}')),
                'region': s3_raw.get('region', 'us-east-1'),
                'endpoint_url': s3_raw.get('endpoint_url'),
                'public_domain': s3_raw.get('public_domain'),
                'cache_max_age': s3_raw.get('cache_max_age', 60)
            }

        return StorageConfig(
            type=storage_type,
            upload_logs=upload_logs,
            filesystem=filesystem,
            r2=r2_config,
            s3=s3_config
        )
