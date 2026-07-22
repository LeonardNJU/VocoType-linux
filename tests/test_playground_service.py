from __future__ import annotations

import json
import os
import subprocess
import socket
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile

from settings_center import playground_service


def test_record_audio_is_fixed_to_three_seconds_streams_waveform_and_writes_private_wav(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    calls: dict[str, object] = {}
    waveform_updates: list[tuple[tuple[float, float], ...]] = []

    class FakeInputStream:
        def __init__(self, **kwargs):
            calls.update(kwargs)
            self.position = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            calls["closed"] = True

        def read(self, count):
            start = self.position
            self.position += count
            values = np.linspace(-0.5, 0.5, 48_000, dtype=np.float32)[
                start : self.position
            ]
            return values.reshape(-1, 1), False

    monkeypatch.setitem(
        sys.modules,
        "sounddevice",
        SimpleNamespace(InputStream=FakeInputStream),
    )

    def fake_write(path, samples, sample_rate, *, subtype):
        calls["write"] = (Path(path), np.asarray(samples).copy(), sample_rate, subtype)
        Path(path).write_bytes(b"fake-wav")

    monkeypatch.setattr(soundfile, "write", fake_write)
    output = tmp_path / "last.wav"
    recording = playground_service._record_audio_direct(
        device_id=7,
        device_name="USB microphone",
        sample_rate=16_000,
        output_path=output,
        waveform_callback=waveform_updates.append,
    )

    assert calls["samplerate"] == 16_000
    assert calls["channels"] == 1
    assert calls["dtype"] == "float32"
    assert calls["device"] == 7
    assert calls["closed"] is True
    assert recording.frame_count == 48_000
    assert recording.duration_seconds == pytest.approx(3.0)
    assert recording.path == output
    assert waveform_updates
    assert all(-1.0 <= low <= high <= 1.0 for update in waveform_updates for low, high in update)
    assert output.read_bytes() == b"fake-wav"
    assert output.stat().st_mode & 0o777 == 0o600


def test_play_recording_uses_selected_portaudio_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    source = tmp_path / "recording.wav"
    source.write_bytes(b"wav")
    calls: dict[str, object] = {}
    waveform = np.linspace(-0.02, 0.02, 40_000, dtype=np.float32)
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
    monkeypatch.setattr(
        soundfile,
        "info",
        lambda *_args, **_kwargs: SimpleNamespace(frames=40_000, samplerate=16_000),
    )
    output = playground_service.OutputDevice(
        device_id="6",
        name="USB headphones",
        backend="portaudio",
    )

    result = playground_service._play_recording_direct(source, output_device=output)

    assert result.duration_seconds == pytest.approx(2.5)
    assert result.backend == "PortAudio"
    assert result.output_name == "USB headphones"
    assert result.gain_db == pytest.approx(20.0 * np.log10(20.0))
    assert float(np.max(np.abs(calls["samples"]))) == pytest.approx(0.4)
    assert calls["kwargs"] == {"samplerate": 16_000, "device": 6}
    assert calls["waited"] is True


def test_play_recording_targets_selected_pipewire_sink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    source = tmp_path / "recording.wav"
    source.write_bytes(b"wav")
    calls: list[list[str]] = []
    monkeypatch.setattr(
        soundfile,
        "read",
        lambda *_args, **_kwargs: (
            np.linspace(-0.1, 0.1, 48_000, dtype=np.float32),
            48_000,
        ),
    )
    monkeypatch.setattr(
        playground_service,
        "playground_cache_dir",
        lambda: tmp_path,
    )
    monkeypatch.setattr(
        playground_service.shutil,
        "which",
        lambda command: "/usr/bin/pw-play" if command == "pw-play" else None,
    )
    monkeypatch.setattr(
        playground_service.subprocess,
        "run",
        lambda argv, **_kwargs: calls.append(list(argv)) or SimpleNamespace(returncode=0),
    )
    output = playground_service.OutputDevice(
        device_id="alsa_output.usb-speakers",
        name="USB speakers",
        backend="pipewire",
    )

    result = playground_service._play_recording_direct(source, output_device=output)

    assert len(calls) == 1
    assert calls[0][:3] == [
        "/usr/bin/pw-play",
        "--target",
        "alsa_output.usb-speakers",
    ]
    playback_path = Path(calls[0][3])
    assert playback_path != source
    assert not playback_path.exists()
    assert result.backend == "PipeWire"
    assert result.output_name == "USB speakers"
    assert result.gain_db == pytest.approx(20.0 * np.log10(7.2), rel=1e-3)


def test_prepare_playback_waveform_caps_gain_and_preserves_source():
    source = np.array([-0.01, 0.0, 0.01], dtype=np.float32)
    original = source.copy()

    amplified, gain = playground_service.prepare_playback_waveform(source)

    assert gain == pytest.approx(20.0)
    assert amplified.tolist() == pytest.approx([-0.2, 0.0, 0.2])
    assert source.tolist() == pytest.approx(original.tolist())


def test_prepare_playback_waveform_rejects_silence():
    with pytest.raises(ValueError, match="没有可听信号"):
        playground_service.prepare_playback_waveform(
            np.zeros(100, dtype=np.float32)
        )


def test_waveform_envelope_preserves_minimum_and_maximum():
    envelope = playground_service.waveform_envelope(
        np.array([-0.8, -0.2, 0.1, 0.7], dtype=np.float32),
        columns=2,
    )
    assert envelope[0] == pytest.approx((-0.8, -0.2))
    assert envelope[1] == pytest.approx((0.1, 0.7))


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
    assert "打开 AI 功能" in reason

    enabled = {**config, "enabled": True}
    ready, reason = playground_service.slm_playground_gate(
        enabled,
        verified_fingerprint=None,
    )
    assert ready is False
    assert "API 端点测活" in reason

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
    assert "API 端点测活" in reason


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
    assert "API 端点" in reason

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


def test_list_output_devices_marks_pipewire_default(monkeypatch: pytest.MonkeyPatch):
    responses = {
        ("/usr/bin/pactl", "get-default-sink"): SimpleNamespace(
            stdout="sink.hdmi\n"
        ),
        ("/usr/bin/pactl", "--format=json", "list", "sinks"): SimpleNamespace(
            stdout=json.dumps(
                [
                    {"name": "sink.speakers", "description": "USB Speakers"},
                    {"name": "sink.hdmi", "description": "HDMI Output"},
                ]
            )
        ),
    }
    monkeypatch.setattr(
        playground_service.shutil,
        "which",
        lambda command: "/usr/bin/pactl" if command == "pactl" else None,
    )
    monkeypatch.setattr(
        playground_service.subprocess,
        "run",
        lambda argv, **_kwargs: responses[tuple(argv)],
    )

    outputs = playground_service.list_output_devices()

    assert [item.name for item in outputs] == ["HDMI Output", "USB Speakers"]
    assert outputs[0].is_default is True
    assert outputs[0].device_id == "sink.hdmi"
    assert all(item.backend == "pipewire" for item in outputs)


def test_voice_edit_recording_uses_real_backend_edit_audio_protocol(tmp_path: Path):
    recording = tmp_path / "edit-command.wav"
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
            json.dumps(
                {
                    "success": True,
                    "handled": True,
                    "mode": "replace",
                    "instruction": "把 A 替换成 B",
                    "expected_text": "B 是旧版本标记。",
                },
                ensure_ascii=False,
            ).encode("utf-8")
        )
        connection.close()
        listener.close()

    thread = threading.Thread(target=server)
    thread.start()
    assert ready.wait(2)
    result = playground_service.edit_recording(
        recording,
        context_text="A 是旧版本标记。",
        socket_path=socket_path,
        timeout_seconds=2,
    )
    thread.join(timeout=2)

    assert result["instruction"] == "把 A 替换成 B"
    assert result["expected_text"] == "B 是旧版本标记。"
    assert captured["type"] == "edit_audio"
    assert captured["audio_path"] == str(recording)
    assert captured["snapshot"] == {
        "text": "A 是旧版本标记。",
        "cursor_pos": len("A 是旧版本标记。"),
        "anchor_pos": len("A 是旧版本标记。"),
        "selected_text": "",
    }





