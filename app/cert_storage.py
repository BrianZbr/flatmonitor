"""
FlatMonitor - Certificate Storage

Manages SSL certificate metadata separately from check results.
Stores cert expiry in JSON files with TTL-based caching.
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict


class CertStorage:
    """Manages SSL certificate metadata with TTL caching."""

    DEFAULT_TTL_SECONDS = 86400  # 24 hours

    def __init__(self, data_dir: str = "data", ttl_seconds: int = DEFAULT_TTL_SECONDS):
        self.certs_dir = Path(data_dir) / "certs"
        self.certs_dir.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = ttl_seconds

    def get_cert_expiry(self, site_id: str, domain_name: str, url: str,
                        fetch_callback) -> Optional[str]:
        """
        Get certificate expiry with caching.

        Args:
            site_id: Site identifier
            domain_name: Domain name
            url: HTTPS URL to check
            fetch_callback: Function to call when cache miss/expired (should return cert_expiry string or None)

        Returns:
            ISO format expiry date or None
        """
        cert_path = self._get_cert_path(site_id, domain_name)

        # Check if cached cert is still valid
        if cert_path.exists():
            try:
                with open(cert_path, 'r') as f:
                    data = json.load(f)

                cached_expiry = data.get('cert_expiry')
                last_check = data.get('last_check', 0)

                # Use cache if not expired
                if time.time() - last_check < self.ttl_seconds:
                    return cached_expiry

            except (json.JSONDecodeError, IOError):
                pass  # Fall through to fetch

        # Fetch fresh cert data
        expiry = fetch_callback()

        # Store in cache
        self._store_cert(site_id, domain_name, expiry)

        return expiry

    def get_cert_info(self, site_id: str, domain_name: str) -> Optional[Dict]:
        """
        Get full cert info for a domain.

        Returns dict with cert_expiry, last_check, is_valid, or None if not cached.
        """
        cert_path = self._get_cert_path(site_id, domain_name)

        if not cert_path.exists():
            return None

        try:
            with open(cert_path, 'r') as f:
                data = json.load(f)

            last_check = data.get('last_check', 0)
            age_seconds = time.time() - last_check
            cert_expiry = data.get('cert_expiry')

            # Check if cert is expired
            is_valid = False
            days_remaining = None
            if cert_expiry:
                try:
                    expiry_dt = datetime.fromisoformat(cert_expiry.replace('Z', '+00:00'))
                    days_remaining = (expiry_dt - datetime.now(timezone.utc)).days
                    is_valid = days_remaining > 0
                except ValueError:
                    pass

            return {
                'cert_expiry': cert_expiry,
                'last_check': last_check,
                'age_seconds': age_seconds,
                'is_valid': is_valid,
                'days_remaining': days_remaining,
                'is_fresh': age_seconds < self.ttl_seconds
            }

        except (json.JSONDecodeError, IOError):
            return None

    def _store_cert(self, site_id: str, domain_name: str,
                    cert_expiry: Optional[str]) -> None:
        """Store cert data in JSON file."""
        cert_path = self._get_cert_path(site_id, domain_name)
        cert_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            'site_id': site_id,
            'domain_name': domain_name,
            'cert_expiry': cert_expiry,
            'last_check': time.time()
        }

        with open(cert_path, 'w') as f:
            json.dump(data, f, indent=2)

    def _get_cert_path(self, site_id: str, domain_name: str) -> Path:
        """Get the file path for a domain's cert metadata."""
        return self.certs_dir / site_id / f"{domain_name}.json"

    def cleanup(self, max_age_days: int = 30) -> None:
        """Remove cert cache files older than max_age_days."""
        cutoff_time = time.time() - (max_age_days * 86400)

        for site_dir in self.certs_dir.iterdir():
            if not site_dir.is_dir():
                continue

            for cert_file in site_dir.glob("*.json"):
                try:
                    if cert_file.stat().st_mtime < cutoff_time:
                        cert_file.unlink()
                except OSError:
                    pass

            # Remove empty site directories
            try:
                if site_dir.exists() and not any(site_dir.iterdir()):
                    site_dir.rmdir()
            except OSError:
                pass
