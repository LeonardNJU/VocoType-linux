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


def test_preview_client_uses_backend_session_protocol(monkeypatch):
    calls = []

    def fake_request(payload, **_kwargs):
        calls.append(payload)
        if payload["type"] == "asr_preview_start":
            return {
                "success": True,
                "session_id": "preview-session",
                "chunk_samples": 9600,
            }
        if payload["type"] == "asr_preview_feed":
            return {"success": True, "text": "实时文本"}
        return {"success": True}

    monkeypatch.setattr(audio_recorder, "_backend_request", fake_request)
    client = audio_recorder.BackendPreviewClient.start()
    assert client is not None
    assert client.chunk_samples == 9600
    assert client.feed(np.array([1, 2], dtype=np.int16)) == "实时文本"
    client.close()

    assert [item["type"] for item in calls] == [
        "asr_preview_start",
        "asr_preview_feed",
        "asr_preview_close",
    ]
    assert calls[1]["session_id"] == "preview-session"
    assert calls[2]["flush"] is False


def test_preview_unavailable_falls_back_without_recording_failure(monkeypatch):
    monkeypatch.setattr(
        audio_recorder,
        "_backend_request",
        lambda _payload, **_kwargs: {
            "success": False,
            "error": "streaming_disabled",
        },
    )
    assert audio_recorder.BackendPreviewClient.start() is None


def test_recorder_protocol_emits_replaceable_partial_and_terminal_path(capsys):
    audio_recorder._emit_protocol_event("partial", text="当前假设")
    audio_recorder._emit_protocol_event("audio", path="/tmp/final.wav")
    lines = capsys.readouterr().out.strip().splitlines()
    import json

    assert json.loads(lines[0]) == {"type": "partial", "text": "当前假设"}
    assert json.loads(lines[1]) == {"type": "audio", "path": "/tmp/final.wav"}


def test_recorder_release_never_waits_for_online_final_flush():
    source = MODULE_PATH.read_text(encoding="utf-8")
    record_body = source.split("    def record", 1)[1].split("def main", 1)[0]
    assert "preview_thread.join(timeout=0.15)" in record_body
    assert "preview_client.feed(tail, is_final=True)" not in record_body
    assert "最终离线识别" in record_body
