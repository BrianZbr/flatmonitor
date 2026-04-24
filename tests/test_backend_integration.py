"""
Backend integration tests.

These tests require actual credentials and make real API calls.
They should only be run when credentials are available.
Run with: pytest -m backend tests/test_backend_integration.py
"""

import pytest
import os
from unittest.mock import patch
from app.storage_backends import R2Backend, S3Backend


pytest.importorskip("boto3")


@pytest.mark.backend
class TestR2Integration:
    """Integration tests for R2 backend (requires real credentials)."""
    
    @pytest.fixture(autouse=True)
    def skip_if_no_credentials(self):
        """Skip tests if R2 credentials are not available."""
        required_vars = ['R2_ACCOUNT_ID', 'R2_ACCESS_KEY_ID', 'R2_SECRET_ACCESS_KEY', 'R2_BUCKET_NAME']
        missing = [var for var in required_vars if not os.getenv(var)]
        if missing:
            pytest.skip(f"Missing R2 credentials: {', '.join(missing)}. Set environment variables to run integration tests.")
    
    def test_r2_real_connection(self):
        """Test actual R2 connection and basic operations."""
        backend = R2Backend(
            account_id=os.getenv('R2_ACCOUNT_ID'),
            access_key_id=os.getenv('R2_ACCESS_KEY_ID'),
            secret_access_key=os.getenv('R2_SECRET_ACCESS_KEY'),
            bucket_name=os.getenv('R2_BUCKET_NAME')
        )
        
        # Test that we can list bucket (basic connectivity)
        try:
            backend.s3_client.head_bucket(Bucket=backend.bucket_name)
        except Exception as e:
            pytest.fail(f"R2 connection failed: {e}")
    
    def test_r2_write_and_read_file(self):
        """Test writing and reading a file to/from R2."""
        backend = R2Backend(
            account_id=os.getenv('R2_ACCOUNT_ID'),
            access_key_id=os.getenv('R2_ACCESS_KEY_ID'),
            secret_access_key=os.getenv('R2_SECRET_ACCESS_KEY'),
            bucket_name=os.getenv('R2_BUCKET_NAME')
        )
        
        # Write a test file
        test_content = "<html><body>Integration Test</body></html>"
        test_path = "integration-test.html"
        
        try:
            # Write file
            url = backend.write_file(test_path, test_content, "text/html")
            assert url.endswith(test_path)
            
            # Verify file exists and content matches
            response = backend.s3_client.get_object(Bucket=backend.bucket_name, Key=test_path)
            actual_content = response['Body'].read().decode('utf-8')
            assert actual_content == test_content
            
            # Clean up
            backend.s3_client.delete_object(Bucket=backend.bucket_name, Key=test_path)
            
        except Exception as e:
            pytest.fail(f"R2 write/read test failed: {e}")


@pytest.mark.backend  
class TestS3Integration:
    """Integration tests for S3 backend (requires real credentials)."""
    
    @pytest.fixture(autouse=True)
    def skip_if_no_credentials(self):
        """Skip tests if S3 credentials are not available."""
        required_vars = ['S3_ACCESS_KEY_ID', 'S3_SECRET_ACCESS_KEY', 'S3_BUCKET_NAME']
        missing = [var for var in required_vars if not os.getenv(var)]
        if missing:
            pytest.skip(f"Missing S3 credentials: {', '.join(missing)}. Set environment variables to run integration tests.")
    
    def test_s3_real_connection(self):
        """Test actual S3 connection and basic operations."""
        backend = S3Backend(
            access_key_id=os.getenv('S3_ACCESS_KEY_ID'),
            secret_access_key=os.getenv('S3_SECRET_ACCESS_KEY'),
            bucket_name=os.getenv('S3_BUCKET_NAME'),
            endpoint_url=os.getenv('S3_ENDPOINT_URL', 'https://s3.amazonaws.com')
        )
        
        # Test that we can access bucket (basic connectivity)
        try:
            backend.s3_client.head_bucket(Bucket=backend.bucket_name)
        except Exception as e:
            pytest.fail(f"S3 connection failed: {e}")
    
    def test_s3_write_and_read_file(self):
        """Test writing and reading a file to/from S3."""
        backend = S3Backend(
            access_key_id=os.getenv('S3_ACCESS_KEY_ID'),
            secret_access_key=os.getenv('S3_SECRET_ACCESS_KEY'),
            bucket_name=os.getenv('S3_BUCKET_NAME'),
            endpoint_url=os.getenv('S3_ENDPOINT_URL', 'https://s3.amazonaws.com')
        )
        
        # Write a test file
        test_content = "<html><body>S3 Integration Test</body></html>"
        test_path = "s3-integration-test.html"
        
        try:
            # Write file
            url = backend.write_file(test_path, test_content, "text/html")
            assert url.endswith(test_path)
            
            # Verify file exists and content matches
            response = backend.s3_client.get_object(Bucket=backend.bucket_name, Key=test_path)
            actual_content = response['Body'].read().decode('utf-8')
            assert actual_content == test_content
            
            # Clean up
            backend.s3_client.delete_object(Bucket=backend.bucket_name, Key=test_path)
            
        except Exception as e:
            pytest.fail(f"S3 write/read test failed: {e}")
