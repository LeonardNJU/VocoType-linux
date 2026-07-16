#!/usr/bin/env python3
"""Verify that a Python interpreter can load the VoCoType ASR runtime."""

import importlib
import sys


REQUIRED_IMPORTS = (
    "numpy",
    "sounddevice",
    "soundfile",
    "librosa",
    "modelscope.hub.snapshot_download",
    "funasr_onnx.paraformer_bin",
    "funasr_onnx.vad_bin",
    "funasr_onnx.punc_bin",
)


def check_runtime(importer=None, stderr=None):
    importer = importer or importlib.import_module
    stderr = stderr or sys.stderr

    for module_name in REQUIRED_IMPORTS:
        try:
            importer(module_name)
        except Exception as exc:
            print(
                f"VoCoType runtime import failed at {module_name}: "
                f"{type(exc).__name__}: {exc}",
                file=stderr,
            )
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(check_runtime())
