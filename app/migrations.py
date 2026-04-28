"""
FlatMonitor - CSV Schema Migrations

Migration functions to transform data between CSV schema versions.
Migrations are applied at read-time to maintain backward compatibility
without rewriting archive files.
"""

import logging
from typing import Callable, Dict, List

from app.schema_versions import (
    get_fields_for_version,
    get_field_changes,
    CURRENT_SCHEMA_VERSION,
)

logger = logging.getLogger(__name__)


# Type alias for migration functions
MigrationFunc = Callable[[List[str], List[str]], List[str]]


def migrate_v1_to_v2(row: List[str], headers: List[str]) -> List[str]:
    """Migrate from schema v1 to v2.

    Changes:
        - Add protection_type field (default: empty string)

    Args:
        row: CSV row data from v1 file
        headers: Headers from v1 file

    Returns:
        list: Migrated row matching v2 schema
    """
    # V1 has 7 fields, V2 has 8 fields (adds protection_type)
    # If row is shorter than expected, pad it
    if len(row) < len(headers):
        row = row + [""] * (len(headers) - len(row))

    # Add protection_type field (empty string = null)
    row.append("")
    return row


# Registry of migration functions between consecutive versions
MIGRATIONS: Dict[tuple, MigrationFunc] = {
    (1, 2): migrate_v1_to_v2,
}


def apply_migrations(row: List[str], from_version: int, to_version: int,
                     headers: List[str]) -> List[str]:
    """Apply all migrations needed to bring row to target schema version.

    Args:
        row: Raw CSV row data from old file
        from_version: Schema version of the source file
        to_version: Target schema version (usually CURRENT_SCHEMA_VERSION)
        headers: Headers from the source file

    Returns:
        list: Migrated row matching target schema
    """
    if from_version >= to_version:
        return row  # No migration needed

    current_row = row.copy()
    current_version = from_version

    while current_version < to_version:
        migration_key = (current_version, current_version + 1)

        if migration_key not in MIGRATIONS:
            logger.warning(
                f"No migration defined from v{current_version} to v{current_version + 1}, "
                f"using default padding"
            )
            # Default: just pad row to match target field count
            target_fields = get_fields_for_version(current_version + 1)
            current_headers = get_fields_for_version(current_version)
            current_row = _default_migration(current_row, current_headers, target_fields)
        else:
            migration_func = MIGRATIONS[migration_key]
            current_headers = get_fields_for_version(current_version)
            current_row = migration_func(current_row, current_headers)

        current_version += 1

    return current_row


def _default_migration(row: List[str], from_headers: List[str],
                       to_headers: List[str]) -> List[str]:
    """Default migration: pad or truncate row to match target headers.

    This is used when no explicit migration is defined.

    Args:
        row: CSV row data
        from_headers: Source schema headers
        to_headers: Target schema headers

    Returns:
        list: Adjusted row
    """
    changes = {"added": list(set(to_headers) - set(from_headers)),
               "removed": list(set(from_headers) - set(to_headers))}

    # Create dict from row
    data = {}
    for i, header in enumerate(from_headers):
        if i < len(row):
            data[header] = row[i]
        else:
            data[header] = ""

    # Build new row from target headers
    new_row = []
    for header in to_headers:
        if header in data:
            new_row.append(data[header])
        else:
            # New field - add empty string
            new_row.append("")

    return new_row


def get_migration_path(from_version: int, to_version: int) -> List[tuple]:
    """Get list of migration steps between two versions.

    Args:
        from_version: Source schema version
        to_version: Target schema version

    Returns:
        list: List of (from, to) tuples representing migration steps
    """
    if from_version >= to_version:
        return []

    steps = []
    current = from_version
    while current < to_version:
        steps.append((current, current + 1))
        current += 1

    return steps


def register_migration(from_version: int, to_version: int,
                     func: MigrationFunc) -> None:
    """Register a new migration function.

    This allows external code to add migrations dynamically.

    Args:
        from_version: Source schema version
        to_version: Target schema version
        func: Migration function
    """
    MIGRATIONS[(from_version, to_version)] = func
    logger.info(f"Registered migration from v{from_version} to v{to_version}")
