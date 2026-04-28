"""
Unit tests for cert_storage.py
Tests SSL certificate metadata storage with TTL caching.
"""

import pytest
import tempfile
import shutil
import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import Mock

from app.cert_storage import CertStorage


class TestCertStorageInitialization:
    """Tests for CertStorage initialization."""

    def test_directories_created(self):
        """Test that certs directory is created on initialization."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = CertStorage(data_dir=tmpdir)
            assert storage.certs_dir.exists()
            assert storage.certs_dir.name == "certs"

    def test_default_ttl(self):
        """Test default TTL is 24 hours."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = CertStorage(data_dir=tmpdir)
            assert storage.ttl_seconds == 86400

    def test_custom_ttl(self):
        """Test custom TTL can be set."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = CertStorage(data_dir=tmpdir, ttl_seconds=3600)
            assert storage.ttl_seconds == 3600


class TestGetCertExpiry:
    """Tests for get_cert_expiry method."""

    def test_returns_none_when_no_cert_exists(self):
        """Test get_cert_expiry returns None when no cached cert."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = CertStorage(data_dir=tmpdir)
            fetch_callback = Mock(return_value="2025-12-31T23:59:59Z")

            result = storage.get_cert_expiry("testsite", "example.com", "https://example.com", fetch_callback)

            # Should call fetch and return the value
            assert result == "2025-12-31T23:59:59Z"
            fetch_callback.assert_called_once()

    def test_returns_cached_cert_within_ttl(self):
        """Test cached cert is returned when within TTL."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = CertStorage(data_dir=tmpdir, ttl_seconds=3600)

            # Pre-store a cert
            storage._store_cert("testsite", "example.com", "2025-12-31T23:59:59Z")

            fetch_callback = Mock(return_value="2026-12-31T23:59:59Z")

            result = storage.get_cert_expiry("testsite", "example.com", "https://example.com", fetch_callback)

            # Should return cached value, not call fetch
            assert result == "2025-12-31T23:59:59Z"
            fetch_callback.assert_not_called()

    def test_fetches_when_ttl_expired(self):
        """Test fetch is called when TTL expired."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = CertStorage(data_dir=tmpdir, ttl_seconds=1)  # 1 second TTL

            # Pre-store a cert
            storage._store_cert("testsite", "example.com", "2025-12-31T23:59:59Z")

            # Wait for TTL to expire
            time.sleep(1.1)

            fetch_callback = Mock(return_value="2026-12-31T23:59:59Z")

            result = storage.get_cert_expiry("testsite", "example.com", "https://example.com", fetch_callback)

            # Should fetch new value
            assert result == "2026-12-31T23:59:59Z"
            fetch_callback.assert_called_once()

    def test_fetches_when_callback_returns_none(self):
        """Test None is stored and returned when fetch returns None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = CertStorage(data_dir=tmpdir)
            fetch_callback = Mock(return_value=None)

            result = storage.get_cert_expiry("testsite", "example.com", "https://example.com", fetch_callback)

            assert result is None
            fetch_callback.assert_called_once()

    def test_corrupted_json_falls_back_to_fetch(self):
        """Test corrupted cache file triggers fetch."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = CertStorage(data_dir=tmpdir)

            # Create corrupted JSON file
            cert_path = storage._get_cert_path("testsite", "example.com")
            cert_path.parent.mkdir(parents=True, exist_ok=True)
            cert_path.write_text("not valid json {{{")

            fetch_callback = Mock(return_value="2025-12-31T23:59:59Z")

            result = storage.get_cert_expiry("testsite", "example.com", "https://example.com", fetch_callback)

            # Should fetch despite corrupted file
            assert result == "2025-12-31T23:59:59Z"
            fetch_callback.assert_called_once()


class TestGetCertInfo:
    """Tests for get_cert_info method."""

    def test_returns_none_when_no_cert_exists(self):
        """Test get_cert_info returns None when no cached cert."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = CertStorage(data_dir=tmpdir)

            result = storage.get_cert_info("testsite", "example.com")

            assert result is None

    def test_returns_full_cert_info(self):
        """Test get_cert_info returns complete info dict."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = CertStorage(data_dir=tmpdir)

            # Store a cert
            expiry = "2025-12-31T23:59:59Z"
            storage._store_cert("testsite", "example.com", expiry)

            result = storage.get_cert_info("testsite", "example.com")

            assert result is not None
            assert result['cert_expiry'] == expiry
            assert 'last_check' in result
            assert 'age_seconds' in result
            assert 'is_valid' in result
            assert 'days_remaining' in result
            assert 'is_fresh' in result

    def test_valid_cert_detection(self):
        """Test is_valid is True for future expiry."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = CertStorage(data_dir=tmpdir)

            # Expiry 30 days in future
            future_expiry = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
            storage._store_cert("testsite", "example.com", future_expiry)

            result = storage.get_cert_info("testsite", "example.com")

            assert result['is_valid'] is True
            assert result['days_remaining'] > 0

    def test_expired_cert_detection(self):
        """Test is_valid is False for past expiry."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = CertStorage(data_dir=tmpdir)

            # Expiry 30 days in past
            past_expiry = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
            storage._store_cert("testsite", "example.com", past_expiry)

            result = storage.get_cert_info("testsite", "example.com")

            assert result['is_valid'] is False
            assert result['days_remaining'] < 0

    def test_is_fresh_within_ttl(self):
        """Test is_fresh is True when within TTL."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = CertStorage(data_dir=tmpdir, ttl_seconds=3600)
            storage._store_cert("testsite", "example.com", "2025-12-31T23:59:59Z")

            result = storage.get_cert_info("testsite", "example.com")

            assert result['is_fresh'] is True

    def test_is_not_fresh_when_ttl_expired(self):
        """Test is_fresh is False when TTL expired."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = CertStorage(data_dir=tmpdir, ttl_seconds=1)
            storage._store_cert("testsite", "example.com", "2025-12-31T23:59:59Z")

            # Wait for TTL to expire
            time.sleep(1.1)

            result = storage.get_cert_info("testsite", "example.com")

            assert result['is_fresh'] is False

    def test_handles_invalid_expiry_format(self):
        """Test graceful handling of invalid expiry format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = CertStorage(data_dir=tmpdir)
            storage._store_cert("testsite", "example.com", "invalid-date-format")

            result = storage.get_cert_info("testsite", "example.com")

            # Should not crash, but is_valid stays False
            assert result is not None
            assert result['is_valid'] is False
            assert result['days_remaining'] is None

    def test_corrupted_json_returns_none(self):
        """Test get_cert_info returns None for corrupted JSON."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = CertStorage(data_dir=tmpdir)

            cert_path = storage._get_cert_path("testsite", "example.com")
            cert_path.parent.mkdir(parents=True, exist_ok=True)
            cert_path.write_text("not valid json {{{")

            result = storage.get_cert_info("testsite", "example.com")

            assert result is None


