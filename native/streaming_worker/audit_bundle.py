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
CORE_SYSTEM_LIBS = {
    "libbrotlicommon.so.1",
    "libbrotlidec.so.1",
    "libcom_err.so.2",
    "libcrypto.so.3",
    "libcurl.so.4",
    "libffi.so.8",
    "libgmp.so.10",
    "libgnutls.so.30",
    "libgssapi_krb5.so.2",
    "libhogweed.so.6",
    "libidn2.so.0",
    "libk5crypto.so.3",
    "libkeyutils.so.1",
    "libkrb5.so.3",
    "libkrb5support.so.0",
    "libldap-2.5.so.0",
    "liblber-2.5.so.0",
    "libnettle.so.8",
    "libnghttp2.so.14",
    "libp11-kit.so.0",
    "libpsl.so.5",
    "libresolv.so.2",
    "librtmp.so.1",
    "libssh.so.4",
    "libssl.so.3",
    "libtasn1.so.6",
    "libunistring.so.2",
    "libz.so.1",
    "libzstd.so.1",
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
    for path in [
        root / "bin/vocotype-core",
        root / "bin/vocotype-streaming-worker",
        root / "bin/vocotype-offline-worker",
        *lib_dir.iterdir(),
    ]:
        if path.is_symlink() or not path.is_file():
            continue
        section = dynamic_section(path)
        if not section:
            continue
        allowed = set(ALLOWED_HOST_LIBS)
        if path.name == "vocotype-core":
            allowed.update(CORE_SYSTEM_LIBS)
        for dependency in NEEDED_RE.findall(section):
            if dependency not in bundled and dependency not in allowed:
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
