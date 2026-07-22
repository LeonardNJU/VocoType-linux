"""Client and lifecycle manager for the native VoCoType C++ core."""

from __future__ import annotations

import base64
import ctypes
import json
import logging
import os
import signal
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_REQUEST_TIMEOUT_S = 130.0
_DEFAULT_STARTUP_TIMEOUT_S = 45.0


class NativeCoreError(RuntimeError):
    """Raised when the native core is unavailable or rejects a request."""


class NativeCoreClient:
    """One-request-per-connection client for ``vocotype-core``."""

    _launch_lock = threading.Lock()
    _owned_processes: dict[str, subprocess.Popen[bytes]] = {}

    def __init__(
        self,
        *,
        config_path: str | os.PathLike[str],
        socket_path: str | os.PathLike[str] | None = None,
        executable: str | os.PathLike[str] | None = None,
        startup_timeout_s: float = _DEFAULT_STARTUP_TIMEOUT_S,
    ) -> None:
        self.config_path = Path(config_path).expanduser()
        self.socket_path = Path(
            socket_path
            or os.environ.get(
                "VOCOTYPE_NATIVE_CORE_SOCKET",
                f"/tmp/vocotype-ibus-core-{os.getuid()}.sock",
            )
        ).expanduser()
        self._explicit_executable = (
            Path(executable).expanduser() if executable is not None else None
        )
        self.startup_timeout_s = max(1.0, float(startup_timeout_s))
        self._known_running = False

    @staticmethod
    def backend_preference() -> str:
        value = os.environ.get("VOCOTYPE_BACKEND", "auto").strip().lower()
        if value in {"cpp", "native"}:
            return "cpp"
        if value in {"python", "legacy"}:
            return "python"
        return "auto"

    @classmethod
    def should_use_native(cls, config_path: str | os.PathLike[str]) -> bool:
        preference = cls.backend_preference()
        if preference == "python":
            return False
        client = cls(config_path=config_path)
        available = client.find_executable() is not None
        if preference == "cpp" and not available:
            logger.error("VOCOTYPE_BACKEND=cpp，但未找到 vocotype-core")
        return available

    def find_executable(self) -> Path | None:
        candidates: list[Path] = []
        configured = os.environ.get("VOCOTYPE_CORE_BINARY", "").strip()
        if configured:
            candidates.append(Path(configured).expanduser())
        if self._explicit_executable is not None:
            candidates.append(self._explicit_executable)
        candidates.extend(
            [
                Path.home() / ".local/lib/vocotype-streaming/bin/vocotype-core",
                Path.home() / ".local/libexec/vocotype-core",
                Path("/usr/libexec/vocotype-core"),
                Path("/usr/lib/vocotype/vocotype-core"),
                Path("/usr/lib64/vocotype/vocotype-core"),
            ]
        )
        for multiarch in Path("/usr/lib").glob("*/vocotype/vocotype-core"):
            candidates.append(multiarch)
        project_root = Path(__file__).resolve().parents[1]
        candidates.extend(
            [
                project_root / "build/native-core/vocotype-core",
                project_root / "build/native-core-release/vocotype-core",
                project_root / "native/streaming_worker/build/bundle/bin/vocotype-core",
            ]
        )
        seen: set[Path] = set()
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
            except OSError:
                resolved = candidate
            if resolved in seen:
                continue
            seen.add(resolved)
            if resolved.is_file() and os.access(resolved, os.X_OK):
                return resolved
        return None

    @staticmethod
    def _parent_death_signal() -> None:
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            libc.prctl(1, signal.SIGTERM, 0, 0, 0)
        except Exception:
            return

    def _request_once(
        self,
        payload: dict[str, Any],
        *,
        timeout_s: float,
    ) -> dict[str, Any]:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        chunks: list[bytes] = []
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(max(0.1, float(timeout_s)))
            client.connect(str(self.socket_path))
            client.sendall(encoded)
            while True:
                chunk = client.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
        if not chunks:
            raise NativeCoreError("native core returned an empty response")
        try:
            response = json.loads(b"".join(chunks).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise NativeCoreError(f"invalid native core response: {exc}") from exc
        if not isinstance(response, dict):
            raise NativeCoreError("native core response must be a JSON object")
        return response

    def ping(self, timeout_s: float = 0.5) -> bool:
        try:
            response = self._request_once({"type": "ping"}, timeout_s=timeout_s)
        except (OSError, NativeCoreError):
            self._known_running = False
            return False
        self._known_running = bool(response.get("pong")) and response.get("backend") == "cpp"
        return self._known_running

    def ensure_running(self) -> None:
        if self._known_running and self.socket_path.exists():
            return
        if self.ping():
            return
        with self._launch_lock:
            if self._known_running and self.socket_path.exists():
                return
            if self.ping():
                return
            executable = self.find_executable()
            if executable is None:
                raise NativeCoreError("vocotype-core is not installed")
            key = str(self.socket_path)
            old_process = self._owned_processes.get(key)
            if old_process is not None and old_process.poll() is None:
                old_process.terminate()
                try:
                    old_process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    old_process.kill()
                    old_process.wait(timeout=2.0)
            self._owned_processes.pop(key, None)
            try:
                self.socket_path.unlink(missing_ok=True)
            except OSError as exc:
                raise NativeCoreError(
                    f"cannot remove stale native socket {self.socket_path}: {exc}"
                ) from exc
            command = [
                str(executable),
                "--enable-final-asr",
                "--config",
                str(self.config_path),
                "--socket-path",
                str(self.socket_path),
            ]
            logger.info("启动 IBus 原生 core: %s", executable)
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=None,
                close_fds=True,
                preexec_fn=self._parent_death_signal,
            )
            self._owned_processes[key] = process
            deadline = time.monotonic() + self.startup_timeout_s
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    self._owned_processes.pop(key, None)
                    raise NativeCoreError(
                        f"vocotype-core exited during startup ({process.returncode})"
                    )
                if self.ping(timeout_s=0.5):
                    self._known_running = True
                    return
                time.sleep(0.05)
            process.terminate()
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2.0)
            self._owned_processes.pop(key, None)
            self._known_running = False
            raise NativeCoreError("timed out waiting for vocotype-core")

    def restart(self) -> None:
        self._known_running = False
        key = str(self.socket_path)
        with self._launch_lock:
            process = self._owned_processes.pop(key, None)
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2.0)
            try:
                self.socket_path.unlink(missing_ok=True)
            except OSError:
                pass
        self.ensure_running()

    def request(
        self,
        payload: dict[str, Any],
        *,
        timeout_s: float = _DEFAULT_REQUEST_TIMEOUT_S,
        require_success: bool = False,
    ) -> dict[str, Any]:
        self.ensure_running()
        try:
            response = self._request_once(payload, timeout_s=timeout_s)
        except OSError:
            self._known_running = False
            self.restart()
            response = self._request_once(payload, timeout_s=timeout_s)
        if require_success and not response.get("success"):
            raise NativeCoreError(str(response.get("error", "native core request failed")))
        return response

    def transcribe(self, audio_path: str, **options: Any) -> dict[str, Any]:
        return self.request(
            {"type": "transcribe", "audio_path": audio_path, **options},
            require_success=False,
        )

    def start_transcription(self, audio_path: str, **options: Any) -> dict[str, Any]:
        return self.request(
            {"type": "transcribe_start", "audio_path": audio_path, **options},
            timeout_s=10.0,
            require_success=True,
        )

    def poll_transcription(self, task_id: str, after_seq: int = 0) -> dict[str, Any]:
        return self.request(
            {"type": "polish_poll", "task_id": task_id, "after_seq": after_seq},
            timeout_s=10.0,
            require_success=True,
        )

    def cancel_transcription(self, task_id: str) -> dict[str, Any]:
        return self.request(
            {"type": "polish_cancel", "task_id": task_id}, timeout_s=5.0
        )

    def start_edit(self, audio_path: str, **context: Any) -> dict[str, Any]:
        return self.request(
            {"type": "edit_start", "audio_path": audio_path, **context},
            timeout_s=10.0,
            require_success=True,
        )

    def poll_edit(self, task_id: str) -> dict[str, Any]:
        return self.request(
            {"type": "edit_poll", "task_id": task_id},
            timeout_s=10.0,
            require_success=True,
        )

    def cancel_edit(self, task_id: str) -> dict[str, Any]:
        return self.request({"type": "edit_cancel", "task_id": task_id}, timeout_s=5.0)

    def start_session(self) -> dict[str, Any]:
        return self.request(
            {"type": "asr_preview_start"}, timeout_s=45.0, require_success=False
        )

    def feed(self, session_id: str, pcm16: bytes) -> dict[str, Any]:
        return self.request(
            {
                "type": "asr_preview_feed",
                "session_id": session_id,
                "pcm16": base64.b64encode(pcm16).decode("ascii"),
                "is_final": False,
            },
            timeout_s=10.0,
            require_success=False,
        )

    def close_session(self, session_id: str, *, flush: bool = False) -> dict[str, Any]:
        return self.request(
            {
                "type": "asr_preview_close",
                "session_id": session_id,
                "flush": bool(flush),
            },
            timeout_s=10.0,
            require_success=False,
        )

    @classmethod
    def close_all(cls) -> None:
        with cls._launch_lock:
            processes = list(cls._owned_processes.items())
            cls._owned_processes.clear()
        for socket_name, process in processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2.0)
            try:
                Path(socket_name).unlink(missing_ok=True)
            except OSError:
                pass
