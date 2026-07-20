#!/usr/bin/env python3
"""Build reproducible VoCoType source and Python release artifacts."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read_version() -> str:
    namespace: dict[str, object] = {}
    exec((ROOT / "vocotype_version.py").read_text(encoding="utf-8"), namespace)
    version = str(namespace["__version__"])
    if not version or any(part == "" for part in version.split(".")):
        raise RuntimeError(f"invalid version: {version!r}")
    return version


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
    """Remove build metadata that PEP 517 backends may leave in the checkout."""

    for path in ROOT.glob("*.egg-info"):
        shutil.rmtree(path, ignore_errors=True)
    for path in ROOT.rglob("__pycache__"):
        if any(part in {".git", ".venv", "build", "dist"} for part in path.parts):
            continue
        shutil.rmtree(path, ignore_errors=True)
    for pattern in ("*.pyc", "*.pyo"):
        for path in ROOT.rglob(pattern):
            if any(part in {".git", ".venv", "build", "dist"} for part in path.parts):
                continue
            path.unlink(missing_ok=True)


def build_python_distributions(output_dir: Path) -> list[Path]:
    python_dir = output_dir / "python"
    shutil.rmtree(python_dir, ignore_errors=True)
    python_dir.mkdir(parents=True)
    uv = shutil.which("uv")
    clean_generated_source_metadata()
    try:
        if uv:
            run(uv, "build", "--out-dir", str(python_dir), str(ROOT))
        else:
            try:
                import build  # noqa: F401
            except ImportError as exc:
                raise RuntimeError("install 'uv' or the Python 'build' package") from exc
            run(sys.executable, "-m", "build", "--outdir", str(python_dir), str(ROOT))
    finally:
        clean_generated_source_metadata()
    artifacts = sorted(
        path
        for path in python_dir.iterdir()
        if path.is_file() and (path.suffix == ".whl" or path.name.endswith(".tar.gz"))
    )
    for path in python_dir.iterdir():
        if path.is_file() and path not in artifacts:
            path.unlink()
    if not any(path.suffix == ".whl" for path in artifacts):
        raise RuntimeError("wheel build produced no artifact")
    if not any(path.name.endswith(".tar.gz") for path in artifacts):
        raise RuntimeError("sdist build produced no artifact")
    return artifacts


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_metadata(output_dir: Path, version: str, commit: str, artifacts: list[Path]) -> None:
    relative = [path.relative_to(output_dir) for path in sorted(artifacts)]
    checksums = output_dir / "SHA256SUMS"
    checksums.write_text(
        "".join(f"{sha256(output_dir / path)}  {path.as_posix()}\n" for path in relative),
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
                "sha256": sha256(output_dir / path),
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
    parser.add_argument("--source-only", action="store_true")
    parser.add_argument("--expected-version")
    parser.add_argument("--keep-output", action="store_true")
    parser.add_argument("--allow-dirty", action="store_true")
    args = parser.parse_args()

    version = read_version()
    if args.expected_version and args.expected_version.lstrip("v") != version:
        parser.error(
            f"expected version {args.expected_version.lstrip('v')}, "
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
    artifacts = [source_archive]
    if not args.source_only:
        artifacts.extend(build_python_distributions(output_dir))
    write_metadata(output_dir, version, commit, artifacts)
    print(f"Built VoCoType {version} release artifacts in {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
