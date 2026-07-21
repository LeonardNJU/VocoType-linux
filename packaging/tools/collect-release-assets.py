#!/usr/bin/env python3
"""Flatten downloaded CI artifacts into the exact GitHub Release asset set."""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    source = args.source.resolve()
    destination = args.destination.resolve()
    if source == destination or source in destination.parents:
        parser.error("destination must be outside the downloaded artifact tree")
    shutil.rmtree(destination, ignore_errors=True)
    destination.mkdir(parents=True)

    seen: dict[str, Path] = {}
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        name = path.name
        previous = seen.get(name)
        if previous is not None:
            raise SystemExit(
                f"duplicate release asset basename {name!r}: {previous} and {path}"
            )
        seen[name] = path
        shutil.copy2(path, destination / name)
    if not seen:
        raise SystemExit("no release assets were collected")
    print(f"Collected {len(seen)} final assets in {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
