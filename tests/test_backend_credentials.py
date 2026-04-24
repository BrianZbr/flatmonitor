"""
Backend credential handling tests.

These tests verify proper credential validation and error handling
without making actual API calls.
"""

import pytest
import os
from unittest.mock import patch, MagicMock
from app.storage_backends import R2Backend, S3Backend, create_storage_backend


class TestCredentialHandling:
    """Test credential validation and error handling."""

    def test_r2_missing_credentials_raises_error(self):
        """Test that missing R2 credentials raise clear errors."""
        with pytest.raises(ValueError, match="Missing required R2 configuration"):
            create_storage_backend({
                "type": "r2",
                "r2": {
                    # Missing account_id, access_key_id, secret_access_key, bucket_name
                }
            })

    def test_s3_missing_credentials_raises_error(self):
        """Test that missing S3 credentials raise clear errors."""
        with pytest.raises(ValueError, match="Missing required S3 configuration"):
            create_storage_backend({
                "type": "s3", 
                "s3": {
                    # Missing access_key_id, secret_access_key, bucket_name
                }
            })

    def test_r2_backend_creation_with_valid_credentials(self):
        """Test R2 backend creation succeeds with all required credentials."""
        # Mock boto3 to prevent actual API calls
        with patch('app.storage_backends.boto3') as mock_boto3:
            mock_client = MagicMock()
            mock_boto3.client.return_value = mock_client
            
            backend = R2Backend(
                account_id="test123",
                access_key_id="AKIATEST",
                secret_access_key="secret123",
                bucket_name="test-bucket"
            )
            
            assert backend.account_id == "test123"
            assert backend.bucket_name == "test-bucket"
            assert backend.endpoint_url == "https://test123.r2.cloudflarestorage.com"
            
            # Verify boto3 was called with correct parameters
            mock_boto3.client.assert_called_once_with(
                "s3",
                endpoint_url="https://test123.r2.cloudflarestorage.com",
                aws_access_key_id="AKIATEST",
                aws_secret_access_key="secret123",
                region_name="auto"
            )

    def test_s3_backend_creation_with_valid_credentials(self):
        """Test S3 backend creation succeeds with all required credentials."""
        with patch('app.storage_backends.boto3') as mock_boto3:
            mock_client = MagicMock()
            mock_boto3.client.return_value = mock_client
            
            backend = S3Backend(
                access_key_id="AKIATEST",
                secret_access_key="secret123",
                bucket_name="test-bucket",
                endpoint_url="https://s3.amazonaws.com"
            )
            
            assert backend.bucket_name == "test-bucket"
            assert backend.endpoint_url == "https://s3.amazonaws.com"
            
            # Verify boto3 was called with correct parameters
            mock_boto3.client.assert_called_once_with(
                "s3",
                endpoint_url="https://s3.amazonaws.com",
                aws_access_key_id="AKIATEST",
                aws_secret_access_key="secret123",
                region_name="us-east-1"
            )

    @patch.dict(os.environ, {
        'R2_ACCOUNT_ID': 'env123',
        'R2_ACCESS_KEY_ID': 'envAKIA',
        'R2_SECRET_ACCESS_KEY': 'envsecret',
        'R2_BUCKET_NAME': 'env-bucket'
    })
    def test_r2_credentials_from_environment(self):
        """Test R2 credentials can be loaded from environment variables."""
        with patch('app.storage_backends.boto3') as mock_boto3:
            mock_client = MagicMock()
            mock_boto3.client.return_value = mock_client
            
            backend = create_storage_backend({
                "type": "r2",
                "filesystem": {"enabled": False}
            })
            
            assert backend.account_id == "env123"
            assert backend.bucket_name == "env-bucket"

    @patch.dict(os.environ, {
        'AWS_ACCESS_KEY_ID': 'envAKIA',
        'AWS_SECRET_ACCESS_KEY': 'envsecret', 
        'S3_BUCKET_NAME': 'env-bucket'
    })
    def test_s3_credentials_from_environment(self):
        """Test S3 credentials can be loaded from environment variables."""
        with patch('app.storage_backends.boto3') as mock_boto3:
            mock_client = MagicMock()
            mock_boto3.client.return_value = mock_client
            
            backend = create_storage_backend({
                "type": "s3",
                "filesystem": {"enabled": False}
            })
            
            assert backend.bucket_name == "env-bucket"

    def test_invalid_credentials_dont_make_api_calls(self):
        """Test that invalid credentials are caught before API calls."""
        with patch('app.storage_backends.boto3') as mock_boto3:
            # Simulate boto3 raising an auth error
            mock_boto3.client.side_effect = Exception("Invalid credentials")
            
            with pytest.raises(Exception, match="Invalid credentials"):
                R2Backend(
                    account_id="test123",
                    access_key_id="invalid",
                    secret_access_key="invalid",
                    bucket_name="test-bucket"
                )

    def test_backend_write_file_with_mocked_client(self):
        """Test write_file method works with mocked S3 client."""
        with patch('app.storage_backends.boto3') as mock_boto3:
            mock_client = MagicMock()
            mock_boto3.client.return_value = mock_client
            
            # Mock the S3 response
            mock_client.put_object.return_value = {"ETag": "test-etag"}
            
            backend = R2Backend(
                account_id="test123",
                access_key_id="AKIATEST",
                secret_access_key="secret123",
                bucket_name="test-bucket"
            )
            
            # This should work without making real API calls
            url = backend.write_file("test.html", "<html>Test</html>", "text/html")
            
            # Verify the correct S3 API call was made
            mock_client.put_object.assert_called_once()
            call_args = mock_client.put_object.call_args
            assert call_args[1]['Bucket'] == "test-bucket"
            assert call_args[1]['Key'] == "test.html"
            assert call_args[1]['Body'] == b"<html>Test</html>"
            assert call_args[1]['ContentType'] == "text/html"
            
            assert url == "https://test-bucket.test123.r2.dev/test.html"
