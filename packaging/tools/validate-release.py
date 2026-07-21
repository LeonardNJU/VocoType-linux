#!/usr/bin/env python3
"""Validate VoCoType source, wheel, and sdist release artifacts."""

from __future__ import annotations

import argparse
import json
import re
import tarfile
import zipfile
from pathlib import Path, PurePosixPath
import sys

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))
from release_common import file_sha256
from versioning import ReleaseVersion, normalize_expected_version

HEX_COMMIT = re.compile(r"^[0-9a-f]{40}$")
SOURCE_REQUIRED = (
    "README.md",
    "MANIFEST.in",
    "vocotype_package.py",
    ".github/workflows/release.yml",
    "packaging/tools/stage-system-package.sh",
    "fcitx5/module/vocotype_module.cpp",
    "ibus/scripts/install-gui.sh",
    "settings_center/playground_service.py",
    "data/metainfo/io.github.LeonardNJU.VoCoType.metainfo.xml",
)
SDIST_REQUIRED = (
    "README.md",
    "MANIFEST.in",
    "vocotype_package.py",
    "packaging/tools/stage-system-package.sh",
    "fcitx5/module/vocotype_module.cpp",
    "ibus/scripts/install-gui.sh",
    "settings_center/playground_service.py",
    "tests/test_release_packaging.py",
)
WHEEL_REQUIRED = (
    "app/config.py",
    "ibus/main.py",
    "settings_center/application.py",
    "settings_center/playground_service.py",
    "vocotype_package.py",
    "vocotype_version.py",
    "share/vocotype/terms.yaml",
    "share/vocotype/ibus/vocotype.xml.in",
    "share/metainfo/io.github.LeonardNJU.VoCoType.metainfo.xml",
)
FORBIDDEN_ARCHIVE_PARTS = ("/build/", "/__pycache__/")


def safe_relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"unsafe artifact path: {value!r}")
    return path


def _require_suffixes(names: list[str], required: tuple[str, ...], label: str) -> None:
    for suffix in required:
        if not any(name.endswith(suffix) for name in names):
            raise ValueError(f"{label} is missing {suffix}")
    for forbidden in FORBIDDEN_ARCHIVE_PARTS:
        if any(forbidden in f"/{name}" for name in names):
            raise ValueError(f"{label} contains forbidden path fragment {forbidden}")


def validate_release(
    release_dir: Path,
    *,
    expected_version: str | None = None,
    expected_commit: str | None = None,
) -> dict[str, object]:
    root = release_dir.resolve()
    manifest_path = root / "release-manifest.json"
    sums_path = root / "SHA256SUMS"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1 or manifest.get("project") != "vocotype-linux":
        raise ValueError("unexpected release manifest identity")
    version = str(manifest.get("version", ""))
    commit = str(manifest.get("commit", ""))
    try:
        version = ReleaseVersion.parse(version).python
    except ValueError as exc:
        raise ValueError(f"invalid release version: {version!r}") from exc
    if not HEX_COMMIT.fullmatch(commit):
        raise ValueError(f"invalid commit: {commit!r}")
    if expected_version and normalize_expected_version(expected_version) != version:
        raise ValueError(f"version mismatch: expected {expected_version}, found {version}")
    if expected_commit and expected_commit != commit:
        raise ValueError(f"commit mismatch: expected {expected_commit}, found {commit}")

    rows = manifest.get("artifacts")
    if not isinstance(rows, list):
        raise ValueError("manifest artifacts must be a list")
    manifest_entries: dict[str, dict[str, object]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("manifest artifact entry must be an object")
        relative = safe_relative_path(str(row.get("path", ""))).as_posix()
        if relative in manifest_entries:
            raise ValueError(f"duplicate manifest artifact: {relative}")
        artifact = root / relative
        if not artifact.is_file():
            raise ValueError(f"missing artifact: {relative}")
        actual_size = artifact.stat().st_size
        actual_hash = file_sha256(artifact)
        if row.get("size") != actual_size or row.get("sha256") != actual_hash:
            raise ValueError(f"manifest digest mismatch: {relative}")
        manifest_entries[relative] = row

    checksum_entries: dict[str, str] = {}
    for raw in sums_path.read_text(encoding="utf-8").splitlines():
        digest, separator, value = raw.partition("  ")
        if not separator or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"invalid checksum row: {raw!r}")
        relative = safe_relative_path(value).as_posix()
        if relative in checksum_entries:
            raise ValueError(f"duplicate checksum artifact: {relative}")
        checksum_entries[relative] = digest
    if set(checksum_entries) != set(manifest_entries):
        raise ValueError("checksum and manifest artifact sets differ")
    for relative, digest in checksum_entries.items():
        if file_sha256(root / relative) != digest:
            raise ValueError(f"checksum mismatch: {relative}")

    source_name = f"VocoType-linux-{version}.tar.gz"
    wheel_names = [name for name in manifest_entries if name.endswith(".whl")]
    sdist_names = [
        name
        for name in manifest_entries
        if name.startswith("python/") and name.endswith(".tar.gz")
    ]
    if source_name not in manifest_entries or len(wheel_names) != 1 or len(sdist_names) != 1:
        raise ValueError("release must contain one source archive, one wheel, and one Python sdist")
    python_files = sorted(
        path.relative_to(root).as_posix()
        for path in (root / "python").iterdir()
        if path.is_file()
    )
    if python_files != sorted([wheel_names[0], sdist_names[0]]):
        raise ValueError(f"unexpected Python release files: {python_files}")

    with tarfile.open(root / source_name) as archive:
        source_members = archive.getnames()
    prefix = f"VocoType-linux-{version}/"
    if not source_members or any(
        name != prefix.rstrip("/") and not name.startswith(prefix)
        for name in source_members
    ):
        raise ValueError("source archive has an inconsistent top-level directory")
    _require_suffixes(source_members, SOURCE_REQUIRED, "source archive")

    with zipfile.ZipFile(root / wheel_names[0]) as archive:
        _require_suffixes(archive.namelist(), WHEEL_REQUIRED, "wheel")
    with tarfile.open(root / sdist_names[0]) as archive:
        _require_suffixes(archive.getnames(), SDIST_REQUIRED, "sdist")

    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-dir", type=Path, default=Path("dist/release"))
    parser.add_argument("--expected-version")
    parser.add_argument("--expected-commit")
    args = parser.parse_args()
    manifest = validate_release(
        args.release_dir,
        expected_version=args.expected_version,
        expected_commit=args.expected_commit,
    )
    print(
        f"Validated VoCoType {manifest['version']} release for commit {manifest['commit']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
