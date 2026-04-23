"""
FlatMonitor - Storage Backends

Pluggable storage backends for the generated HTML dashboard.
Supports local filesystem (default) and R2/S3-compatible object storage.
"""

import os
import hashlib
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Dict
from datetime import datetime, timezone

try:
    import boto3
    from botocore.exceptions import ClientError
    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False


class StorageBackend(ABC):
    """Abstract base class for storage backends."""

    @abstractmethod
    def write_file(self, relative_path: str, content: str, content_type: str = "text/html") -> str:
        """
        Write file and return public URL.

        Args:
            relative_path: Path relative to storage root (e.g., "index.html", "site/mysite.html")
            content: File content as string
            content_type: MIME type of the content

        Returns:
            Public URL to access the file
        """
        pass

    @abstractmethod
    def get_public_url(self, relative_path: str) -> str:
        """Get the public URL for a file without writing."""
        pass

    @abstractmethod
    def upload_logs(self, data_dir: Path) -> None:
        """Upload log files from data/live/ to storage. Called after dashboard build."""
        pass

    @abstractmethod
    def get_log_public_url(self, site_id: str, domain_name: str) -> str:
        """Get the public URL for a log file."""
        pass

    @abstractmethod
    def get_archive_log_public_url(self, site_id: str, domain_name: str, date: str) -> str:
        """Get the public URL for an archived log file. Date format: YYYY-MM-DD."""
        pass

    @abstractmethod
    def upload_assets(self, assets_dir: Path) -> None:
        """Upload static assets (images, etc.) from assets_dir to storage."""
        pass


class FilesystemBackend(StorageBackend):
    """Default filesystem storage backend."""

    def __init__(self, output_dir: str = "public"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def write_file(self, relative_path: str, content: str, content_type: str = "text/html") -> str:
        """Write file to local filesystem."""
        output_path = self.output_dir / relative_path
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)

        return str(output_path.resolve())

    def get_public_url(self, relative_path: str) -> str:
        """Get local filesystem path as URL."""
        output_path = self.output_dir / relative_path
        return str(output_path.resolve())

    def upload_logs(self, data_dir: Path) -> None:
        """No-op for filesystem backend - logs are already local."""
        pass

    def get_log_public_url(self, site_id: str, domain_name: str) -> str:
        """Return relative URL path to log file from site page."""
        # Site pages are in public/, logs are in data/live/
        # From public/{site}.html, go up one level then to data/
        return f"../data/live/{site_id}/{domain_name}.log"

    def get_archive_log_public_url(self, site_id: str, domain_name: str, date: str) -> str:
        """Return relative URL path to archived log file from site page."""
        return f"../data/archive/{date}/{site_id}/{domain_name}.log"

    def upload_assets(self, assets_dir: Path) -> None:
        """No-op for filesystem backend - assets are already local."""
        pass


