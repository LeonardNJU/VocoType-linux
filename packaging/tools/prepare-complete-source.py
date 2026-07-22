#!/usr/bin/env python3
"""Inject CI-built native and Python runtimes into a package source archive."""
from __future__ import annotations

import argparse
import gzip
import os
import shutil
import tarfile
import tempfile
from pathlib import Path


def _safe_extract(archive: Path, destination: Path) -> Path:
    destination = destination.resolve()
    with tarfile.open(archive, "r:gz") as handle:
        members = handle.getmembers()
        for member in members:
            target = (destination / member.name).resolve()
            if destination != target and destination not in target.parents:
                raise ValueError(f"unsafe source archive member: {member.name}")
            if member.isdev() or member.isfifo():
                raise ValueError(f"unsupported source archive member: {member.name}")
        handle.extractall(destination, members=members)
    roots = [entry for entry in destination.iterdir() if entry.is_dir()]
    if len(roots) != 1:
        raise ValueError("source archive must contain exactly one top-level directory")
    return roots[0]


def _validate_bundle(bundle: Path) -> None:
    required = (
        bundle / "bin/vocotype-streaming-worker",
        bundle / "lib/libfunasr.so",
        bundle / "share/licenses/onnxruntime/LICENSE",
        bundle / "share/licenses/funasr/LICENSE",
    )
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise ValueError("native bundle is incomplete: " + ", ".join(missing))
    if not os.access(bundle / "bin/vocotype-streaming-worker", os.X_OK):
        raise ValueError("native streaming worker is not executable")


def _validate_wheelhouse(wheelhouse: Path) -> None:
    import subprocess
    import sys

    audit = Path(__file__).resolve().with_name("audit-wheelhouse.py")
    result = subprocess.run(
        [sys.executable, str(audit), str(wheelhouse)],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError(result.stderr.strip() or result.stdout.strip())


def _write_reproducible_archive(source_root: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".tar", delete=False) as raw:
        raw_path = Path(raw.name)
    try:
        with tarfile.open(raw_path, "w", format=tarfile.PAX_FORMAT) as archive:
            paths = [source_root, *sorted(source_root.rglob("*"))]
            for path in paths:
                arcname = path.relative_to(source_root.parent).as_posix()
                info = archive.gettarinfo(str(path), arcname=arcname)
                info.uid = 0
                info.gid = 0
                info.uname = "root"
                info.gname = "root"
                info.mtime = 0
                if path.is_file():
                    with path.open("rb") as handle:
                        archive.addfile(info, handle)
                else:
                    archive.addfile(info)
        with raw_path.open("rb") as source, output.open("wb") as target:
            with gzip.GzipFile(filename="", mode="wb", fileobj=target, mtime=0) as compressed:
                shutil.copyfileobj(source, compressed)
    finally:
        raw_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--native-bundle", type=Path, required=True)
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    source = args.source.resolve()
    bundle = args.native_bundle.resolve()
    wheelhouse = args.wheelhouse.resolve()
    _validate_bundle(bundle)
    _validate_wheelhouse(wheelhouse)

    with tempfile.TemporaryDirectory(prefix="vocotype-complete-source-") as value:
        root = _safe_extract(source, Path(value))
        bundle_target = root / "native/streaming_worker/build/bundle"
        wheel_target = root / "vendor/wheelhouse"
        shutil.rmtree(bundle_target, ignore_errors=True)
        shutil.rmtree(wheel_target, ignore_errors=True)
        shutil.copytree(bundle, bundle_target, symlinks=True)
        shutil.copytree(wheelhouse, wheel_target, symlinks=True)
        _write_reproducible_archive(root, args.output.resolve())
    print(args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
