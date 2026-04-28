"""
FlatMonitor - CSV Schema Version Registry

Centralized definitions of CSV schema versions for backward compatibility.
This module provides a registry of all schema versions and their field definitions.
"""

from typing import Dict, Any


CSV_SCHEMA_VERSIONS: Dict[int, Dict[str, Any]] = {
    1: {
        "fields": ["timestamp", "site_id", "domain_id", "domain_status",
                   "http_status", "latency_ms", "failure_type"],
        "description": "Original schema without protection_type field",
        "deprecated": False,
        "added_in": "1.0.0",
    },
    2: {
        "fields": ["timestamp", "site_id", "domain_id", "domain_status",
                   "http_status", "latency_ms", "failure_type", "protection_type"],
        "description": "Added protection_type field for bot protection detection",
        "deprecated": False,
        "added_in": "1.x.x",  # Update when version is known
    },
}

CURRENT_SCHEMA_VERSION = 2


def get_schema_version(version: int) -> Dict[str, Any]:
    """Get schema definition for a specific version.

    Args:
        version: Schema version number

    Returns:
        dict: Schema definition containing fields, description, etc.

    Raises:
        ValueError: If version is not defined
    """
    if version not in CSV_SCHEMA_VERSIONS:
        raise ValueError(f"Unknown schema version: {version}")
    return CSV_SCHEMA_VERSIONS[version]


def get_fields_for_version(version: int) -> list:
    """Get field list for a specific schema version.

    Args:
        version: Schema version number

    Returns:
        list: Ordered list of field names
    """
    schema = get_schema_version(version)
    return schema["fields"].copy()


def get_version_history() -> Dict[int, Dict[str, Any]]:
    """Get full version history dictionary.

    Returns:
        dict: All defined schema versions
    """
    return CSV_SCHEMA_VERSIONS.copy()


def is_deprecated(version: int) -> bool:
    """Check if a schema version is deprecated.

    Args:
        version: Schema version number

    Returns:
        bool: True if version is deprecated
    """
    try:
        return get_schema_version(version).get("deprecated", False)
    except ValueError:
        return False


def get_field_changes(from_version: int, to_version: int) -> Dict[str, list]:
    """Get list of fields added and removed between two versions.

    Args:
        from_version: Source schema version
        to_version: Target schema version

    Returns:
        dict: {'added': [...], 'removed': [...]}
    """
    from_fields = set(get_fields_for_version(from_version))
    to_fields = set(get_fields_for_version(to_version))

    return {
        "added": list(to_fields - from_fields),
        "removed": list(from_fields - to_fields),
    }