def test_audio_runtime_candidates_prefer_fcitx_user_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("VOCOTYPE_AUDIO_RUNTIME_PYTHON", raising=False)
    candidates = playground_service._audio_runtime_candidates(
        home=tmp_path,
        project_root=tmp_path / "project",
    )
    assert candidates[0] == str(
        tmp_path / ".local/share/vocotype-fcitx5/.venv/bin/python"
    )
    assert candidates[1] == str(
        tmp_path / ".local/share/vocotype/.venv/bin/python"
    )


def test_audio_runtime_explains_install_repair_when_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("VOCOTYPE_AUDIO_RUNTIME_PYTHON", raising=False)
    with pytest.raises(
        playground_service.AudioRuntimeUnavailable,
        match="概览与安装.*安装 / 修复",
    ):
        playground_service.find_audio_runtime_python(
            home=tmp_path,
            project_root=tmp_path / "missing-project",
        )


def test_list_input_devices_delegates_to_private_audio_worker(
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict[str, object] = {}

    def fake_worker(command, payload=None, *, timeout=0):
        captured.update(command=command, payload=payload, timeout=timeout)
        return {
            "event": "result",
            "devices": [
                {
                    "device_id": 12,
                    "name": "USB microphone",
                    "sample_rate": 48_000,
                    "channels": 1,
                }
            ],
        }

    monkeypatch.setattr(playground_service, "_run_audio_worker", fake_worker)
    devices = playground_service.list_input_devices()

    assert captured == {"command": "list-inputs", "payload": None, "timeout": 20}
    assert devices == [
        playground_service.InputDevice(12, "USB microphone", 48_000, 1)
    ]


def test_record_audio_delegates_to_private_worker_and_preserves_waveform(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    updates: list[tuple[tuple[float, float], ...]] = []
    output = tmp_path / "recording.wav"

    def fake_worker(
        command,
        payload,
        *,
        waveform_callback=None,
        timeout=0,
    ):
        assert command == "record"
        assert payload == {
            "device_id": 7,
            "device_name": "USB microphone",
            "sample_rate": 16_000,
            "duration_seconds": 3.0,
            "output_path": str(output),
        }
        assert timeout >= 23
        assert waveform_callback is not None
        waveform_callback(((-0.3, 0.4),))
        return {
            "event": "result",
            "recording": {
                "path": str(output),
                "device_id": 7,
                "device_name": "USB microphone",
                "sample_rate": 16_000,
                "frame_count": 48_000,
                "duration_seconds": 3.0,
                "peak": 0.4,
                "rms": 0.1,
            },
        }

    monkeypatch.setattr(playground_service, "_stream_audio_worker", fake_worker)
    recording = playground_service.record_audio(
        device_id=7,
        device_name="USB microphone",
        sample_rate=16_000,
        output_path=output,
        waveform_callback=updates.append,
    )

    assert updates == [((-0.3, 0.4),)]
    assert recording.path == output
    assert recording.frame_count == 48_000
    assert recording.peak == pytest.approx(0.4)


def test_play_recording_delegates_to_private_audio_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    source = tmp_path / "recording.wav"
    source.write_bytes(b"wav")
    output = playground_service.OutputDevice(
        device_id="sink.usb",
        name="USB speakers",
        backend="pipewire",
        is_default=True,
    )

    def fake_worker(command, payload=None, *, timeout=0):
        assert command == "play"
        assert payload == {
            "path": str(source),
            "output_device": {
                "device_id": "sink.usb",
                "name": "USB speakers",
                "backend": "pipewire",
                "is_default": True,
            },
        }
        assert timeout == 180
        return {
            "event": "result",
            "playback": {
                "duration_seconds": 2.5,
                "backend": "PipeWire",
                "output_name": "USB speakers",
                "gain_db": 3.0,
            },
        }

    monkeypatch.setattr(playground_service, "_run_audio_worker", fake_worker)
    result = playground_service.play_recording(source, output_device=output)

    assert result == playground_service.PlaybackResult(
        duration_seconds=2.5,
        backend="PipeWire",
        output_name="USB speakers",
        gain_db=3.0,
    )


def test_audio_worker_probe_protocol_uses_audio_capable_runtime():
    root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(root)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "settings_center.playground_audio_worker",
            "probe",
        ],
        capture_output=True,
        text=True,
        env=environment,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(result.stdout.strip())
    assert payload == {"event": "result", "runtime": "ready"}
