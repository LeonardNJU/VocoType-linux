#!/usr/bin/env python3
"""Build the reproducible VoCoType source release archive."""

from __future__ import annotations

import argparse
import gzip
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))
from release_common import file_sha256
from versioning import ReleaseVersion, normalize_expected_version

ROOT = Path(__file__).resolve().parents[2]


def read_version() -> str:
    namespace: dict[str, object] = {}
    exec((ROOT / "vocotype_version.py").read_text(encoding="utf-8"), namespace)
    version = str(namespace["__version__"])
    try:
        return ReleaseVersion.parse(version).python
    except ValueError as exc:
        raise RuntimeError(f"invalid version: {version!r}") from exc


def run(*argv: str, cwd: Path = ROOT) -> None:
    subprocess.run(argv, cwd=cwd, check=True)


def git_output(*argv: str) -> str:
    return subprocess.check_output(["git", *argv], cwd=ROOT, text=True).strip()


def build_source_archive(output_dir: Path, version: str, treeish: str) -> Path:
    prefix = f"VocoType-linux-{version}/"
    target = output_dir / f"VocoType-linux-{version}.tar.gz"
    with tempfile.NamedTemporaryFile(suffix=".tar", delete=False) as handle:
        tar_path = Path(handle.name)
    try:
        with tar_path.open("wb") as handle:
            subprocess.run(
                ["git", "archive", "--format=tar", f"--prefix={prefix}", treeish],
                cwd=ROOT,
                stdout=handle,
                check=True,
            )
        with tar_path.open("rb") as source, target.open("wb") as raw_target:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw_target, mtime=0) as compressed:
                shutil.copyfileobj(source, compressed)
    finally:
        tar_path.unlink(missing_ok=True)
    return target



def clean_generated_source_metadata() -> None:
    """Remove generated checkout metadata before source release validation."""
    for path in ROOT.glob("*.egg-info"):
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    for path in ROOT.rglob("__pycache__"):
        if path.is_dir():
            shutil.rmtree(path)


def write_metadata(output_dir: Path, version: str, commit: str, artifacts: list[Path]) -> None:
    relative = [path.relative_to(output_dir) for path in sorted(artifacts)]
    checksums = output_dir / "SHA256SUMS"
    checksums.write_text(
        "".join(f"{file_sha256(output_dir / path)}  {path.as_posix()}\n" for path in relative),
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "project": "vocotype-linux",
        "version": version,
        "commit": commit,
        "artifacts": [
            {
                "path": path.as_posix(),
                "size": (output_dir / path).stat().st_size,
                "sha256": file_sha256(output_dir / path),
            }
            for path in relative
        ],
    }
    (output_dir / "release-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "dist/release")
    parser.add_argument("--treeish", default="HEAD")
    parser.add_argument("--source-only", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--expected-version")
    parser.add_argument("--keep-output", action="store_true")
    parser.add_argument("--allow-dirty", action="store_true")
    args = parser.parse_args()

    version = read_version()
    if args.expected_version:
        try:
            expected = normalize_expected_version(args.expected_version)
        except ValueError as exc:
            parser.error(str(exc))
        if expected != version:
            parser.error(
                f"expected version {expected}, "
                f"but vocotype_version.py contains {version}"
            )

    if not args.allow_dirty and args.treeish == "HEAD":
        dirty = git_output("status", "--porcelain", "--untracked-files=normal")
        if dirty:
            parser.error(
                "refusing to build release assets from a dirty working tree; "
                "commit the changes or pass --allow-dirty for a local preview"
            )

    output_dir = args.output.resolve()
    if not args.keep_output:
        shutil.rmtree(output_dir, ignore_errors=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    commit = git_output("rev-parse", args.treeish)
    source_archive = build_source_archive(output_dir, version, args.treeish)
    write_metadata(output_dir, version, commit, [source_archive])
    print(f"Built VoCoType {version} release artifacts in {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
