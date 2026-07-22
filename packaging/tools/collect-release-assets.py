#!/usr/bin/env python3
"""Flatten CI artifacts into GitHub-safe, deterministic Release asset names."""
from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+\-]*$")


def release_asset_name(name: str) -> str:
    # GitHub replaces '~' in uploaded asset names. Normalize before writing the
    # manifest and checksums so downloaded names remain directly verifiable.
    normalized = name.replace("~", ".")
    if normalized != Path(normalized).name or not SAFE_NAME_RE.fullmatch(normalized):
        raise ValueError(f"unsafe GitHub Release asset name: {name!r}")
    return normalized


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
        try:
            name = release_asset_name(path.name)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        previous = seen.get(name)
        if previous is not None:
            raise SystemExit(
                f"duplicate normalized release asset name {name!r}: "
                f"{previous} and {path}"
            )
        seen[name] = path
        shutil.copy2(path, destination / name)
    if not seen:
        raise SystemExit("no release assets were collected")
    print(f"Collected {len(seen)} final assets in {destination}")
    for name in sorted(seen):
        print(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
