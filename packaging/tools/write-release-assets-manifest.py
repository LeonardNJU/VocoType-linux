#!/usr/bin/env python3
"""Write a machine-readable manifest for every final GitHub Release asset."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    output = args.output.resolve()
    excluded = {output, root / "SHA256SUMS.all"}
    assets = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path in excluded:
            continue
        assets.append(
            {
                "path": path.relative_to(root).as_posix(),
                "size": path.stat().st_size,
                "sha256": digest(path),
            }
        )
    payload = {
        "schema_version": 1,
        "project": "vocotype-linux",
        "tag": args.tag,
        "commit": args.commit,
        "assets": assets,
    }
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
