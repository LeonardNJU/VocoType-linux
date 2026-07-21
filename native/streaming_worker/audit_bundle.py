#!/usr/bin/env python3
"""Reject host-specific dynamic dependencies and absolute RPATHs in a bundle."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ALLOWED_HOST_LIBS = {
    "libc.so.6",
    "libdl.so.2",
    "libgcc_s.so.1",
    "libm.so.6",
    "libpthread.so.0",
    "librt.so.1",
    "libstdc++.so.6",
    "ld-linux-aarch64.so.1",
    "ld-linux-x86-64.so.2",
}
NEEDED_RE = re.compile(r"Shared library: \[(.+?)\]")
PATH_RE = re.compile(r"Library (?:rpath|runpath): \[(.+?)\]", re.IGNORECASE)


def dynamic_section(path: Path) -> str:
    result = subprocess.run(
        ["readelf", "-d", str(path)],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: audit_bundle.py BUNDLE_DIR", file=sys.stderr)
        return 2
    root = Path(sys.argv[1]).resolve()
    lib_dir = root / "lib"
    bundled = {entry.name for entry in lib_dir.iterdir()}
    errors: list[str] = []
    for path in [root / "bin/vocotype-streaming-worker", *lib_dir.iterdir()]:
        if path.is_symlink() or not path.is_file():
            continue
        section = dynamic_section(path)
        if not section:
            continue
        for dependency in NEEDED_RE.findall(section):
            if dependency not in bundled and dependency not in ALLOWED_HOST_LIBS:
                errors.append(f"{path.name}: unbundled dependency {dependency}")
        for runpath in PATH_RE.findall(section):
            for item in runpath.split(":"):
                if item.startswith("/"):
                    errors.append(f"{path.name}: absolute RUNPATH {item}")
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"Native bundle audit passed: {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
