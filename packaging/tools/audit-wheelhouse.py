#!/usr/bin/env python3
"""Validate the package-local Python 3.12 runtime wheelhouse."""
from __future__ import annotations

import argparse
import re
import zipfile
from pathlib import Path, PurePosixPath

NORMALIZE = re.compile(r"[-_.]+")
REQUIRED = {
    "funasr-onnx",
    "jieba",
    "modelscope",
    "numpy",
    "onnxruntime",
    "pygobject",
    "pyyaml",
    "scipy",
    "sentencepiece",
    "sounddevice",
    "soundfile",
    "wetextprocessing",
}
FORBIDDEN = {"torch", "transformers", "socksio", "pyrime"}


def package_name(filename: str) -> str:
    return NORMALIZE.sub("-", filename.split("-", 1)[0]).lower()


def wheel_version(filename: str) -> str:
    parts = filename.removesuffix(".whl").split("-")
    if len(parts) < 5:
        raise ValueError(f"invalid wheel filename: {filename}")
    return parts[1]


def _validate_member_name(value: str, wheel: Path) -> None:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"unsafe wheel member in {wheel.name}: {value!r}")


def validate_wheel(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if not names:
                raise ValueError(f"empty wheel archive: {path.name}")
            for name in names:
                _validate_member_name(name, path)
            bad_member = archive.testzip()
            if bad_member is not None:
                raise ValueError(
                    f"wheel CRC failure in {path.name}: {bad_member}"
                )
            parts = path.name.removesuffix(".whl").split("-")
            if len(parts) < 5:
                raise ValueError(f"invalid wheel filename: {path.name}")
            distribution = NORMALIZE.sub("_", parts[0])
            version = parts[1]
            dist_info = f"{distribution}-{version}.dist-info/".casefold()
            folded = {name.casefold() for name in names}
            for member in ("METADATA", "WHEEL", "RECORD"):
                expected = dist_info + member.casefold()
                if expected not in folded:
                    raise ValueError(
                        f"wheel {path.name} is missing top-level "
                        f"{distribution}-{version}.dist-info/{member}"
                    )
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError) as exc:
        raise ValueError(f"invalid wheel archive {path.name}: {exc}") from exc


def validate_wheelhouse(
    root: Path,
    *,
    expected_pygobject_version: str | None = None,
) -> list[Path]:
    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"wheelhouse does not exist: {root}")
    files = sorted(path for path in root.iterdir() if path.is_file())
    non_wheels = [path.name for path in files if path.suffix != ".whl"]
    if non_wheels:
        raise ValueError("non-wheel files present: " + ", ".join(non_wheels))
    if not files:
        raise ValueError("wheelhouse contains no wheels")
    for path in files:
        validate_wheel(path)
    names = {package_name(path.name) for path in files}
    missing = sorted(REQUIRED - names)
    if missing:
        raise ValueError("required wheels missing: " + ", ".join(missing))
    extras = sorted(FORBIDDEN & names)
    if extras:
        raise ValueError(
            "optional backend wheels leaked into core package: " + ", ".join(extras)
        )
    pygobject = [path for path in files if package_name(path.name) == "pygobject"]
    if len(pygobject) != 1:
        raise ValueError(f"expected exactly one PyGObject wheel, found {len(pygobject)}")
    if expected_pygobject_version is not None:
        actual = wheel_version(pygobject[0].name)
        if actual != expected_pygobject_version:
            raise ValueError(
                f"PyGObject wheel version mismatch: expected "
                f"{expected_pygobject_version}, found {actual}"
            )
    return files


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheelhouse", type=Path)
    parser.add_argument("--expected-pygobject-version")
    args = parser.parse_args()
    try:
        files = validate_wheelhouse(
            args.wheelhouse,
            expected_pygobject_version=args.expected_pygobject_version,
        )
    except ValueError as exc:
        parser.error(str(exc))
    print(
        f"Core runtime wheelhouse audit passed: {len(files)} wheels in "
        f"{args.wheelhouse.resolve()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
