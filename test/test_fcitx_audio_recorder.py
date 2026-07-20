from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import numpy as np


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


def test_pending_callback_frames_are_preserved_after_stream_stop():
    recorder = audio_recorder.AudioRecorder(device=None, sample_rate=16000)
    first = np.array([[1], [2]], dtype=np.int16)
    second = np.array([[3], [4]], dtype=np.int16)
    recorder.audio_queue.put(first)
    recorder.audio_queue.put(second)

    assert recorder._drain_pending_frames() == 2
    assert len(recorder.audio_frames) == 2
    assert np.array_equal(recorder.audio_frames[0], first)
    assert np.array_equal(recorder.audio_frames[1], second)
    assert recorder.audio_queue.empty()
