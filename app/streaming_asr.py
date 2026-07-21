"""Optional CPU-only FunASR online preview for the 2-pass ASR path.

The online model is hosted by a disposable native worker built from FunASR's
official C++ ONNX runtime. Its output is UI-only: callers must still submit the
original complete recording to :class:`app.funasr_server.FunASRServer` for the
final committed text.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import selectors
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, TextIO

import numpy as np

from app.audio_utils import SAMPLE_RATE, resample_audio
from app.download_models import get_model_cache_path, model_requirements
from app.funasr_config import MODELS, STREAMING_MODEL_REVISION

logger = logging.getLogger(__name__)

DEFAULT_CHUNK_SIZE = (5, 10, 5)
SAMPLES_PER_60_MS = 960


class StreamingAudioChunker:
    """Convert arbitrary native-rate PCM blocks into fixed 16 kHz ASR chunks."""

    def __init__(
        self,
        source_rate: int,
        target_chunk_samples: int,
        target_rate: int = SAMPLE_RATE,
    ) -> None:
        if source_rate <= 0 or target_chunk_samples <= 0 or target_rate <= 0:
            raise ValueError("sample rates and chunk size must be positive")
        self.source_rate = int(source_rate)
        self.target_rate = int(target_rate)
        self.target_chunk_samples = int(target_chunk_samples)
        self.source_chunk_samples = max(
            1,
            round(self.target_chunk_samples * self.source_rate / self.target_rate),
        )
        self._pending = np.empty(0, dtype=np.int16)

    def push(self, pcm: np.ndarray) -> list[np.ndarray]:
        values = np.asarray(pcm, dtype=np.int16).reshape(-1)
        if values.size:
            self._pending = np.concatenate((self._pending, values))
        chunks: list[np.ndarray] = []
        while self._pending.size >= self.source_chunk_samples:
            native = self._pending[: self.source_chunk_samples]
            self._pending = self._pending[self.source_chunk_samples :]
            chunks.append(self._convert(native, exact=True))
        return chunks

    def finish(self) -> np.ndarray:
        native = self._pending
        self._pending = np.empty(0, dtype=np.int16)
        if not native.size:
            return np.empty(0, dtype=np.int16)
        return self._convert(native, exact=False)

    def _convert(self, native: np.ndarray, *, exact: bool) -> np.ndarray:
        if self.source_rate == self.target_rate:
            converted = native.astype(np.int16, copy=True)
        else:
            converted = resample_audio(native, self.source_rate, self.target_rate)
            converted = np.asarray(converted, dtype=np.int16).reshape(-1)
        if exact:
            if converted.size < self.target_chunk_samples:
                converted = np.pad(
                    converted,
                    (0, self.target_chunk_samples - converted.size),
                )
            elif converted.size > self.target_chunk_samples:
                converted = converted[: self.target_chunk_samples]
        return converted


def _valid_chunk_size(raw: Any) -> tuple[int, int, int]:
    try:
        values = tuple(int(value) for value in raw)
    except (TypeError, ValueError):
        return DEFAULT_CHUNK_SIZE
    if len(values) != 3 or any(value < 0 for value in values) or values[1] <= 0:
        return DEFAULT_CHUNK_SIZE
    return values


def _worker_candidates(configured: str = "") -> list[Path]:
    project_root = Path(__file__).resolve().parents[1]
    values = [
        os.environ.get("VOCOTYPE_STREAMING_WORKER", ""),
        configured,
        shutil.which("vocotype-streaming-worker") or "",
        str(project_root / "native/streaming_worker/build/bundle/bin/vocotype-streaming-worker"),
        str(project_root / "native/streaming_worker/build/bin/vocotype-streaming-worker"),
        str(project_root / "libexec/vocotype-streaming-worker"),
        str(Path(sys.prefix) / "libexec/vocotype-streaming-worker"),
        "/usr/libexec/vocotype-streaming-worker",
        "/usr/lib/vocotype/vocotype-streaming-worker",
        "/usr/lib64/vocotype/vocotype-streaming-worker",
        str(
            Path.home()
            / ".local/lib/vocotype-streaming/bin/vocotype-streaming-worker"
        ),
        str(Path.home() / ".local/lib/vocotype/vocotype-streaming-worker"),
    ]
    result: list[Path] = []
    seen: set[str] = set()
    for value in values:
        if not value:
            continue
        candidate = Path(value).expanduser()
        key = str(candidate)
        if key not in seen:
            seen.add(key)
            result.append(candidate)
    return result


def resolve_streaming_worker(configured: str = "") -> Path:
    for candidate in _worker_candidates(configured):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise FileNotFoundError(
        "vocotype-streaming-worker not found; install the native streaming runtime"
    )


class StreamingASRProcess:
    """Manage the optional native online model as a disposable subprocess.

    The host VoCoType backend stays resident, but the online ONNX sessions do
    not. The native worker is started lazily for a recording and exits after an
    idle timeout. Disabling the feature calls :meth:`cleanup`, terminating it
    immediately and allowing Linux to reclaim all model memory.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = dict(config or {})
        self.enabled = bool(self.config.get("enabled", False))
        self.model_name = str(
            os.environ.get("FUNASR_STREAMING_ASR_MODEL")
            or self.config.get("model")
            or MODELS["asr_streaming"]["name"]
        )
        self.chunk_size = _valid_chunk_size(
            self.config.get("chunk_size", DEFAULT_CHUNK_SIZE)
        )
        self.chunk_samples = self.chunk_size[1] * SAMPLES_PER_60_MS
        self.threads = max(
            1,
            min(4, int(self.config.get("intra_op_num_threads", 1) or 1)),
        )
        self.idle_timeout_s = max(
            1.0, float(self.config.get("idle_timeout_s", 30.0) or 30.0)
        )
        self.session_idle_timeout_s = max(
            2.0,
            float(self.config.get("session_idle_timeout_s", 15.0) or 15.0),
        )
        self.startup_timeout_s = max(
            1.0, float(self.config.get("startup_timeout_s", 180.0) or 180.0)
        )
        self.request_timeout_s = max(
            0.5, float(self.config.get("request_timeout_s", 2.0) or 2.0)
        )
        self.worker_path = str(self.config.get("worker_path", "") or "")

        self._process: subprocess.Popen[str] | None = None
        self._stdin: TextIO | None = None
        self._stdout: TextIO | None = None
        self._worker_ready = False
        self._process_lock = threading.RLock()
        self._request_lock = threading.Lock()
        self._initializing = False
        self._init_error = ""
        self._disposed = False

    @property
    def ready(self) -> bool:
        with self._process_lock:
            process = self._process
            return bool(
                self.enabled
                and not self._disposed
                and self._worker_ready
                and process is not None
                and process.poll() is None
                and self._stdin is not None
                and self._stdout is not None
            )

    @property
    def init_error(self) -> str:
        return self._init_error

    def initialize(self) -> dict[str, Any]:
        if not self.enabled or self._disposed:
            return {"success": False, "error": "streaming_disabled"}
        with self._process_lock:
            if self.ready:
                return {"success": True}
            if self._initializing:
                return {"success": False, "error": "streaming_initializing"}
            self._initializing = True
            self._init_error = ""
        return self._start_claimed()

    def initialize_async(self) -> None:
        """Start loading without blocking; normally first recording is lazy."""
        if not self.enabled or self._disposed:
            return
        with self._process_lock:
            if self.ready or self._initializing:
                return
            self._initializing = True
            self._init_error = ""
        threading.Thread(
            target=self._start_claimed,
            daemon=True,
            name="VoCoTypeNativeStreamingWorkerInit",
        ).start()

    def _resolve_model_dir(self) -> str:
        model_config = {"name": self.model_name, "type": "asr_streaming"}
        required_files, required_any_files = model_requirements(model_config)
        return get_model_cache_path(
            self.model_name,
            STREAMING_MODEL_REVISION,
            required_files=required_files,
            required_any_files=required_any_files,
        )

    def _start_claimed(self) -> dict[str, Any]:
        process: subprocess.Popen[str] | None = None
        assigned = False
        try:
            self._discard_worker()
            if not self.enabled or self._disposed:
                return {"success": False, "error": "streaming_disabled"}
            worker = resolve_streaming_worker(self.worker_path)
            model_dir = self._resolve_model_dir()
            command = [
                str(worker),
                "--model-dir",
                model_dir,
                "--threads",
                str(self.threads),
                "--chunk-size",
                *(str(value) for value in self.chunk_size),
                "--idle-timeout-ms",
                str(round(self.idle_timeout_s * 1000)),
                "--session-idle-timeout-ms",
                str(round(self.session_idle_timeout_s * 1000)),
            ]
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
                start_new_session=True,
            )
            if process.stdin is None or process.stdout is None:
                raise RuntimeError("streaming_worker_stdio_unavailable")
            with self._process_lock:
                if self._disposed or not self.enabled:
                    raise RuntimeError("streaming_disposed")
                self._process = process
                self._stdin = process.stdin
                self._stdout = process.stdout
                self._worker_ready = False
                assigned = True

            # FunASR normally emits only a few model-load lines, but draining
            # immediately prevents a verbose native failure from filling the
            # stderr pipe before the ready event is produced.
            if process.stderr is not None:
                threading.Thread(
                    target=self._drain_stderr,
                    args=(process, process.stderr),
                    daemon=True,
                    name="VoCoTypeNativeStreamingWorkerStderr",
                ).start()
            response = self._read_response(process.stdout, self.startup_timeout_s)
            if response.get("type") != "ready" or not response.get("success"):
                raise RuntimeError(
                    str(response.get("error", "streaming_worker_failed"))
                )
            with self._process_lock:
                if self._disposed or not self.enabled:
                    raise RuntimeError("streaming_disposed")
                self._worker_ready = True
                self.chunk_samples = max(
                    1, int(response.get("chunk_samples", self.chunk_samples))
                )
            threading.Thread(
                target=self._monitor_worker,
                args=(process,),
                daemon=True,
                name="VoCoTypeNativeStreamingWorkerMonitor",
            ).start()
            logger.info(
                "FunASR native 在线模型就绪 pid=%s model=%s chunk=%s threads=%s",
                process.pid,
                self.model_name,
                self.chunk_size,
                self.threads,
            )
            return {"success": True}
        except Exception as exc:  # noqa: BLE001
            with self._process_lock:
                self._init_error = str(exc)
            if assigned:
                self._discard_worker()
            elif process is not None:
                self._terminate_process(process)
            logger.warning("native 在线 ASR worker 初始化失败: %s", exc)
            return {"success": False, "error": str(exc)}
        finally:
            with self._process_lock:
                self._initializing = False

    @staticmethod
    def _read_response(stream: TextIO, timeout_s: float) -> dict[str, Any]:
        selector = selectors.DefaultSelector()
        try:
            selector.register(stream, selectors.EVENT_READ)
            if not selector.select(timeout_s):
                raise TimeoutError("streaming_worker_request_timeout")
            line = stream.readline()
        finally:
            selector.close()
        if not line:
            raise EOFError("streaming_worker_exited")
        response = json.loads(line)
        if not isinstance(response, dict):
            raise ValueError("streaming_worker_invalid_response")
        return response

    def _request(
        self,
        payload: dict[str, Any],
        *,
        timeout_s: float | None = None,
        start_if_needed: bool = True,
    ) -> dict[str, Any]:
        if not self.ready:
            if not start_if_needed:
                return {"success": False, "error": "streaming_not_ready"}
            initialized = self.initialize()
            if not initialized.get("success"):
                return initialized

        with self._request_lock:
            with self._process_lock:
                process = self._process
                stdin = self._stdin
                stdout = self._stdout
            if process is None or stdin is None or stdout is None:
                return {"success": False, "error": "streaming_not_ready"}
            try:
                stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
                stdin.flush()
                timeout = self.request_timeout_s if timeout_s is None else timeout_s
                return self._read_response(stdout, timeout)
            except (EOFError, OSError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
                self._discard_worker()
                return {"success": False, "error": str(exc)}

    def start_session(self) -> dict[str, Any]:
        response = self._request(
            {"type": "start"}, timeout_s=self.startup_timeout_s
        )
        if response.get("success"):
            response.setdefault("chunk_samples", self.chunk_samples)
            response.setdefault("sample_rate", SAMPLE_RATE)
        return response

    def feed(
        self, session_id: str, pcm: bytes, *, is_final: bool = False
    ) -> dict[str, Any]:
        return self._request(
            {
                "type": "feed",
                "session_id": session_id,
                "pcm16": base64.b64encode(pcm).decode("ascii"),
                "is_final": is_final,
            }
        )

    def close_session(
        self, session_id: str, *, flush: bool = False
    ) -> dict[str, Any]:
        return self._request(
            {
                "type": "close",
                "session_id": session_id,
                "flush": flush,
            },
            start_if_needed=False,
        )

    def cleanup(self) -> None:
        # Ask the worker to stop while ``ready`` is still meaningful, then
        # mark the manager disabled. Forced termination remains the fallback.
        if self.ready:
            self._request(
                {"type": "stop"},
                timeout_s=1.0,
                start_if_needed=False,
            )
        with self._process_lock:
            self._disposed = True
            self.enabled = False
        self._discard_worker()

    def _monitor_worker(self, process: subprocess.Popen[str]) -> None:
        process.wait()
        with self._process_lock:
            if self._process is process:
                self._process = None
                self._stdin = None
                self._stdout = None
                self._worker_ready = False
        logger.debug("native 在线 ASR worker 已退出 pid=%s", process.pid)

    @staticmethod
    def _drain_stderr(process: subprocess.Popen[str], stream: TextIO) -> None:
        for line in stream:
            logger.debug("streaming-worker[%s]: %s", process.pid, line.rstrip())

    def _discard_worker(self) -> None:
        with self._process_lock:
            process = self._process
            self._process = None
            self._stdin = None
            self._stdout = None
            self._worker_ready = False
        if process is not None:
            self._terminate_process(process)

    @staticmethod
    def _terminate_process(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1.0)