class R2Backend(StorageBackend):
    """
    Cloudflare R2 storage backend.

    Uses S3-compatible API via boto3.
    Supports environment variables for credentials with config YAML overrides.
    """

    def __init__(
        self,
        account_id: str,
        access_key_id: str,
        secret_access_key: str,
        bucket_name: str,
        public_domain: Optional[str] = None,
        endpoint_url: Optional[str] = None,
        region: str = "auto",
        cache_max_age: int = 60
    ):
        if not BOTO3_AVAILABLE:
            raise ImportError("boto3 is required for R2 backend. Install with: pip install boto3")

        self.account_id = account_id
        self.bucket_name = bucket_name
        self.public_domain = public_domain
        self.cache_max_age = cache_max_age

        # Use provided endpoint or construct from account_id
        if endpoint_url:
            self.endpoint_url = endpoint_url
        else:
            self.endpoint_url = f"https://{account_id}.r2.cloudflarestorage.com"

        # Initialize S3 client for R2
        self.s3_client = boto3.client(
            "s3",
            endpoint_url=self.endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name=region
        )

        # Content deduplication cache: path -> hash
        self._content_cache: Dict[str, str] = {}

    def write_file(self, relative_path: str, content: str, content_type: str = "text/html") -> str:
        """
        Upload file to R2 with content deduplication.

        Skips upload if content hasn't changed (based on hash).
        """
        # Calculate content hash for deduplication
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

        # Check if content changed
        if relative_path in self._content_cache:
            if self._content_cache[relative_path] == content_hash:
                # Content unchanged, skip upload
                return self.get_public_url(relative_path)

        # Prepare metadata (boto3 uses camelCase, custom metadata goes in Metadata dict)
        metadata = {
            "flatmonitor-generated": datetime.now(timezone.utc).isoformat(),
            "content-hash": content_hash
        }

        try:
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=relative_path,
                Body=content.encode("utf-8"),
                ContentType=content_type,
                CacheControl=f"max-age=10, public",
                Metadata=metadata
            )

            # Update cache
            self._content_cache[relative_path] = content_hash

        except ClientError as e:
            raise RuntimeError(f"Failed to upload {relative_path} to R2: {e}") from e

        return self.get_public_url(relative_path)

    def get_public_url(self, relative_path: str) -> str:
        """Get public URL for file."""
        if self.public_domain:
            # Custom domain (e.g., https://status.yourdomain.com/index.html)
            base = self.public_domain.rstrip("/")
            return f"{base}/{relative_path}"
        else:
            # R2.dev subdomain
            return f"https://{self.bucket_name}.{self.account_id}.r2.dev/{relative_path}"

    def file_exists(self, relative_path: str) -> bool:
        """Check if file exists in bucket."""
        try:
            self.s3_client.head_object(Bucket=self.bucket_name, Key=relative_path)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "404":
                return False
            raise

    def upload_logs(self, data_dir: Path) -> None:
        """Upload log files from data/live/ to R2 storage."""
        live_dir = data_dir / "live"
        if not live_dir.exists():
            return

        import logging
        logger = logging.getLogger(__name__)
        uploaded_count = 0

        for site_dir in live_dir.iterdir():
            if not site_dir.is_dir():
                continue

            site_id = site_dir.name
            for log_file in site_dir.glob("*.log"):
                try:
                    # Read log file content
                    with open(log_file, "rb") as f:
                        content = f.read()

                    if not content:
                        continue

                    # Upload to R2 with path: logs/{site_id}/{domain_name}.log
                    domain_name = log_file.stem
                    key = f"logs/{site_id}/{domain_name}.log"

                    # Calculate hash for deduplication
                    content_hash = hashlib.sha256(content).hexdigest()

                    # Check if content changed (using cache)
                    cache_key = f"log:{key}"
                    if cache_key in self._content_cache:
                        if self._content_cache[cache_key] == content_hash:
                            continue  # Skip unchanged files

                    # Upload to R2
                    self.s3_client.put_object(
                        Bucket=self.bucket_name,
                        Key=key,
                        Body=content,
                        ContentType="text/plain",
                        CacheControl="max-age=60, public",
                        Metadata={
                            "flatmonitor-generated": datetime.now(timezone.utc).isoformat(),
                            "content-hash": content_hash
                        }
                    )

                    # Update cache
                    self._content_cache[cache_key] = content_hash
                    uploaded_count += 1

                except Exception as e:
                    logger.warning(f"Failed to upload log {log_file}: {e}")

        # Upload archived logs
        archive_dir = data_dir / "archive"
        if archive_dir.exists():
            for date_dir in archive_dir.iterdir():
                if not date_dir.is_dir():
                    continue
                date = date_dir.name
                for site_dir in date_dir.iterdir():
                    if not site_dir.is_dir():
                        continue
                    site_id = site_dir.name
                    for log_file in site_dir.glob("*.log"):
                        try:
                            with open(log_file, "rb") as f:
                                content = f.read()
                            if not content:
                                continue
                            domain_name = log_file.stem
                            key = f"logs/archive/{date}/{site_id}/{domain_name}.log"
                            content_hash = hashlib.sha256(content).hexdigest()
                            cache_key = f"log:{key}"
                            if cache_key in self._content_cache:
                                if self._content_cache[cache_key] == content_hash:
                                    continue
                            self.s3_client.put_object(
                                Bucket=self.bucket_name,
                                Key=key,
                                Body=content,
                                ContentType="text/plain",
                                CacheControl="max-age=3600, public",
                                Metadata={
                                    "flatmonitor-generated": datetime.now(timezone.utc).isoformat(),
                                    "content-hash": content_hash,
                                    "archive-date": date
                                }
                            )
                            self._content_cache[cache_key] = content_hash
                            uploaded_count += 1
                        except Exception as e:
                            logger.warning(f"Failed to upload archive {log_file}: {e}")

        if uploaded_count > 0:
            logger.info(f"Uploaded {uploaded_count} log files to R2")

    def get_log_public_url(self, site_id: str, domain_name: str) -> str:
        """Get the public URL for a log file in R2."""
        key = f"logs/{site_id}/{domain_name}.log"
        return self.get_public_url(key)

    def get_archive_log_public_url(self, site_id: str, domain_name: str, date: str) -> str:
        """Get the public URL for an archived log file in R2."""
        key = f"logs/archive/{date}/{site_id}/{domain_name}.log"
        return self.get_public_url(key)

    def upload_assets(self, assets_dir: Path) -> None:
        """Upload static assets (images, etc.) from assets_dir to R2 storage."""
        import logging
        import mimetypes
        logger = logging.getLogger(__name__)

        if not assets_dir.exists():
            logger.warning(f"Assets directory does not exist: {assets_dir}")
            return

        logger.info(f"Scanning assets directory: {assets_dir}")
        uploaded_count = 0
        skipped_count = 0
        file_count = 0

        for asset_file in assets_dir.iterdir():
            if not asset_file.is_file():
                logger.debug(f"Skipping non-file: {asset_file}")
                continue

            file_count += 1
            try:
                # Read file content
                with open(asset_file, "rb") as f:
                    content = f.read()

                if not content:
                    logger.warning(f"Empty file, skipping: {asset_file.name}")
                    continue

                # Upload to R2 with path: assets/{filename}
                key = f"assets/{asset_file.name}"
                public_url = self.get_public_url(key)

                # Calculate hash for deduplication
                content_hash = hashlib.sha256(content).hexdigest()

                # Check if content changed (using cache)
                cache_key = f"asset:{key}"
                if cache_key in self._content_cache:
                    if self._content_cache[cache_key] == content_hash:
                        logger.debug(f"Asset unchanged, skipping: {key}")
                        skipped_count += 1
                        continue  # Skip unchanged files

                # Guess content type
                content_type, _ = mimetypes.guess_type(str(asset_file))
                if not content_type:
                    content_type = "application/octet-stream"

                logger.info(f"Uploading asset: {key} ({len(content)} bytes, type: {content_type})")

                # Upload to R2
                self.s3_client.put_object(
                    Bucket=self.bucket_name,
                    Key=key,
                    Body=content,
                    ContentType=content_type,
                    CacheControl="max-age=3600, public",
                    Metadata={
                        "flatmonitor-generated": datetime.now(timezone.utc).isoformat(),
                        "content-hash": content_hash
                    }
                )

                # Update cache
                self._content_cache[cache_key] = content_hash
                uploaded_count += 1
                logger.info(f"Asset uploaded: {public_url}")

            except Exception as e:
                logger.error(f"Failed to upload asset {asset_file}: {e}")

        logger.info(f"Asset upload complete: {uploaded_count} uploaded, {skipped_count} skipped, {file_count} files scanned")


