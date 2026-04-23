"""
FlatMonitor - Storage Manager

CSV storage with rotation and retention. Single-writer pattern for thread safety.
"""

import os
import csv
import shutil
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Optional

from app.models import Result, DomainConfig

logger = logging.getLogger(__name__)

class Storage:
    """Manages CSV storage with rotation and retention."""

    def __init__(self, data_dir: str = "data", live_dir_name: str = "live",
                 archive_dir_name: str = "archive", retention_days: int = 7):
        self.data_dir = Path(data_dir)
        self.live_dir = self.data_dir / live_dir_name
        self.archive_dir = self.data_dir / archive_dir_name
        self.retention_days = retention_days

        # Ensure directories exist
        self.live_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)

        # CSV headers
        self.headers = ["timestamp", "site_id", "domain_id", "domain_status",
                        "http_status", "latency_ms", "failure_type"]

    def append_csv(self, result: Result) -> None:
        """Append a result to the appropriate CSV file."""
        domain_path = self._get_domain_path(result.site_id, result.domain_id)
        domain_path.parent.mkdir(parents=True, exist_ok=True)

        # Check if file exists to write headers
        file_exists = domain_path.exists()

        with open(domain_path, "a", newline="") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(self.headers)
            writer.writerow(result.to_csv_row())

    def read_domain_results(self, site_id: str, domain_id: str,
                           hours: int = 4) -> List[Result]:
        """Read results for a specific domain from the last N hours."""
        domain_path = self._get_domain_path(site_id, domain_id)

        if not domain_path.exists():
            return []

        results = []
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours)

        with open(domain_path, "r") as f:
            reader = csv.reader(f)
            # Skip header
            next(reader, None)

            for row in reader:
                if len(row) < 4:
                    continue

                try:
                    result = Result.from_csv_row(row)
                    result_time = datetime.fromisoformat(
                        result.timestamp.replace("Z", "+00:00")
                    )

                    if result_time >= cutoff_time:
                        results.append(result)
                except (ValueError, IndexError):
                    continue

        return results

    def read_site_results(self, site_id: str, hours: int = 4) -> dict:
        """Read all results for a site, grouped by domain."""
        site_dir = self.live_dir / site_id

        if not site_dir.exists():
            return {}

        results_by_domain = {}

        for domain_file in site_dir.glob("*.log"):
            domain_name = domain_file.stem
            domain_id = f"{site_id}.{domain_name}"
            results_by_domain[domain_id] = self.read_domain_results(
                site_id, domain_id, hours
            )

        return results_by_domain

    def rotate(self) -> None:
        """
        Rotate files from live to archive.
        Called every hour to archive current logs.
        Only truncates live files after verifying data was successfully archived.
        Archives are organized by month (YYYY-MM) for easier historical analysis.
        """
        current_month = datetime.now(timezone.utc).strftime("%Y-%m")
        month_archive_dir = self.archive_dir / current_month
        month_archive_dir.mkdir(parents=True, exist_ok=True)

        rotated_count = 0
        error_count = 0

        for site_dir in self.live_dir.iterdir():
            if not site_dir.is_dir():
                continue

            site_id = site_dir.name
            site_archive_dir = month_archive_dir / site_id
            site_archive_dir.mkdir(parents=True, exist_ok=True)

            for domain_file in site_dir.glob("*.log"):
                try:
                    # Append to archive (don't overwrite)
                    archive_path = site_archive_dir / domain_file.name
                    rows_archived = self._append_to_archive(domain_file, archive_path)

                    # Only truncate if data was successfully archived
                    if rows_archived > 0:
                        # Truncate the live file (keep only headers)
                        with open(domain_file, "r") as f:
                            reader = csv.reader(f)
                            headers = next(reader, None)

                        with open(domain_file, "w", newline="") as f:
                            writer = csv.writer(f)
                            if headers:
                                writer.writerow(headers)
                        rotated_count += 1
                        logger.debug(f"Rotated {domain_file.name}: {rows_archived} rows archived")
                    elif rows_archived == 0:
                        # No data to archive, but file might have headers - safe to keep as-is
                        logger.debug(f"Skipped {domain_file.name}: no data rows to archive")

                except Exception as e:
                    error_count += 1
                    logger.error(f"Failed to rotate {domain_file}: {e}")
                    # Do NOT truncate on error - preserve live data
                    continue

        # Update archive index for any successfully rotated sites
        if rotated_count > 0:
            for site_dir in self.live_dir.iterdir():
                if not site_dir.is_dir():
                    continue
                site_id = site_dir.name
                self.update_archive_index(site_id, current_month)

        logger.info(f"Rotation complete: {rotated_count} files rotated, {error_count} errors")

    def cleanup(self) -> None:
        """Delete archives older than retention_days."""
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=self.retention_days)

        for month_dir in self.archive_dir.iterdir():
            if not month_dir.is_dir():
                continue

            try:
                # Parse YYYY-MM format
                dir_date = datetime.strptime(month_dir.name, "%Y-%m").replace(tzinfo=timezone.utc)
                # Use last day of the month for comparison
                from calendar import monthrange
                last_day = monthrange(dir_date.year, dir_date.month)[1]
                month_end = dir_date.replace(day=last_day)
                if month_end < cutoff_date:
                    shutil.rmtree(month_dir)
                    logger.info(f"Cleaned up archive: {month_dir.name}")
            except ValueError:
                # Not a date-formatted directory, skip
                continue

    def get_archive_index_path(self) -> Path:
        """Get path to the archive index file."""
        return self.data_dir / "archive_index.json"

    def read_archive_index(self) -> dict:
        """Read the archive index file. Returns dict of site_id -> list of archive months."""
        index_path = self.get_archive_index_path()
        if not index_path.exists():
            return {}
        try:
            import json
            with open(index_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to read archive index: {e}")
            return {}

    def update_archive_index(self, site_id: str, month: str) -> None:
        """Update the archive index with a new archive entry."""
        index = self.read_archive_index()
        if site_id not in index:
            index[site_id] = []
        if month not in index[site_id]:
            index[site_id].append(month)
            index[site_id] = sorted(index[site_id], reverse=True)
        try:
            import json
            index_path = self.get_archive_index_path()
            with open(index_path, 'w') as f:
                json.dump(index, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to write archive index: {e}")

    def _append_to_archive(self, live_file: Path, archive_path: Path) -> int:
        """
        Append data from live file to archive file without overwriting existing archive data.

        Returns:
            int: Number of data rows (excluding header) that were archived.
                 Returns 0 if no data to archive or file doesn't exist.
        """
        if not live_file.exists():
            return 0

        # Check if archive exists to determine if we need to skip the header
        archive_exists = archive_path.exists()

        with open(live_file, "r") as f:
            reader = csv.reader(f)
            rows = list(reader)

        if not rows:
            return 0

        # If live file has only headers (no data), nothing to append
        if len(rows) == 1:
            return 0

        # If archive doesn't exist, keep header from live file
        # If archive exists, skip header from live file
        data_rows = rows[1:] if archive_exists else rows

        # Write to a temp file first for atomic operation
        temp_path = archive_path.with_suffix('.tmp')

        try:
            # Copy existing archive content if it exists
            if archive_exists:
                shutil.copy2(archive_path, temp_path)

            with open(temp_path, "a", newline="") as f:
                writer = csv.writer(f)
                for row in data_rows:
                    writer.writerow(row)

            # Atomic rename
            temp_path.rename(archive_path)

            return len(data_rows)

        except Exception as e:
            # Clean up temp file if it exists
            if temp_path.exists():
                temp_path.unlink()
            raise e

    def _get_domain_path(self, site_id: str, domain_id: str) -> Path:
        """Get the file path for a domain's log file."""
        # Extract domain name from domain_id (remove site_id prefix)
        if domain_id.startswith(site_id + "."):
            domain_name = domain_id[len(site_id) + 1:]
        else:
            domain_name = domain_id

        return self.live_dir / site_id / f"{domain_name}.log"
