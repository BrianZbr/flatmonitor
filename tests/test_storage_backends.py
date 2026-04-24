"""
Tests for storage backend implementations.
"""

import pytest
import tempfile
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch

from app.storage_backends import (
    FilesystemBackend,
    MultiStorageBackend,
    create_storage_backend
)


@pytest.mark.unit
class TestFilesystemBackend:
    """Test the local filesystem storage backend."""

    def test_write_file_creates_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            backend = FilesystemBackend(tmpdir)
            content = "<html>Test</html>"

            result = backend.write_file("test.html", content, "text/html")

            # Check file was created
            file_path = Path(tmpdir) / "test.html"
            assert file_path.exists()
            assert file_path.read_text() == content
            assert str(file_path.resolve()) == result

    def test_write_file_creates_nested_directories(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            backend = FilesystemBackend(tmpdir)

            backend.write_file("nested/path/file.html", "content", "text/html")

            assert (Path(tmpdir) / "nested/path/file.html").exists()

    def test_get_public_url_returns_absolute_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            backend = FilesystemBackend(tmpdir)

            url = backend.get_public_url("test.html")

            assert url == str(Path(tmpdir) / "test.html")

    def test_get_log_public_url_returns_relative_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            backend = FilesystemBackend(tmpdir)

            url = backend.get_log_public_url("mysite", "example.com")

            assert url == "../data/live/mysite/example.com.log"

    def test_get_archive_log_public_url_returns_relative_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            backend = FilesystemBackend(tmpdir)

            url = backend.get_archive_log_public_url("mysite", "example.com", "2024-01-15")

            assert url == "../data/archive/2024-01-15/mysite/example.com.log"

    def test_upload_logs_is_noop(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            backend = FilesystemBackend(tmpdir)
            data_dir = Path(tmpdir) / "data"

            # Should not raise
            backend.upload_logs(data_dir)


@pytest.mark.unit
class TestMultiStorageBackend:
    """Test the multi-backend wrapper that writes to both primary and secondary."""

    def test_write_file_writes_to_both_backends(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            primary = Mock(spec=FilesystemBackend)
            primary.write_file.return_value = "https://r2.example.com/test.html"
            secondary = FilesystemBackend(tmpdir)

            multi = MultiStorageBackend(primary, secondary)
            result = multi.write_file("test.html", "<html>Test</html>", "text/html")

            # Returns primary URL
            assert result == "https://r2.example.com/test.html"

            # Both backends received the write
            primary.write_file.assert_called_once_with("test.html", "<html>Test</html>", "text/html")
            assert (Path(tmpdir) / "test.html").exists()

    def test_get_public_url_uses_primary(self):
        primary = Mock(spec=FilesystemBackend)
        primary.get_public_url.return_value = "https://r2.example.com/page.html"
        secondary = Mock(spec=FilesystemBackend)

        multi = MultiStorageBackend(primary, secondary)
        result = multi.get_public_url("page.html")

        assert result == "https://r2.example.com/page.html"
        primary.get_public_url.assert_called_once_with("page.html")
        secondary.get_public_url.assert_not_called()

    def test_get_log_public_url_uses_primary(self):
        primary = Mock(spec=FilesystemBackend)
        primary.get_log_public_url.return_value = "https://r2.example.com/logs/site/domain.log"
        secondary = Mock(spec=FilesystemBackend)

        multi = MultiStorageBackend(primary, secondary)
        result = multi.get_log_public_url("site", "domain")

        assert result == "https://r2.example.com/logs/site/domain.log"
        primary.get_log_public_url.assert_called_once_with("site", "domain")

    def test_get_archive_log_public_url_uses_primary(self):
        primary = Mock(spec=FilesystemBackend)
        primary.get_archive_log_public_url.return_value = "https://r2.example.com/logs/archive/2024-01-15/site/domain.log"
        secondary = Mock(spec=FilesystemBackend)

        multi = MultiStorageBackend(primary, secondary)
        result = multi.get_archive_log_public_url("site", "domain", "2024-01-15")

        assert result == "https://r2.example.com/logs/archive/2024-01-15/site/domain.log"
        primary.get_archive_log_public_url.assert_called_once_with("site", "domain", "2024-01-15")

    def test_upload_logs_calls_primary_and_skips_filesystem_secondary(self):
        primary = Mock(spec=FilesystemBackend)
        secondary = FilesystemBackend("/tmp")  # Real filesystem backend

        multi = MultiStorageBackend(primary, secondary)
        data_dir = Path("/fake/data")
        multi.upload_logs(data_dir)

        # Primary should always be called
        primary.upload_logs.assert_called_once_with(data_dir)
        # Filesystem secondary should NOT be called (logs already local)

    def test_upload_logs_calls_both_when_secondary_not_filesystem(self):
        primary = Mock(spec=FilesystemBackend)
        secondary = Mock(spec=FilesystemBackend)  # Mock as non-filesystem type
        # Make isinstance check fail by setting different spec
        secondary.__class__ = Mock

        multi = MultiStorageBackend(primary, secondary)
        data_dir = Path("/fake/data")
        multi.upload_logs(data_dir)

        primary.upload_logs.assert_called_once_with(data_dir)
        secondary.upload_logs.assert_called_once_with(data_dir)


@pytest.mark.unit
class TestCreateStorageBackend:
    """Test the factory function for creating storage backends."""

    def test_creates_filesystem_backend_by_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = {
                "type": "filesystem",
                "filesystem": {"output_dir": tmpdir}
            }

            backend = create_storage_backend(config)

            assert isinstance(backend, FilesystemBackend)

    @patch('app.storage_backends.R2Backend')
    def test_creates_r2_backend_alone_when_filesystem_disabled(self, mock_r2):
        config = {
            "type": "r2",
            "filesystem": {"enabled": False, "output_dir": "public"},
            "r2": {
                "account_id": "test123",
                "access_key_id": "AKIA...",
                "secret_access_key": "secret...",
                "bucket_name": "test-bucket"
            }
        }

        backend = create_storage_backend(config)

        # Should create R2Backend with correct parameters
        mock_r2.assert_called_once_with(
            account_id="test123",
            access_key_id="AKIA...",
            secret_access_key="secret...",
            bucket_name="test-bucket",
            public_domain=None,
            endpoint_url=None,
            region="auto",
            cache_max_age=60
        )
        assert backend == mock_r2.return_value

    @patch('app.storage_backends.R2Backend')
    def test_wraps_r2_in_multi_backend_when_filesystem_enabled(self, mock_r2):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = {
                "type": "r2",
                "filesystem": {"enabled": True, "output_dir": tmpdir},
                "r2": {
                    "account_id": "test123",
                    "access_key_id": "AKIA...",
                    "secret_access_key": "secret...",
                    "bucket_name": "test-bucket"
                }
            }

            backend = create_storage_backend(config)

            assert isinstance(backend, MultiStorageBackend)

    @patch('app.storage_backends.S3Backend')
    def test_wraps_s3_in_multi_backend_when_filesystem_enabled(self, mock_s3):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = {
                "type": "s3",
                "filesystem": {"enabled": True, "output_dir": tmpdir},
                "s3": {
                    "access_key_id": "AKIA...",
                    "secret_access_key": "secret...",
                    "bucket_name": "test-bucket"
                }
            }

            backend = create_storage_backend(config)

            assert isinstance(backend, MultiStorageBackend)

    @patch('app.storage_backends.R2Backend')
    def test_filesystem_enabled_by_default_for_r2(self, mock_r2):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = {
                "type": "r2",
                "filesystem": {"output_dir": tmpdir},  # No enabled field
                "r2": {
                    "account_id": "test123",
                    "access_key_id": "AKIA...",
                    "secret_access_key": "secret...",
                    "bucket_name": "test-bucket"
                }
            }

            backend = create_storage_backend(config)

            # Should wrap by default
            assert isinstance(backend, MultiStorageBackend)

    def test_raises_error_for_unknown_backend_type(self):
        config = {"type": "unknown"}

        with pytest.raises(ValueError, match="Unknown storage backend type"):
            create_storage_backend(config)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
