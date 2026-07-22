#!/usr/bin/env python3
"""Validate the exact installer-only GitHub Release asset set."""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))
from versioning import ReleaseVersion

SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+\-]*$")
CHECKSUM_RE = re.compile(r"^([0-9a-f]{64})  (?:\./)?(.+)$")
PACKAGE_NAMES = (
    "vocotype-linux",
    "vocotype-linux-ibus",
    "vocotype-linux-fcitx5",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_checksums(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        match = CHECKSUM_RE.fullmatch(line)
        if not match:
            raise ValueError(f"invalid checksum line {line_number}: {line!r}")
        digest, name = match.groups()
        if name in result:
            raise ValueError(f"duplicate checksum entry: {name}")
        result[name] = digest
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    version = ReleaseVersion.parse(args.version)
    root = args.root.resolve()
    if not root.is_dir():
        parser.error(f"asset root does not exist: {root}")

    entries = list(root.iterdir())
    if any(path.is_dir() for path in entries):
        parser.error("final Release assets must be flat")
    files = sorted(path for path in entries if path.is_file())
    names = {path.name for path in files}
    if "SHA256SUMS" not in names:
        parser.error("installer checksum file SHA256SUMS is missing")
    if len(names) != 10:
        parser.error(
            "public Release must contain exactly 9 installers and SHA256SUMS; "
            f"found {len(names)} files: {sorted(names)}"
        )
    for name in names:
        if "~" in name or not SAFE_NAME_RE.fullmatch(name):
            parser.error(f"GitHub-unsafe final asset name: {name!r}")

    installers = names - {"SHA256SUMS"}
    debian_version = version.debian.replace("~", ".")
    expected_debs = {
        f"{package}_{debian_version}-1_amd64.deb" for package in PACKAGE_NAMES
    }
    expected_arch = {
        f"{package}-{version.arch}-1-x86_64.pkg.tar.zst"
        for package in PACKAGE_NAMES
    }
    actual_debs = {name for name in installers if name.endswith(".deb")}
    actual_arch = {name for name in installers if name.endswith(".pkg.tar.zst")}
    actual_rpms = {name for name in installers if name.endswith(".x86_64.rpm")}
    if actual_debs != expected_debs:
        parser.error(
            f"DEB asset set mismatch: expected={sorted(expected_debs)} "
            f"actual={sorted(actual_debs)}"
        )
    if actual_arch != expected_arch:
        parser.error(
            f"Arch asset set mismatch: expected={sorted(expected_arch)} "
            f"actual={sorted(actual_arch)}"
        )
    expected_rpm_prefixes = {
        package: f"{package}-{version.rpm_version}-{version.rpm_release}"
        for package in PACKAGE_NAMES
    }
    matched_rpms: set[str] = set()
    for package, prefix in expected_rpm_prefixes.items():
        matches = {
            name
            for name in actual_rpms
            if name.startswith(prefix) and name.endswith(".x86_64.rpm")
        }
        if len(matches) != 1:
            parser.error(
                f"expected exactly one binary RPM for {package} with prefix "
                f"{prefix!r}; found {sorted(matches)}"
            )
        matched_rpms.update(matches)
    if matched_rpms != actual_rpms or any(name.endswith(".src.rpm") for name in names):
        parser.error(f"unexpected RPM assets: {sorted(actual_rpms - matched_rpms)}")
    if installers != actual_debs | actual_arch | actual_rpms:
        parser.error(
            "non-installer assets are forbidden in public Release: "
            f"{sorted(installers - actual_debs - actual_arch - actual_rpms)}"
        )

    checksums = parse_checksums(root / "SHA256SUMS")
    if set(checksums) != installers:
        parser.error(
            "checksum asset set mismatch: missing="
            f"{sorted(installers - set(checksums))} extra="
            f"{sorted(set(checksums) - installers)}"
        )
    for name, expected in checksums.items():
        if sha256(root / name) != expected:
            parser.error(f"checksum index mismatch: {name}")

    print(
        "FINAL_RELEASE_INSTALLERS_OK "
        f"files={len(files)} debs={len(actual_debs)} "
        f"rpms={len(actual_rpms)} arch={len(actual_arch)}"
    )
    for name in sorted(names):
        print(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
