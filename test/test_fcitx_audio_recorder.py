from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "fcitx5" / "backend" / "audio_recorder.py"
)
SPEC = importlib.util.spec_from_file_location("vocotype_fcitx_audio_recorder", MODULE_PATH)
assert SPEC and SPEC.loader
audio_recorder = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = audio_recorder
SPEC.loader.exec_module(audio_recorder)


def test_explicit_44100_sample_rate_is_honoured():
    assert audio_recorder.resolve_requested_sample_rate(44100, 16000) == 44100


def test_configured_sample_rate_is_used_when_cli_is_absent():
    assert audio_recorder.resolve_requested_sample_rate(None, 48000) == 48000


def test_16khz_is_used_when_neither_cli_nor_config_is_present():
    assert (
        audio_recorder.resolve_requested_sample_rate(None, None)
        == audio_recorder.SAMPLE_RATE
        == 16000
    )
