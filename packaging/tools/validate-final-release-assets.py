#!/usr/bin/env python3
"""Validate the exact flattened asset set before GitHub Release publication."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+\-]*$")
CHECKSUM_RE = re.compile(r"^([0-9a-f]{64})  (?:\./)?(.+)$")


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
    parser.add_argument("--tag", required=True)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    if not root.is_dir():
        parser.error(f"asset root does not exist: {root}")

    files = sorted(path for path in root.iterdir() if path.is_file())
    if any(path.is_dir() for path in root.iterdir()):
        parser.error("final Release assets must be flat")
    names = {path.name for path in files}
    if "release-assets.json" not in names or "SHA256SUMS.all" not in names:
        parser.error("final manifest or checksum index is missing")
    for name in names:
        if "~" in name or not SAFE_NAME_RE.fullmatch(name):
            parser.error(f"GitHub-unsafe final asset name: {name!r}")

    manifest = json.loads((root / "release-assets.json").read_text(encoding="utf-8"))
    if manifest.get("tag") != args.tag:
        parser.error(f"manifest tag mismatch: {manifest.get('tag')!r}")
    if manifest.get("commit") != args.commit:
        parser.error(f"manifest commit mismatch: {manifest.get('commit')!r}")
    entries = manifest.get("assets")
    if not isinstance(entries, list):
        parser.error("manifest assets must be a list")
    manifest_names: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            parser.error("manifest asset entry must be an object")
        name = entry.get("path")
        if not isinstance(name, str) or name != Path(name).name:
            parser.error(f"invalid manifest asset path: {name!r}")
        if name in manifest_names:
            parser.error(f"duplicate manifest asset: {name}")
        manifest_names.add(name)
        path = root / name
        if not path.is_file():
            parser.error(f"manifest asset is missing: {name}")
        if entry.get("size") != path.stat().st_size:
            parser.error(f"manifest size mismatch: {name}")
        if entry.get("sha256") != sha256(path):
            parser.error(f"manifest checksum mismatch: {name}")
    expected_manifest_names = names - {"release-assets.json", "SHA256SUMS.all"}
    if manifest_names != expected_manifest_names:
        parser.error(
            "manifest asset set mismatch: missing="
            f"{sorted(expected_manifest_names - manifest_names)} extra="
            f"{sorted(manifest_names - expected_manifest_names)}"
        )

    checksums = parse_checksums(root / "SHA256SUMS.all")
    expected_checksum_names = names - {"SHA256SUMS.all"}
    if set(checksums) != expected_checksum_names:
        parser.error(
            "checksum asset set mismatch: missing="
            f"{sorted(expected_checksum_names - set(checksums))} extra="
            f"{sorted(set(checksums) - expected_checksum_names)}"
        )
    for name, expected in checksums.items():
        if sha256(root / name) != expected:
            parser.error(f"checksum index mismatch: {name}")

    debs = [name for name in names if name.endswith(".deb")]
    rpms = [name for name in names if name.endswith(".x86_64.rpm")]
    arch = [name for name in names if name.endswith(".pkg.tar.zst")]
    if len(debs) != 3 or len(rpms) != 3 or len(arch) != 3:
        parser.error(
            f"expected 3 DEB, 3 binary RPM, and 3 Arch assets; "
            f"found {len(debs)}, {len(rpms)}, {len(arch)}"
        )
    required_markers = (
        "vocotype-native-streaming-linux-x86_64.tar.gz",
        "VocoType-linux-",
        "vocotype_linux-",
    )
    for marker in required_markers:
        if marker.endswith(".gz"):
            present = marker in names
        else:
            present = any(name.startswith(marker) for name in names)
        if not present:
            parser.error(f"required Release asset family missing: {marker}")

    print(
        f"FINAL_RELEASE_ASSETS_OK files={len(files)} manifest_assets={len(entries)} "
        f"debs={len(debs)} rpms={len(rpms)} arch={len(arch)}"
    )
    for name in sorted(names):
        print(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
