#!/usr/bin/env python3
"""Canonical metadata for VoCoType native-package flavors."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vocotype_package import package_flavor_metadata

metadata = package_flavor_metadata


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("flavor")
    parser.add_argument("--field")
    args = parser.parse_args()
    try:
        result = metadata(args.flavor)
    except ValueError as exc:
        parser.error(str(exc))
    if args.field:
        if args.field not in result:
            parser.error(f"unknown field: {args.field}")
        value = result[args.field]
        if isinstance(value, bool):
            print("true" if value else "false")
        elif isinstance(value, list):
            print(" ".join(str(item) for item in value))
        else:
            print(value)
    else:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
