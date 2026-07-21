from __future__ import annotations

import base64
import stat
import textwrap
from pathlib import Path

import numpy as np

from app.download_models import model_requirements
from app.streaming_asr import (
    StreamingASRProcess,
    StreamingAudioChunker,
    resolve_streaming_worker,
)


def _fake_native_worker(tmp_path: Path) -> Path:
    worker = tmp_path / "vocotype-streaming-worker"
    worker.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import base64
            import json
            import sys

            sessions = {}
            print(json.dumps({
                "type": "ready",
                "success": True,
                "sample_rate": 16000,
                "chunk_samples": 9600,
            }), flush=True)
            for line in sys.stdin:
                request = json.loads(line)
                kind = request.get("type")
                if kind == "start":
                    session_id = str(len(sessions) + 1)
                    sessions[session_id] = ""
                    response = {
                        "success": True,
                        "session_id": session_id,
                        "sample_rate": 16000,
                        "chunk_samples": 9600,
                    }
                elif kind == "feed":
                    session_id = request["session_id"]
                    pcm = base64.b64decode(request["pcm16"], validate=True)
                    sessions[session_id] += f"{len(pcm) // 2}"
                    response = {
                        "success": True,
                        "text": sessions[session_id],
                        "final": bool(request.get("is_final", False)),
                    }
                elif kind == "close":
                    session_id = request["session_id"]
                    response = {
                        "success": True,
                        "text": sessions.pop(session_id, ""),
                        "final": True,
                    }
                elif kind == "stop":
                    print(json.dumps({"success": True}), flush=True)
                    break
                else:
                    response = {"success": False, "error": "unknown_request"}
                print(json.dumps(response), flush=True)
            """
        ),
        encoding="utf-8",
    )
    worker.chmod(worker.stat().st_mode | stat.S_IXUSR)
    return worker


def test_streaming_process_is_disabled_by_default():
    process = StreamingASRProcess({})
    assert process.enabled is False
    assert process.start_session() == {
        "success": False,
        "error": "streaming_disabled",
    }
    assert process._process is None


def test_online_model_requires_encoder_and_decoder_payloads():
    required, required_any = model_requirements(
        {"name": "online", "type": "asr_streaming"}
    )
    assert required == ("config.yaml", "am.mvn", "tokens.json")
    assert required_any == (
        ("model_quant.onnx", "model.onnx"),
        ("decoder_quant.onnx", "decoder.onnx"),
    )


def test_chunker_preserves_fixed_600ms_shape_at_native_rate(monkeypatch):
    monkeypatch.setattr(
        "app.streaming_asr.resample_audio",
        lambda audio, _source, _target: audio[::3],
    )
    chunker = StreamingAudioChunker(source_rate=48000, target_chunk_samples=9600)
    first = np.arange(14400, dtype=np.int16)
    second = np.arange(14400, 28800, dtype=np.int16)
    assert chunker.push(first) == []
    chunks = chunker.push(second)
    assert len(chunks) == 1
    assert chunks[0].dtype == np.int16
    assert chunks[0].shape == (9600,)
    assert chunker.finish().size == 0


def test_native_worker_is_lazy_and_preserves_full_preview(tmp_path, monkeypatch):
    worker = _fake_native_worker(tmp_path)
    process = StreamingASRProcess(
        {
            "enabled": True,
            "worker_path": str(worker),
            "startup_timeout_s": 2,
            "request_timeout_s": 2,
        }
    )
    monkeypatch.setattr(process, "_resolve_model_dir", lambda: str(tmp_path))

    assert process._process is None
    started = process.start_session()
    assert started["success"] is True
    assert started["chunk_samples"] == 9600
    assert process.ready is True

    payload = np.zeros(9600, dtype="<i2").tobytes()
    first = process.feed(started["session_id"], payload)
    second = process.feed(started["session_id"], payload)
    assert first == {"success": True, "text": "9600", "final": False}
    assert second == {"success": True, "text": "96009600", "final": False}

    closed = process.close_session(started["session_id"])
    assert closed == {"success": True, "text": "96009600", "final": True}
    process.cleanup()
    assert process._process is None
    assert process.enabled is False


def test_native_protocol_encodes_pcm16_as_base64(tmp_path, monkeypatch):
    worker = _fake_native_worker(tmp_path)
    process = StreamingASRProcess(
        {"enabled": True, "worker_path": str(worker), "startup_timeout_s": 2}
    )
    monkeypatch.setattr(process, "_resolve_model_dir", lambda: str(tmp_path))
    started = process.start_session()
    raw = b"\x00\x01\x02\x03"

    captured = {}
    original_request = process._request

    def capture(payload, **kwargs):
        captured.update(payload)
        return original_request(payload, **kwargs)

    monkeypatch.setattr(process, "_request", capture)
    result = process.feed(started["session_id"], raw)
    assert result["success"] is True
    assert base64.b64decode(captured["pcm16"], validate=True) == raw
    process.cleanup()


def test_worker_resolution_rejects_non_executable(tmp_path, monkeypatch):
    worker = tmp_path / "worker"
    worker.write_text("not executable", encoding="utf-8")
    monkeypatch.delenv("VOCOTYPE_STREAMING_WORKER", raising=False)
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(
        "app.streaming_asr._worker_candidates", lambda _configured="": [worker]
    )
    try:
        resolve_streaming_worker(str(worker))
    except FileNotFoundError as exc:
        assert "native streaming runtime" in str(exc)
    else:
        raise AssertionError("non-executable worker must be rejected")


def test_native_worker_source_uses_official_local_funasr_api():
    source = Path("native/streaming_worker/worker.cpp").read_text(encoding="utf-8")
    assert "FunASRInit" in source
    assert "FunASROnlineInit" in source
    assert "FunASRInferBuffer" in source
    assert "http://" not in source
    assert "https://" not in source
    assert "websocket" not in source.lower()


def test_native_worker_build_is_pinned_and_cpu_only():
    build = Path("native/streaming_worker/build.sh").read_text(encoding="utf-8")
    assert "bd6e72142f1cca3c30b7651bf5fa567dfe969810" in build
    assert "-DGPU=OFF" in build
    assert "ONNXRUNTIME_DIR" in build
