"""
Unit tests for config.py
Tests YAML loading, domain parsing, validation error handling
"""

import pytest
import tempfile
import os
from pathlib import Path

from app.config import ConfigLoader, expand_env_vars
from app.models import DomainConfig


class TestConfigLoader:
    """Tests for ConfigLoader class."""

    def test_load_valid_config(self):
        yaml_content = """
domains:
  - id: acme.www
    url: https://www.acme.com
    expect:
      http_status: 200
      body_contains: "Welcome"
    bot_protection_string: "Cloudflare"
    timeout: 20
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(yaml_content)
            temp_path = f.name

        try:
            loader = ConfigLoader(temp_path)
            domains = loader.load()

            assert len(domains) == 1
            assert domains[0].id == "acme.www"
            assert domains[0].url == "https://www.acme.com"
            assert domains[0].interval_seconds == 60  # Fixed at 60s
            assert domains[0].expect.http_status == 200
            assert domains[0].expect.body_contains == "Welcome"
            assert domains[0].bot_protection_string == "Cloudflare"
            assert domains[0].timeout == 20
        finally:
            os.unlink(temp_path)

    def test_load_multiple_domains(self):
        yaml_content = """
domains:
  - id: site1.www
    url: https://site1.com
  - id: site2.api
    url: https://api.site2.com
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(yaml_content)
            temp_path = f.name

        try:
            loader = ConfigLoader(temp_path)
            domains = loader.load()

            assert len(domains) == 2
            assert domains[0].id == "site1.www"
            assert domains[1].id == "site2.api"
            assert domains[1].interval_seconds == 60  # Fixed at 60s
        finally:
            os.unlink(temp_path)

    def test_load_default_values(self):
        yaml_content = """
domains:
  - id: test.site
    url: https://example.com
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(yaml_content)
            temp_path = f.name

        try:
            loader = ConfigLoader(temp_path)
            domains = loader.load()

            assert domains[0].interval_seconds == 60  # Fixed at 60s
            assert domains[0].timeout == 20
            assert domains[0].expect.http_status == 200
            assert domains[0].expect.body_contains is None
            assert domains[0].bot_protection_string is None
        finally:
            os.unlink(temp_path)

    def test_file_not_found(self):
        loader = ConfigLoader("/nonexistent/path/config.yaml")
        with pytest.raises(FileNotFoundError):
            loader.load()

    def test_missing_domains_key(self):
        yaml_content = """
some_other_key:
  - item: value
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(yaml_content)
            temp_path = f.name

        try:
            loader = ConfigLoader(temp_path)
            with pytest.raises(ValueError, match="must contain a 'domains' key"):
                loader.load()
        finally:
            os.unlink(temp_path)

    def test_get_sites_grouping(self):
        yaml_content = """
domains:
  - id: acme.www
    url: https://www.acme.com
  - id: acme.api
    url: https://api.acme.com
  - id: other.site
    url: https://other.com
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(yaml_content)
            temp_path = f.name

        try:
            loader = ConfigLoader(temp_path)
            loader.load()
            sites = loader.get_sites()

            assert "acme" in sites
            assert "other" in sites
            assert len(sites["acme"]) == 2
            assert len(sites["other"]) == 1
        finally:
            os.unlink(temp_path)

    def test_get_domain_by_id_success(self):
        yaml_content = """
domains:
  - id: test.site
    url: https://example.com
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(yaml_content)
            temp_path = f.name

        try:
            loader = ConfigLoader(temp_path)
            loader.load()
            domain = loader.get_domain_by_id("test.site")
            assert domain.id == "test.site"
        finally:
            os.unlink(temp_path)

    def test_get_domain_by_id_failure(self):
        yaml_content = """
domains:
  - id: test.site
    url: https://example.com
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(yaml_content)
            temp_path = f.name

        try:
            loader = ConfigLoader(temp_path)
            loader.load()
            with pytest.raises(ValueError, match="Domain not found"):
                loader.get_domain_by_id("nonexistent")
        finally:
            os.unlink(temp_path)

    def test_rotation_interval_default(self):
        """Test that rotation_interval defaults to 86400 (24 hours / daily)."""
        yaml_content = """
