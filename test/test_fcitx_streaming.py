from __future__ import annotations

from pathlib import Path
import json
import socket
import threading
import time

from fcitx5.backend.fcitx5_server import Fcitx5Backend, StreamTask


ROOT = Path(__file__).resolve().parents[1]


class _FakeAsr:
    def __init__(self, result):
        self.result = result

    def transcribe_audio(self, audio_path, *, options):
        assert Path(audio_path).exists()
        assert options == {"use_punc": True}
        return dict(self.result)


class _FakeStreamingPolisher:
    enabled = True

    def __init__(self, events):
        self.events = events
        self.release_calls = 0
        self.received = None

    def should_polish(self, text, *, long_mode, min_chars=None):
        self.received = (text, long_mode, min_chars)
        return True

    def stream_polish(self, text, *, long_mode, min_chars=None, enable_thinking=None):
        self.received = (text, long_mode, min_chars, enable_thinking)
        yield from self.events

    @staticmethod
    def format_failure_message(reason):
        return f"failed:{reason}"

    def release(self):
        self.release_calls += 1


def _backend(asr_result, polisher):
    backend = Fcitx5Backend.__new__(Fcitx5Backend)
    backend.asr_server = _FakeAsr(asr_result)
    backend._asr_options = {"use_punc": True}
    backend._asr_lock = threading.Lock()
    backend._slm_polisher = polisher
    return backend


def test_stream_task_tracks_incremental_events_and_after_seq():
    task = StreamTask(task_id="task", long_mode=True)
    task.add_event("status", "识别中...")
    task.set_original("原始文本")
    task.set_phase("polishing")
    task.add_event("delta", "润色", preview="润色")

    first = task.snapshot(after_seq=0, idle_timeout_s=20.0)
    assert [event["kind"] for event in first["events"]] == ["status", "delta"]
    assert first["preview"] == "润色"
    assert first["original_text"] == "原始文本"

    second = task.snapshot(after_seq=first["last_seq"], idle_timeout_s=20.0)
    assert second["events"] == []


def test_stream_task_idle_timeout_becomes_error():
    task = StreamTask(
        task_id="task",
        long_mode=True,
        polish_timeout_ms=1000,
        phase="polishing",
    )
    task.last_event_at = time.monotonic() - 2.0

    snapshot = task.snapshot(after_seq=0, idle_timeout_s=20.0)
    assert snapshot["status"] == "error"
    assert snapshot["reason"] == "idle_timeout"
    assert snapshot["events"][-1]["kind"] == "error"


def test_backend_stream_pipeline_preserves_preview_and_final(tmp_path):
    audio = tmp_path / "recording.wav"
    audio.write_bytes(b"audio")
    polisher = _FakeStreamingPolisher(
        [
            {"kind": "status", "text": "正在调用大模型..."},
            {"kind": "delta", "text": "润色后", "preview": "润色后"},
            {"kind": "delta", "text": "文本", "preview": "润色后文本"},
            {"kind": "final", "text": "润色后文本", "reason": "ok"},
        ]
    )
    backend = _backend({"success": True, "text": "原始文本"}, polisher)
    task = StreamTask(
        task_id="task",
        long_mode=True,
        polish_min_chars=4,
        enable_thinking=False,
    )

    backend._run_stream_task(task, str(audio))

    snapshot = task.snapshot(after_seq=0, idle_timeout_s=20.0)
    assert snapshot["status"] == "final"
    assert snapshot["final_text"] == "润色后文本"
    assert snapshot["preview"] == "润色后文本"
    assert snapshot["original_text"] == "原始文本"
    assert polisher.received == ("原始文本", True, 4, False)
    assert polisher.release_calls == 1
    assert not audio.exists()


def test_backend_stream_error_keeps_original_for_fallback(tmp_path):
    audio = tmp_path / "recording.wav"
    audio.write_bytes(b"audio")
    polisher = _FakeStreamingPolisher(
        [
            {
                "kind": "error",
                "reason": "request_error",
                "message": "远端不可用",
            }
        ]
    )
    backend = _backend({"success": True, "text": "原始文本"}, polisher)
    task = StreamTask(task_id="task", long_mode=True)

    backend._run_stream_task(task, str(audio))

    snapshot = task.snapshot(after_seq=0, idle_timeout_s=20.0)
    assert snapshot["status"] == "error"
    assert snapshot["error"] == "远端不可用"
    assert snapshot["original_text"] == "原始文本"
    assert polisher.release_calls == 1


def test_cpp_module_uses_async_start_poll_cancel_and_live_preview():
    header = (ROOT / "fcitx5" / "module" / "vocotype_module.h").read_text(
        encoding="utf-8"
    )
    source = (ROOT / "fcitx5" / "module" / "vocotype_module.cpp").read_text(
        encoding="utf-8"
    )
    ipc_header = (ROOT / "fcitx5" / "addon" / "ipc_client.h").read_text(
        encoding="utf-8"
    )

    for option in (
        "PolishByDefault",
        "PolishMinChars",
        "PolishTimeoutMs",
        "EnableThinking",
    ):
        assert option in header
    assert "startTranscription" in source
    assert "pollPolishTask" in source
    assert "cancelPolishTask" in source
    assert "showPolishProgress" in source
    assert "TranscribeStartResult" in ipc_header
    assert "PolishPollResult" in ipc_header



def _ipc_request(backend, payload):
    server_sock, client_sock = socket.socketpair()
    thread = threading.Thread(target=backend.handle_client, args=(server_sock,))
    thread.start()
    try:
        client_sock.sendall(json.dumps(payload).encode("utf-8"))
        client_sock.shutdown(socket.SHUT_WR)
        chunks = []
        while True:
            chunk = client_sock.recv(8192)
            if not chunk:
                break
            chunks.append(chunk)
        return json.loads(b"".join(chunks).decode("utf-8"))
    finally:
        client_sock.close()
        thread.join(timeout=2)


def test_backend_ipc_start_and_poll_protocol(tmp_path):
    audio = tmp_path / "recording.wav"
    audio.write_bytes(b"audio")
    polisher = _FakeStreamingPolisher(
        [
            {"kind": "delta", "text": "结果", "preview": "结果"},
            {"kind": "final", "text": "结果", "reason": "ok"},
        ]
    )
    backend = _backend({"success": True, "text": "原始文本"}, polisher)
    backend._stream_tasks = {}
    backend._stream_tasks_lock = threading.Lock()
    backend._slm_stream_idle_timeout_s = 20.0

    started = _ipc_request(
        backend,
        {
            "type": "transcribe_start",
            "audio_path": str(audio),
            "long_mode": True,
            "polish_min_chars": 1,
            "polish_timeout_ms": 20000,
            "enable_thinking": False,
        },
    )
    assert started["success"] is True
    task_id = started["task_id"]

    deadline = time.monotonic() + 2.0
    while True:
        polled = _ipc_request(
            backend,
            {"type": "polish_poll", "task_id": task_id, "after_seq": 0},
        )
        if polled["status"] != "running":
            break
        assert time.monotonic() < deadline
        time.sleep(0.01)

    assert polled["success"] is True
    assert polled["status"] == "final"
    assert polled["final_text"] == "结果"
    assert polled["original_text"] == "原始文本"
    assert any(event["kind"] == "delta" for event in polled["events"])
