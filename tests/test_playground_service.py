from __future__ import annotations

import json
import socket
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile

from settings_center import playground_service


def test_record_audio_is_fixed_to_five_seconds_and_writes_private_wav(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    calls: dict[str, object] = {}
    frames = np.linspace(-0.5, 0.5, 80_000, dtype=np.float32).reshape(-1, 1)

    fake_sd = SimpleNamespace(
        rec=lambda count, **kwargs: (
            calls.update({"count": count, **kwargs}) or frames.copy()
        ),
        wait=lambda: calls.update({"waited": True}),
    )
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

    def fake_write(path, samples, sample_rate, *, subtype):
        calls["write"] = (Path(path), np.asarray(samples).copy(), sample_rate, subtype)
        Path(path).write_bytes(b"fake-wav")

    monkeypatch.setattr(soundfile, "write", fake_write)
    output = tmp_path / "last.wav"
    recording = playground_service.record_audio(
        device_id=7,
        device_name="USB microphone",
        sample_rate=16_000,
        output_path=output,
    )

    assert calls["count"] == 80_000
    assert calls["samplerate"] == 16_000
    assert calls["channels"] == 1
    assert calls["dtype"] == "float32"
    assert calls["device"] == 7
    assert calls["waited"] is True
    assert recording.duration_seconds == pytest.approx(5.0)
    assert recording.path == output
    assert output.read_bytes() == b"fake-wav"
    assert output.stat().st_mode & 0o777 == 0o600


def test_play_recording_uses_default_output_device(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    source = tmp_path / "recording.wav"
    source.write_bytes(b"wav")
    calls: dict[str, object] = {}
    waveform = np.zeros(40_000, dtype=np.float32)
    fake_sd = SimpleNamespace(
        play=lambda samples, **kwargs: calls.update(
            {"samples": np.asarray(samples), "kwargs": kwargs}
        ),
        wait=lambda: calls.update({"waited": True}),
    )
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    monkeypatch.setattr(
        soundfile,
        "read",
        lambda *_args, **_kwargs: (waveform, 16_000),
    )

    duration = playground_service.play_recording(source)

    assert duration == pytest.approx(2.5)
    assert calls["kwargs"] == {"samplerate": 16_000}
    assert "device" not in calls["kwargs"]
    assert calls["waited"] is True


def test_transcribe_recording_uses_existing_backend_socket(tmp_path: Path):
    recording = tmp_path / "recording.wav"
    recording.write_bytes(b"wav")
    socket_path = tmp_path / "backend.sock"
    captured: dict[str, object] = {}
    ready = threading.Event()

    def server() -> None:
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(socket_path))
        listener.listen(1)
        ready.set()
        connection, _ = listener.accept()
        chunks = []
        while True:
            chunk = connection.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
        captured.update(json.loads(b"".join(chunks).decode("utf-8")))
        connection.sendall(
            json.dumps({"success": True, "text": "测试转录"}, ensure_ascii=False).encode(
                "utf-8"
            )
        )
        connection.close()
        listener.close()

    thread = threading.Thread(target=server)
    thread.start()
    assert ready.wait(2)
    result = playground_service.transcribe_recording(
        recording,
        socket_path=socket_path,
        timeout_seconds=2,
    )
    thread.join(timeout=2)

    assert result["text"] == "测试转录"
    assert captured == {
        "type": "transcribe",
        "audio_path": str(recording),
        "long_mode": False,
    }


def test_slm_playground_stays_disabled_until_current_endpoint_is_live():
    config = {
        "enabled": False,
        "provider": "remote",
        "endpoint": "https://example.test/v1/chat/completions",
        "model": "example-model",
        "api_key": "secret",
    }
    fingerprint = playground_service.slm_config_fingerprint({**config, "enabled": True})

    ready, reason = playground_service.slm_playground_gate(
        config,
        verified_fingerprint=fingerprint,
    )
    assert ready is False
    assert "打开 AI 润色" in reason

    enabled = {**config, "enabled": True}
    ready, reason = playground_service.slm_playground_gate(
        enabled,
        verified_fingerprint=None,
    )
    assert ready is False
    assert "AI 端点测活" in reason

    ready, reason = playground_service.slm_playground_gate(
        enabled,
        verified_fingerprint=fingerprint,
    )
    assert ready is True
    assert "可以试用" in reason

    changed = {**enabled, "endpoint": "https://other.test/v1/chat/completions"}
    ready, reason = playground_service.slm_playground_gate(
        changed,
        verified_fingerprint=fingerprint,
    )
    assert ready is False
    assert "AI 端点测活" in reason


def test_remote_slm_gate_requires_endpoint_and_model_before_health_check():
    base = {
        "enabled": True,
        "provider": "remote",
        "endpoint": "",
        "model": "example-model",
    }
    ready, reason = playground_service.slm_playground_gate(
        base,
        verified_fingerprint=None,
    )
    assert ready is False
    assert "AI 端点" in reason

    ready, reason = playground_service.slm_playground_gate(
        {**base, "endpoint": "https://example.test", "model": ""},
        verified_fingerprint=None,
    )
    assert ready is False
    assert "模型名称" in reason


def test_slm_fingerprint_does_not_embed_direct_api_key():
    first = playground_service.slm_config_fingerprint(
        {
            "enabled": True,
            "provider": "remote",
            "endpoint": "https://example.test",
            "model": "m",
            "api_key": "first-secret",
        }
    )
    second = playground_service.slm_config_fingerprint(
        {
            "enabled": True,
            "provider": "remote",
            "endpoint": "https://example.test",
            "model": "m",
            "api_key": "second-secret",
        }
    )
    assert first != second
    assert "first-secret" not in first
    assert "second-secret" not in second
