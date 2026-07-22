#!/usr/bin/env python3
"""Validate the package-local Python 3.12 runtime wheelhouse."""
from __future__ import annotations

import argparse
import re
import zipfile
from pathlib import Path, PurePosixPath

NORMALIZE = re.compile(r"[-_.]+")
BASE_REQUIRED = {
    "funasr-onnx",
    "jieba",
    "modelscope",
    "numpy",
    "onnxruntime",
    "pyyaml",
    "scipy",
    "sentencepiece",
    "sounddevice",
    "soundfile",
}
IBUS_REQUIRED = {"pygobject", "pycairo"}
COMMON_FORBIDDEN = {
    "torch", "transformers", "socksio", "pyrime", "wcwidth",
    "wetextprocessing", "pynini", "importlib-resources",
}
FCITX_FORBIDDEN = {"pygobject", "pycairo"}
SUPPORTED_FLAVORS = {"universal", "ibus", "fcitx5"}


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
    flavor: str,
    expected_pygobject_version: str | None = None,
) -> list[Path]:
    flavor = str(flavor or "").strip().lower()
    if flavor not in SUPPORTED_FLAVORS:
        raise ValueError(
            "flavor must be universal, ibus, or fcitx5; "
            f"found {flavor!r}"
        )
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
    includes_ibus = flavor in {"universal", "ibus"}
    required = set(BASE_REQUIRED)
    if includes_ibus:
        required.update(IBUS_REQUIRED)
    missing = sorted(required - names)
    if missing:
        raise ValueError(
            f"required wheels missing for flavor={flavor}: " + ", ".join(missing)
        )

    forbidden = set(COMMON_FORBIDDEN)
    if not includes_ibus:
        forbidden.update(FCITX_FORBIDDEN)
    extras = sorted(forbidden & names)
    if extras:
        raise ValueError(
            f"forbidden wheels present for flavor={flavor}: " + ", ".join(extras)
        )

    pygobject = [path for path in files if package_name(path.name) == "pygobject"]
    if includes_ibus:
        if len(pygobject) != 1:
            raise ValueError(
                f"expected exactly one PyGObject wheel for flavor={flavor}, "
                f"found {len(pygobject)}"
            )
        if expected_pygobject_version is not None:
            actual = wheel_version(pygobject[0].name)
            if actual != expected_pygobject_version:
                raise ValueError(
                    f"PyGObject wheel version mismatch: expected "
                    f"{expected_pygobject_version}, found {actual}"
                )
    elif pygobject:
        raise ValueError("PyGObject must not be bundled in the Fcitx-only runtime")
    return files


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheelhouse", type=Path)
    parser.add_argument(
        "--flavor", required=True, choices=sorted(SUPPORTED_FLAVORS)
    )
    parser.add_argument("--expected-pygobject-version")
    args = parser.parse_args()
    try:
        files = validate_wheelhouse(
            args.wheelhouse,
            flavor=args.flavor,
            expected_pygobject_version=args.expected_pygobject_version,
        )
    except ValueError as exc:
        parser.error(str(exc))
    print(
        f"Runtime wheelhouse audit passed: flavor={args.flavor} "
        f"wheels={len(files)} root={args.wheelhouse.resolve()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