class TestStoreCert:
    """Tests for _store_cert method."""

    def test_creates_json_file(self):
        """Test _store_cert creates JSON file with correct structure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = CertStorage(data_dir=tmpdir)

            storage._store_cert("testsite", "example.com", "2025-12-31T23:59:59Z")

            cert_path = storage._get_cert_path("testsite", "example.com")
            assert cert_path.exists()

            with open(cert_path, 'r') as f:
                data = json.load(f)

            assert data['site_id'] == "testsite"
            assert data['domain_name'] == "example.com"
            assert data['cert_expiry'] == "2025-12-31T23:59:59Z"
            assert 'last_check' in data

    def test_creates_parent_directories(self):
        """Test _store_cert creates parent directories if needed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = CertStorage(data_dir=tmpdir)

            # Use nested site_id
            storage._store_cert("nested/site/path", "example.com", "2025-12-31T23:59:59Z")

            cert_path = storage._get_cert_path("nested/site/path", "example.com")
            assert cert_path.exists()

    def test_overwrites_existing_cert(self):
        """Test _store_cert overwrites existing file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = CertStorage(data_dir=tmpdir)

            # Store initial cert
            storage._store_cert("testsite", "example.com", "2025-12-31T23:59:59Z")

            # Store updated cert
            storage._store_cert("testsite", "example.com", "2026-12-31T23:59:59Z")

            cert_path = storage._get_cert_path("testsite", "example.com")
            with open(cert_path, 'r') as f:
                data = json.load(f)

            assert data['cert_expiry'] == "2026-12-31T23:59:59Z"

    def test_stores_none_expiry(self):
        """Test _store_cert handles None expiry gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = CertStorage(data_dir=tmpdir)

            storage._store_cert("testsite", "example.com", None)

            cert_path = storage._get_cert_path("testsite", "example.com")
            with open(cert_path, 'r') as f:
                data = json.load(f)

            assert data['cert_expiry'] is None


class TestGetCertPath:
    """Tests for _get_cert_path method."""

    def test_path_construction(self):
        """Test cert path is constructed correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = CertStorage(data_dir=tmpdir)

            path = storage._get_cert_path("testsite", "example.com")

            assert path.parent.name == "testsite"
            assert path.name == "example.com.json"
            assert path.parent.parent.name == "certs"


class TestCleanup:
    """Tests for cleanup method."""

    def test_removes_old_certs(self):
        """Test cleanup removes cert files older than max_age."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = CertStorage(data_dir=tmpdir)

            # Create an old cert file by mocking file modification time
            storage._store_cert("testsite", "old.com", "2025-12-31T23:59:59Z")
            old_path = storage._get_cert_path("testsite", "old.com")

            # Manually set mtime to 40 days ago
            old_time = time.time() - (40 * 86400)
            import os
            os.utime(old_path, (old_time, old_time))

            # Create a recent cert
            storage._store_cert("testsite", "recent.com", "2025-12-31T23:59:59Z")

            # Cleanup with 30 day max age
            storage.cleanup(max_age_days=30)

            assert not old_path.exists()
            assert storage._get_cert_path("testsite", "recent.com").exists()

    def test_removes_empty_site_directories(self):
        """Test cleanup removes empty site directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = CertStorage(data_dir=tmpdir)

            # Create a cert that will be removed
            storage._store_cert("empty_site", "old.com", "2025-12-31T23:59:59Z")
            old_path = storage._get_cert_path("empty_site", "old.com")

            # Set old modification time
            old_time = time.time() - (40 * 86400)
            import os
            os.utime(old_path, (old_time, old_time))

            site_dir = old_path.parent

            # Cleanup
            storage.cleanup(max_age_days=30)

            # Directory should be removed
            assert not site_dir.exists()

    def test_handles_missing_directories(self):
        """Test cleanup handles missing directories gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = CertStorage(data_dir=tmpdir)

            # Remove certs dir entirely
            shutil.rmtree(storage.certs_dir)

            # Should not raise
            storage.cleanup(max_age_days=30)

    def test_keeps_recent_certs(self):
        """Test cleanup preserves recent cert files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = CertStorage(data_dir=tmpdir)

            # Create multiple recent certs
            for i in range(3):
                storage._store_cert(f"site{i}", f"domain{i}.com", "2025-12-31T23:59:59Z")

            # Cleanup with 30 day max age
            storage.cleanup(max_age_days=30)

            # All should still exist
            for i in range(3):
                assert storage._get_cert_path(f"site{i}", f"domain{i}.com").exists()
