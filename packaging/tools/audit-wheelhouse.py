#!/usr/bin/env python3
"""Validate the package-local Python 3.12 runtime wheelhouse."""
from __future__ import annotations

import argparse
import re
from pathlib import Path

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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheelhouse", type=Path)
    args = parser.parse_args()
    root = args.wheelhouse.resolve()
    if not root.is_dir():
        parser.error(f"wheelhouse does not exist: {root}")
    files = sorted(path for path in root.iterdir() if path.is_file())
    non_wheels = [path.name for path in files if path.suffix != ".whl"]
    if non_wheels:
        parser.error("non-wheel files present: " + ", ".join(non_wheels))
    names = {package_name(path.name) for path in files}
    missing = sorted(REQUIRED - names)
    if missing:
        parser.error("required wheels missing: " + ", ".join(missing))
    extras = sorted(FORBIDDEN & names)
    if extras:
        parser.error("optional backend wheels leaked into core package: " + ", ".join(extras))
    print(f"Core runtime wheelhouse audit passed: {len(files)} wheels in {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