class S3Backend(R2Backend):
    """
    AWS S3 storage backend (or any S3-compatible service).

    Inherits from R2Backend since the API is identical.
    Just requires explicit endpoint_url for non-AWS S3 services.
    """

    def __init__(
        self,
        access_key_id: str,
        secret_access_key: str,
        bucket_name: str,
        region: str = "us-east-1",
        endpoint_url: Optional[str] = None,
        public_domain: Optional[str] = None,
        cache_max_age: int = 60
    ):
        if not BOTO3_AVAILABLE:
            raise ImportError("boto3 is required for S3 backend. Install with: pip install boto3")

        # For AWS S3, don't provide endpoint_url (uses standard AWS endpoints)
        # For S3-compatible services (MinIO, etc.), provide custom endpoint_url

        self.bucket_name = bucket_name
        self.public_domain = public_domain
        self.cache_max_age = cache_max_age

        # Initialize S3 client
        client_kwargs = {
            "aws_access_key_id": access_key_id,
            "aws_secret_access_key": secret_access_key,
            "region_name": region
        }
        if endpoint_url:
            client_kwargs["endpoint_url"] = endpoint_url

        self.s3_client = boto3.client("s3", **client_kwargs)
        self.endpoint_url = endpoint_url
        self._content_cache: Dict[str, str] = {}

        # For get_public_url, we need account_id placeholder (not used for AWS S3)
        self.account_id = "s3"

    def get_public_url(self, relative_path: str) -> str:
        """Get public URL for file."""
        if self.public_domain:
            base = self.public_domain.rstrip("/")
            return f"{base}/{relative_path}"
        elif self.endpoint_url:
            # S3-compatible service without custom domain
            return f"{self.endpoint_url}/{self.bucket_name}/{relative_path}"
        else:
            # AWS S3 website endpoint
            return f"https://{self.bucket_name}.s3.amazonaws.com/{relative_path}"


