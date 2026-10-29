#!/usr/bin/env python3
"""Validate a FlatMonitor domains.yaml without starting the monitor.

Usage:
    python scripts/validate-config.py config/domains.yaml
    python scripts/validate-config.py config/domains.yaml --strict

Exit codes:
    0  valid (warnings allowed unless --strict)
    1  invalid, or warnings with --strict
"""

import argparse
import os
import sys
from pathlib import Path

# Allow running from a source checkout without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Validation should not require production R2 credentials. The raw storage.type
# is still validated; this just avoids expanding credential env vars.
os.environ.setdefault("FLATMONITOR_STORAGE_TYPE", "filesystem")

from app.config import ConfigLoader  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate FlatMonitor configuration")
    parser.add_argument("config", nargs="?", default="config/domains.yaml",
                        help="Path to domains.yaml (default: config/domains.yaml)")
    parser.add_argument("--strict", action="store_true",
                        help="Treat configuration warnings as errors")
    args = parser.parse_args()

    try:
        loader = ConfigLoader(args.config)
        domains = loader.load()
    except Exception as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1

    for warning in loader.validation_warnings:
        print(f"WARNING: {warning}", file=sys.stderr)

    site_ids = loader.get_sites().keys()
    display_names = {
        site_id: (loader.site_configs[site_id].name if site_id in loader.site_configs else site_id)
        for site_id in site_ids
    }
    print(f"OK: {len(domains)} domains, {len(site_ids)} sites")
    for site_id, name in display_names.items():
        print(f"  {site_id}: {name}")

    if args.strict and loader.validation_warnings:
        print(f"INVALID: {len(loader.validation_warnings)} warning(s) with --strict", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
