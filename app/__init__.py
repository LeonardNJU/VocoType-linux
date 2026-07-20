"""Core runtime package for VoCoType Linux.

Heavy audio/ASR modules are imported lazily so the graphical setup assistant can
start on a fresh system before runtime dependencies and models are installed.
"""

from __future__ import annotations

from importlib import import_module

from vocotype_version import __version__

__all__ = [
    "DEFAULT_CONFIG",
    "ensure_logging_dir",
    "load_config",
    "AudioCapture",
    "TranscriptionWorker",
    "TranscriptionResult",
    "__version__",
]


def __getattr__(name: str):
    if name in {"DEFAULT_CONFIG", "ensure_logging_dir", "load_config"}:
        module = import_module(".config", __name__)
        return getattr(module, name)
    if name == "AudioCapture":
        return import_module(".audio_capture", __name__).AudioCapture
    if name in {"TranscriptionWorker", "TranscriptionResult"}:
        module = import_module(".transcribe", __name__)
        return getattr(module, name)
    raise AttributeError(name)