domains:
  - id: test.site
    url: https://example.com
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(yaml_content)
            temp_path = f.name

        try:
            loader = ConfigLoader(temp_path)
            loader.load()
            assert loader.rotation_interval == 86400
        finally:
            os.unlink(temp_path)

    def test_rotation_interval_from_config(self):
        """Test that rotation_interval can be set via config."""
        yaml_content = """
settings:
  rotation_interval_seconds: 28800

domains:
  - id: test.site
    url: https://example.com
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(yaml_content)
            temp_path = f.name

        try:
            loader = ConfigLoader(temp_path)
            loader.load()
            assert loader.rotation_interval == 28800
        finally:
            os.unlink(temp_path)

    def test_noindex_default(self):
        """Test that noindex defaults to False."""
        yaml_content = """
domains:
  - id: test.site
    url: https://example.com
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(yaml_content)
            temp_path = f.name

        try:
            loader = ConfigLoader(temp_path)
            loader.load()
            assert loader.noindex is False
        finally:
            os.unlink(temp_path)

    def test_noindex_from_config(self):
        """Test that noindex can be set via config."""
        yaml_content = """
settings:
  noindex: true

domains:
  - id: test.site
    url: https://example.com
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(yaml_content)
            temp_path = f.name

        try:
            loader = ConfigLoader(temp_path)
            loader.load()
            assert loader.noindex is True
        finally:
            os.unlink(temp_path)

    def test_dashboard_config_defaults(self):
        """Test that dashboard config defaults are set correctly."""
        yaml_content = """
domains:
  - id: test.site
    url: https://example.com
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(yaml_content)
            temp_path = f.name

        try:
            loader = ConfigLoader(temp_path)
            loader.load()
            assert loader.dashboard.title == "FlatMonitor"
            assert loader.dashboard.header_text is None
            assert loader.dashboard.announcement is None
            assert loader.dashboard.footer_links == []
            assert loader.dashboard.sort_by == "yaml_order"
            assert loader.dashboard.favicon is None
            assert loader.dashboard.header_hint == "Click any site title for detailed status and logs."
            assert loader.dashboard.footer_explanation is None
            assert loader.dashboard.instance_label is None
        finally:
            os.unlink(temp_path)

    def test_dashboard_config_from_settings(self):
        """Test that dashboard config can be set via settings."""
        yaml_content = """
settings:
  dashboard:
    title: "Service Status"
    header_text: "Service monitoring"
    announcement: "Maintenance scheduled"
    footer_links:
      - text: "Terms of Service"
        url: "https://example.com/terms"
      - text: "Privacy"
        url: "https://example.com/privacy"
    sort_by: "yaml_order"
    favicon: "logo.png"
    header_hint: "Click site names to view details"
    footer_explanation: "<strong>Custom:</strong> Your explanation here."
    instance_label: "US-East Primary"

domains:
  - id: test.site
    url: https://example.com
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(yaml_content)
            temp_path = f.name

        try:
            loader = ConfigLoader(temp_path)
            loader.load()
            assert loader.dashboard.title == "Service Status"
            assert loader.dashboard.header_text == "Service monitoring"
            assert loader.dashboard.announcement == "Maintenance scheduled"
            assert len(loader.dashboard.footer_links) == 2
            assert loader.dashboard.footer_links[0]['text'] == "Terms of Service"
            assert loader.dashboard.footer_links[0]['url'] == "https://example.com/terms"
            assert loader.dashboard.sort_by == "yaml_order"
            assert loader.dashboard.favicon == "logo.png"
            assert loader.dashboard.header_hint == "Click site names to view details"
            assert loader.dashboard.footer_explanation == "<strong>Custom:</strong> Your explanation here."
            assert loader.dashboard.instance_label == "US-East Primary"
        finally:
            os.unlink(temp_path)

    def test_duplicate_domain_ids_raises_error(self):
        """Test that duplicate domain IDs are detected and raise an error."""
        yaml_content = """