class MultiStorageBackend(StorageBackend):
    """
    Multi-backend storage that writes to both primary and secondary backends.

    Primary backend (e.g., R2/S3) is used for public URLs.
    Secondary backend (filesystem) is for local backup/debugging.
    """

    def __init__(self, primary: StorageBackend, secondary: StorageBackend):
        self.primary = primary
        self.secondary = secondary

    def write_file(self, relative_path: str, content: str, content_type: str = "text/html") -> str:
        """Write file to both backends, return primary URL."""
        # Write to primary (cloud) backend
        primary_url = self.primary.write_file(relative_path, content, content_type)
        # Write to secondary (local) backend
        self.secondary.write_file(relative_path, content, content_type)
        return primary_url

    def get_public_url(self, relative_path: str) -> str:
        """Get public URL from primary backend."""
        return self.primary.get_public_url(relative_path)

    def upload_logs(self, data_dir: Path) -> None:
        """Upload logs to primary, optionally to secondary if it's not filesystem."""
        self.primary.upload_logs(data_dir)
        # Only upload to secondary if it's a cloud backend (filesystem already has logs locally)
        if not isinstance(self.secondary, FilesystemBackend):
            self.secondary.upload_logs(data_dir)

    def get_log_public_url(self, site_id: str, domain_name: str) -> str:
        """Get log URL from primary backend."""
        return self.primary.get_log_public_url(site_id, domain_name)

    def get_archive_log_public_url(self, site_id: str, domain_name: str, date: str) -> str:
        """Get archive log URL from primary backend."""
        return self.primary.get_archive_log_public_url(site_id, domain_name, date)

    def upload_assets(self, assets_dir: Path) -> None:
        """Upload assets to primary backend."""
        self.primary.upload_assets(assets_dir)


