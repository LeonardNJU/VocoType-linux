from __future__ import annotations

import base64
import json
import os
import socket
import threading
from pathlib import Path

import pytest

from app.native_core_client import NativeCoreClient, NativeCoreError


class FakeUnixCore:
    def __init__(self, socket_path: Path, responses: list[dict]):
        self.socket_path = socket_path
        self.responses = list(responses)
        self.requests: list[dict] = []
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        assert self._ready.wait(timeout=2.0)

    def _serve(self) -> None:
        self.socket_path.unlink(missing_ok=True)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(self.socket_path))
            server.listen(8)
            self._ready.set()
            while self.responses:
                client, _ = server.accept()
                with client:
                    chunks: list[bytes] = []
                    while True:
                        chunk = client.recv(65536)
                        if not chunk:
                            break
                        chunks.append(chunk)
                        try:
                            json.loads(b"".join(chunks).decode("utf-8"))
                        except (UnicodeDecodeError, json.JSONDecodeError):
                            continue
                        break
                    self.requests.append(json.loads(b"".join(chunks).decode("utf-8")))
                    client.sendall(
                        json.dumps(self.responses.pop(0), ensure_ascii=False).encode(
                            "utf-8"
                        )
                    )
        self.socket_path.unlink(missing_ok=True)

    def join(self) -> None:
        self._thread.join(timeout=2.0)
        assert not self._thread.is_alive()


def test_backend_preference_and_explicit_executable(monkeypatch, tmp_path: Path):
    executable = tmp_path / "vocotype-core"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)

    monkeypatch.setenv("VOCOTYPE_BACKEND", "python")
    assert NativeCoreClient.backend_preference() == "python"
    assert not NativeCoreClient.should_use_native(tmp_path / "config.json")

    monkeypatch.setenv("VOCOTYPE_BACKEND", "cpp")
    monkeypatch.setenv("VOCOTYPE_CORE_BINARY", str(executable))
    client = NativeCoreClient(config_path=tmp_path / "config.json")
    assert client.find_executable() == executable.resolve()
    assert NativeCoreClient.should_use_native(tmp_path / "config.json")

    monkeypatch.delenv("VOCOTYPE_BACKEND")
    assert NativeCoreClient.backend_preference() == "auto"


def test_native_core_client_request_protocol(monkeypatch, tmp_path: Path):
    socket_path = tmp_path / "core.sock"
    server = FakeUnixCore(
        socket_path,
        [
            {"pong": True, "backend": "cpp"},
            {"success": True, "text": "最终文本", "backend": "cpp"},
            {"success": True, "task_id": "cpp-1", "status": "running"},
            {
                "success": True,
                "task_id": "cpp-1",
                "status": "final",
                "last_seq": 2,
                "events": [{"seq": 2, "kind": "delta", "text": "文本"}],
                "final_text": "润色文本",
            },
        ],
    )
    client = NativeCoreClient(
        config_path=tmp_path / "ibus.json", socket_path=socket_path
    )
    monkeypatch.setattr(client, "ensure_running", lambda: None)

    assert client.ping()
    assert client.transcribe("/tmp/a.wav", long_mode=False)["text"] == "最终文本"
    started = client.start_transcription(
        "/tmp/b.wav", long_mode=True, enable_thinking=False
    )
    assert started["task_id"] == "cpp-1"
    poll = client.poll_transcription("cpp-1", after_seq=1)
    assert poll["final_text"] == "润色文本"

    server.join()
    assert server.requests == [
        {"type": "ping"},
        {"type": "transcribe", "audio_path": "/tmp/a.wav", "long_mode": False},
        {
            "type": "transcribe_start",
            "audio_path": "/tmp/b.wav",
            "long_mode": True,
            "enable_thinking": False,
        },
        {"type": "polish_poll", "task_id": "cpp-1", "after_seq": 1},
    ]


def test_native_preview_and_edit_protocol(monkeypatch, tmp_path: Path):
    socket_path = tmp_path / "preview.sock"
    server = FakeUnixCore(
        socket_path,
        [
            {"success": True, "session_id": "session-1", "chunk_samples": 9600},
            {"success": True, "text": "预览"},
            {"success": True, "final": True},
            {"success": True, "task_id": "edit-1", "status": "running"},
            {
                "success": True,
                "task_id": "edit-1",
                "status": "final",
                "result": {"success": True, "mode": "no_op"},
            },
        ],
    )
    client = NativeCoreClient(
        config_path=tmp_path / "ibus.json", socket_path=socket_path
    )
    monkeypatch.setattr(client, "ensure_running", lambda: None)

    assert client.start_session()["session_id"] == "session-1"
    assert client.feed("session-1", b"\x00\x01")["text"] == "预览"
    assert client.close_session("session-1", flush=False)["final"]
    assert client.start_edit(
        "/tmp/edit.wav",
        snapshot={"text": "原文", "cursor_pos": 2, "anchor_pos": 2},
        supports_surrounding=True,
    )["task_id"] == "edit-1"
    assert client.poll_edit("edit-1")["status"] == "final"

    server.join()
    assert server.requests[1] == {
        "type": "asr_preview_feed",
        "session_id": "session-1",
        "pcm16": base64.b64encode(b"\x00\x01").decode("ascii"),
        "is_final": False,
    }
    assert server.requests[2] == {
        "type": "asr_preview_close",
        "session_id": "session-1",
        "flush": False,
    }
    assert server.requests[3]["type"] == "edit_start"
    assert server.requests[3]["snapshot"]["text"] == "原文"


def test_require_success_raises(monkeypatch, tmp_path: Path):
    socket_path = tmp_path / "error.sock"
    server = FakeUnixCore(socket_path, [{"success": False, "error": "bad task"}])
    client = NativeCoreClient(
        config_path=tmp_path / "ibus.json", socket_path=socket_path
    )
    monkeypatch.setattr(client, "ensure_running", lambda: None)

    with pytest.raises(NativeCoreError, match="bad task"):
        client.start_transcription("/tmp/bad.wav")
    server.join()