domains:
  - id: site.www
    url: https://www.example.com
  - id: site.www
    url: https://www2.example.com
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(yaml_content)
            temp_path = f.name

        try:
            loader = ConfigLoader(temp_path)
            with pytest.raises(ValueError, match="Duplicate domain ID"):
                loader.load()
        finally:
            os.unlink(temp_path)

    def test_multiple_duplicate_domain_ids_raises_error(self):
        """Test that multiple duplicate domain IDs are all reported in error."""
        yaml_content = """
domains:
  - id: site.www
    url: https://www.example.com
  - id: site.api
    url: https://api.example.com
  - id: site.www
    url: https://www2.example.com
  - id: site.api
    url: https://api2.example.com
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(yaml_content)
            temp_path = f.name

        try:
            loader = ConfigLoader(temp_path)
            with pytest.raises(ValueError, match="site.www.*site.api"):
                loader.load()
        finally:
            os.unlink(temp_path)


class TestExpandEnvVars:
    """Tests for expand_env_vars function."""

    def test_no_expansion_for_plain_string(self):
        """Plain strings without ${} pass through unchanged."""
        assert expand_env_vars("hello world") == "hello world"
        assert expand_env_vars("abc123") == "abc123"

    def test_expands_flatmonitor_prefixed_var(self, monkeypatch):
        """Expands ${FLATMONITOR_VAR_NAME} from environment."""
        monkeypatch.setenv("FLATMONITOR_R2_ACCOUNT_ID", "test-account-123")
        result = expand_env_vars("${FLATMONITOR_R2_ACCOUNT_ID}")
        assert result == "test-account-123"

    def test_returns_placeholder_if_not_found(self):
        """Returns original ${VAR} if env var not found."""
        result = expand_env_vars("${FLATMONITOR_NONEXISTENT}")
        assert result == "${FLATMONITOR_NONEXISTENT}"

    def test_expands_multiple_vars(self, monkeypatch):
        """Expands multiple ${VAR} placeholders in one string."""
        monkeypatch.setenv("FLATMONITOR_R2_ACCOUNT_ID", "acc-123")
        monkeypatch.setenv("FLATMONITOR_R2_BUCKET_NAME", "bucket-456")
        result = expand_env_vars("account: ${FLATMONITOR_R2_ACCOUNT_ID}, bucket: ${FLATMONITOR_R2_BUCKET_NAME}")
        assert result == "account: acc-123, bucket: bucket-456"

    def test_handles_non_string_input(self):
        """Non-string inputs pass through unchanged."""
        assert expand_env_vars(123) == 123
        assert expand_env_vars(None) is None
        assert expand_env_vars(["a", "b"]) == ["a", "b"]

    def test_storage_config_expansion(self, monkeypatch):
        """Storage config values are expanded when parsed."""
        monkeypatch.setenv("FLATMONITOR_R2_ACCOUNT_ID", "test-acc")
        monkeypatch.setenv("FLATMONITOR_R2_ACCESS_KEY_ID", "test-key")
        monkeypatch.setenv("FLATMONITOR_R2_SECRET_ACCESS_KEY", "test-secret")
        monkeypatch.setenv("FLATMONITOR_R2_BUCKET_NAME", "test-bucket")

        yaml_content = """
settings:
  storage:
    type: r2
    r2:
      account_id: "${FLATMONITOR_R2_ACCOUNT_ID}"
      access_key_id: "${FLATMONITOR_R2_ACCESS_KEY_ID}"
      secret_access_key: "${FLATMONITOR_R2_SECRET_ACCESS_KEY}"
      bucket_name: "${FLATMONITOR_R2_BUCKET_NAME}"

domains:
  - id: test.site
    url: https://example.com
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(yaml_content)
            temp_path = f.name

        try:
            loader = ConfigLoader(temp_path)
            loader.load()
            assert loader.storage.r2['account_id'] == "test-acc"
            assert loader.storage.r2['access_key_id'] == "test-key"
            assert loader.storage.r2['secret_access_key'] == "test-secret"
            assert loader.storage.r2['bucket_name'] == "test-bucket"
        finally:
            os.unlink(temp_path)