def create_storage_backend(config: dict) -> StorageBackend:
    """
    Factory function to create appropriate storage backend from config.

    Config format:
    {
        "type": "filesystem" | "r2" | "s3",
        "filesystem": {"output_dir": "public", "enabled": true},
        "r2": {
            "account_id": "..." or "${R2_ACCOUNT_ID}",
            "access_key_id": "..." or "${R2_ACCESS_KEY_ID}",
            "secret_access_key": "..." or "${R2_SECRET_ACCESS_KEY}",
            "bucket_name": "flatmonitor-dashboard",
            "public_domain": "https://status.example.com"  # optional
        },
        "s3": {
            "access_key_id": "...",
            "secret_access_key": "...",
            "bucket_name": "flatmonitor-dashboard",
            "region": "us-east-1",
            "endpoint_url": "...",  # optional, for S3-compatible services
            "public_domain": "..."  # optional
        }
    }

    Environment variables are resolved from ${VAR_NAME} syntax.

    When using r2 or s3 with filesystem.enabled=true, creates a MultiStorageBackend
    that writes to both backends (primary=cloud, secondary=local).
    """
    backend_type = config.get("type", "filesystem")
    fs_config = config.get("filesystem", {})
    filesystem_enabled = fs_config.get("enabled", True)

    if backend_type == "filesystem":
        fs_config = config.get("filesystem", {})
        output_dir = fs_config.get("output_dir", "public")
        return FilesystemBackend(output_dir)

    elif backend_type == "r2":
        r2_config = config.get("r2", {})

        # Resolve environment variables
        account_id = _resolve_env_var(r2_config.get("account_id", "${R2_ACCOUNT_ID}"))
        access_key_id = _resolve_env_var(r2_config.get("access_key_id", "${R2_ACCESS_KEY_ID}"))
        secret_access_key = _resolve_env_var(r2_config.get("secret_access_key", "${R2_SECRET_ACCESS_KEY}"))
        bucket_name = _resolve_env_var(r2_config.get("bucket_name", "${R2_BUCKET_NAME}"))

        if not all([account_id, access_key_id, secret_access_key, bucket_name]):
            missing = []
            if not account_id:
                missing.append("account_id (R2_ACCOUNT_ID)")
            if not access_key_id:
                missing.append("access_key_id (R2_ACCESS_KEY_ID)")
            if not secret_access_key:
                missing.append("secret_access_key (R2_SECRET_ACCESS_KEY)")
            if not bucket_name:
                missing.append("bucket_name (R2_BUCKET_NAME)")
            raise ValueError(f"Missing required R2 configuration: {', '.join(missing)}")

        r2_backend = R2Backend(
            account_id=account_id,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            bucket_name=bucket_name,
            public_domain=r2_config.get("public_domain"),
            endpoint_url=r2_config.get("endpoint_url"),
            region=r2_config.get("region", "auto"),
            cache_max_age=r2_config.get("cache_max_age", 60)
        )

        # Wrap in MultiStorageBackend if filesystem output is also enabled
        if filesystem_enabled:
            output_dir = fs_config.get("output_dir", "public")
            fs_backend = FilesystemBackend(output_dir)
            return MultiStorageBackend(primary=r2_backend, secondary=fs_backend)

        return r2_backend

    elif backend_type == "s3":
        s3_config = config.get("s3", {})

        # Resolve environment variables
        access_key_id = _resolve_env_var(s3_config.get("access_key_id", "${AWS_ACCESS_KEY_ID}"))
        secret_access_key = _resolve_env_var(s3_config.get("secret_access_key", "${AWS_SECRET_ACCESS_KEY}"))
        bucket_name = _resolve_env_var(s3_config.get("bucket_name", "${S3_BUCKET_NAME}"))

        if not all([access_key_id, secret_access_key, bucket_name]):
            missing = []
            if not access_key_id:
                missing.append("access_key_id (AWS_ACCESS_KEY_ID)")
            if not secret_access_key:
                missing.append("secret_access_key (AWS_SECRET_ACCESS_KEY)")
            if not bucket_name:
                missing.append("bucket_name (S3_BUCKET_NAME)")
            raise ValueError(f"Missing required S3 configuration: {', '.join(missing)}")

        s3_backend = S3Backend(
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            bucket_name=bucket_name,
            region=s3_config.get("region", "us-east-1"),
            endpoint_url=s3_config.get("endpoint_url"),
            public_domain=s3_config.get("public_domain"),
            cache_max_age=s3_config.get("cache_max_age", 60)
        )

        # Wrap in MultiStorageBackend if filesystem output is also enabled
        if filesystem_enabled:
            output_dir = fs_config.get("output_dir", "public")
            fs_backend = FilesystemBackend(output_dir)
            return MultiStorageBackend(primary=s3_backend, secondary=fs_backend)

        return s3_backend

    else:
        raise ValueError(f"Unknown storage backend type: {backend_type}")


def _resolve_env_var(value: str) -> str:
    """
    Resolve environment variable references in config values.

    Supports ${VAR_NAME} syntax. If the env var is not set and no default
    is provided, returns the original string (which may be empty if it was
    just ${VAR_NAME}).

    Examples:
        "${R2_ACCOUNT_ID}" -> value of R2_ACCOUNT_ID env var
        "${R2_ACCOUNT_ID:-default}" -> env var value or "default"
        "plaintext" -> "plaintext" (unchanged)
    """
    if not isinstance(value, str) or not value.startswith("${") or not value.endswith("}"):
        return value

    # Strip ${ and }
    inner = value[2:-1]

    # Check for default value syntax: ${VAR:-default}
    if ":-" in inner:
        var_name, default = inner.split(":-", 1)
        return os.environ.get(var_name, default)
    else:
        return os.environ.get(inner, "")
