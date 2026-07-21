#!/usr/bin/env python3
"""Canonical metadata for VoCoType native-package flavors."""
from __future__ import annotations

import argparse
import json

FLAVORS = {
    "universal": {
        "package_name": "vocotype-linux",
        "title": "IBus and Fcitx 5",
        "summary": "Offline voice input for IBus and Fcitx 5",
        "includes_ibus": True,
        "includes_fcitx5": True,
    },
    "ibus": {
        "package_name": "vocotype-linux-ibus",
        "title": "IBus",
        "summary": "Offline voice input for IBus",
        "includes_ibus": True,
        "includes_fcitx5": False,
    },
    "fcitx5": {
        "package_name": "vocotype-linux-fcitx5",
        "title": "Fcitx 5",
        "summary": "Offline voice input for Fcitx 5",
        "includes_ibus": False,
        "includes_fcitx5": True,
    },
}


def metadata(value: str) -> dict[str, object]:
    key = str(value or "").strip().lower()
    aliases = {"fcitx": "fcitx5", "all": "universal", "both": "universal"}
    key = aliases.get(key, key)
    if key not in FLAVORS:
        raise ValueError(f"unknown package flavor: {value}")
    result = dict(FLAVORS[key])
    result["flavor"] = key
    package_names = [item["package_name"] for item in FLAVORS.values()]
    result["conflicts"] = [name for name in package_names if name != result["package_name"]]
    return result


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
