#!/usr/bin/env python3
"""Fcitx 5 global voice module backend with streamed polishing tasks."""
from __future__ import annotations

import sys
import os
import json
import base64
import socket
import logging
import signal
import stat
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

# 添加项目根目录到 path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import DEFAULT_CONFIG, ensure_logging_dir, load_config
from app.funasr_server import FunASRServer
from app.logging_config import setup_logging
from app.slm_polisher import SLMPolisher
from app.streaming_asr import StreamingASRProcess
from app.voice_edit import SurroundingSnapshot

logger = logging.getLogger(__name__)

SOCKET_PATH = "/tmp/vocotype-fcitx5.sock"
MAX_REQUEST_BYTES = 1024 * 1024
REQUEST_TIMEOUT_S = 2.0
MAX_PREVIEW_CHUNK_BYTES = 128 * 1024
DEFAULT_CONFIG_PATH = "~/.config/vocotype/fcitx5-backend.json"
TASK_TTL_S = 300.0
EDIT_TASK_TIMEOUT_S = 30.0


@dataclass
class StreamTask:
    task_id: str
    long_mode: bool
    polish_min_chars: int = 0
    polish_timeout_ms: int = 0
    enable_thinking: bool | None = None
    created_at: float = field(default_factory=time.monotonic)
    last_event_at: float = field(default_factory=time.monotonic)
    status: str = "running"
    phase: str = "asr"
    events: list[dict] = field(default_factory=list)
    seq: int = 0
    preview: str = ""
    final_text: str = ""
    original_text: str = ""
    error: str = ""
    reason: str = ""
    cancelled: bool = False
    done_at: float | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add_event(self, kind: str, text: str = "", **extra) -> None:
        with self.lock:
            if self.cancelled or self.status != "running":
                return
            self._add_event_locked(kind, text, **extra)

    def set_original(self, text: str) -> None:
        with self.lock:
            self.original_text = text

    def set_phase(self, phase: str) -> None:
        with self.lock:
            self.phase = phase
            self.last_event_at = time.monotonic()

    def touch(self) -> None:
        with self.lock:
            if self.cancelled or self.status != "running":
                return
            self.last_event_at = time.monotonic()

    def is_cancelled(self) -> bool:
        with self.lock:
            return self.cancelled

    def mark_final(self, text: str, reason: str = "ok") -> None:
        with self.lock:
            if self.cancelled or self.status != "running":
                return
            self.status = "final"
            self.final_text = text
            self.preview = text
            self.reason = reason
            self.done_at = time.monotonic()
            self._add_event_locked("final", text, reason=reason)

    def mark_error(self, message: str, reason: str = "error") -> None:
        with self.lock:
            if self.cancelled or self.status != "running":
                return
            self._mark_error_locked(message, reason)

    def cancel(self) -> None:
        with self.lock:
            self.cancelled = True
            if self.status == "running":
                self.status = "cancelled"
                self.done_at = time.monotonic()
                self._add_event_locked("cancelled", "已取消")

    def snapshot(self, after_seq: int, idle_timeout_s: float) -> dict:
        with self.lock:
            now = time.monotonic()
            effective_idle_timeout_s = (
                max(0.1, self.polish_timeout_ms / 1000.0)
                if self.polish_timeout_ms > 0
                else idle_timeout_s
            )
            if (
                self.status == "running"
                and self.phase == "polishing"
                and effective_idle_timeout_s > 0
                and now - self.last_event_at > effective_idle_timeout_s
            ):
                self.cancelled = True
                self._mark_error_locked("SLM 调用失败：长时间未收到模型输出", "idle_timeout")

            events = [event for event in self.events if int(event.get("seq", 0)) > after_seq]
            return {
                "success": True,
                "task_id": self.task_id,
                "status": self.status,
                "phase": self.phase,
                "events": events,
                "last_seq": self.seq,
                "preview": self.preview,
                "final_text": self.final_text,
                "original_text": self.original_text,
                "error": self.error,
                "reason": self.reason,
            }

    def _mark_error_locked(self, message: str, reason: str) -> None:
        self.status = "error"
        self.error = message
        self.reason = reason
        self.done_at = time.monotonic()
        self._add_event_locked("error", message, reason=reason)

    def _add_event_locked(self, kind: str, text: str = "", **extra) -> None:
        self.seq += 1
        event = {"seq": self.seq, "kind": kind, "text": text}
        event.update(extra)
        self.events.append(event)
        if len(self.events) > 200:
            self.events = self.events[-200:]
        if kind == "delta":
            self.preview = str(extra.get("preview", self.preview))
        elif kind == "status":
            self.reason = str(extra.get("reason", self.reason))
        self.last_event_at = time.monotonic()


@dataclass
class EditTask:
    task_id: str
    created_at: float = field(default_factory=time.monotonic)
    status: str = "running"
    phase: str = "asr"
    instruction: str = ""
    result: dict = field(default_factory=dict)
    error: str = ""
    reason: str = ""
    cancelled: bool = False
    done_at: float | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def set_instruction(self, instruction: str) -> None:
        with self.lock:
            if self.cancelled or self.status != "running":
                return
            self.instruction = instruction
            self.phase = "editing"

    def is_cancelled(self) -> bool:
        with self.lock:
            return self.cancelled

    def mark_final(self, result: dict) -> None:
        with self.lock:
            if self.cancelled or self.status != "running":
                return
            self.status = "final"
            self.phase = "done"
            self.result = dict(result)
            self.instruction = str(result.get("instruction", self.instruction))
            self.reason = str(result.get("reason", "ok"))
            self.done_at = time.monotonic()

    def mark_error(
        self,
        message: str,
        reason: str = "error",
        instruction: str = "",
    ) -> None:
        with self.lock:
            if self.cancelled or self.status != "running":
                return
            self._mark_error_locked(message, reason, instruction)

    def cancel(self) -> None:
        with self.lock:
            self.cancelled = True
            if self.status == "running":
                self.status = "cancelled"
                self.phase = "done"
                self.error = "已取消语音编辑"
                self.reason = "cancelled"
                self.done_at = time.monotonic()

    def snapshot(self) -> dict:
        with self.lock:
            if (
                self.status == "running"
                and time.monotonic() - self.created_at > EDIT_TASK_TIMEOUT_S
            ):
                self.cancelled = True
                self._mark_error_locked(
                    "AI 编辑超过 30 秒，已停止等待",
                    "timeout",
                    self.instruction,
                )
            return {
                "success": True,
                "task_id": self.task_id,
                "status": self.status,
                "phase": self.phase,
                "instruction": self.instruction,
                "result": dict(self.result),
                "error": self.error,
                "reason": self.reason,
            }

    def _mark_error_locked(
        self,
        message: str,
        reason: str,
        instruction: str = "",
    ) -> None:
        self.status = "error"
        self.phase = "done"
        self.error = message
        self.reason = reason
        if instruction:
            self.instruction = instruction
        self.done_at = time.monotonic()


def load_backend_config() -> tuple[dict, str]:
    """Load backend config from user config file if present."""
    config_path = os.environ.get("VOCOTYPE_FCITX5_CONFIG", DEFAULT_CONFIG_PATH)
    expanded_path = os.path.expanduser(config_path)
    if not os.path.exists(expanded_path):
        return dict(DEFAULT_CONFIG), expanded_path

    try:
        return load_config(expanded_path), expanded_path
    except Exception as exc:
        print(f"Failed to load config {expanded_path}: {exc}", file=sys.stderr)
        return dict(DEFAULT_CONFIG), expanded_path


def configure_logging(config: dict, debug: bool) -> None:
    """Configure logging with optional file output."""
    logging_cfg = config.get("logging", {})
    level = "DEBUG" if debug else logging_cfg.get("level", "INFO")
    write_file = bool(logging_cfg.get("file", False))
    log_dir = ensure_logging_dir(config) if write_file else None
    setup_logging(level=level, log_dir=log_dir)


class Fcitx5Backend:
    """Fcitx 5 Python 后端服务

    职责：
    1. 接收语音识别请求，调用 FunASRServer
    2. 管理远程/本地 SLM 的异步流式任务
    3. 通过 IPC 向 Fcitx 5 全局 module 返回增量事件
    """

    def __init__(self, config: dict | None = None):
        self.config = dict(config or DEFAULT_CONFIG)

        # 语音识别服务
        logger.info("正在初始化 FunASR 服务器...")
        self.asr_server = FunASRServer()
        asr_result = self.asr_server.initialize()
        if not asr_result['success']:
            logger.error("FunASR 初始化失败: %s", asr_result.get('error'))
            sys.exit(1)
        logger.info("FunASR 服务器初始化成功")

        self._asr_options = dict(self.config.get("asr", {}))
        self._asr_options["normalization"] = dict(
            self.config.get("normalization", {})
        )
        audio_value = self.config.get("audio", {})
        audio_cfg = dict(audio_value) if isinstance(audio_value, dict) else {}
        try:
            min_recording_ms = int(audio_cfg.get("min_recording_ms", 1000))
        except (TypeError, ValueError):
            min_recording_ms = 1000
        self._asr_options["min_audio_seconds"] = max(
            0, min(5000, min_recording_ms)
        ) / 1000.0
        self._slm_polisher = SLMPolisher(self.config.get("slm", {}))
        logger.info("SLM 长句润色: enabled=%s", self._slm_polisher.enabled)
        self._streaming_asr = StreamingASRProcess(
            self.config.get("asr_streaming", {})
        )
        logger.info(
            "FunASR 2-pass 实时预览: enabled=%s",
            self._streaming_asr.enabled,
        )
        slm_cfg = dict(self.config.get("slm", {}))
        self._slm_stream_idle_timeout_s = max(
            0.1,
            int(
                slm_cfg.get(
                    "stream_idle_timeout_ms",
                    self._slm_polisher.stream_idle_timeout_ms,
                )
            )
            / 1000.0,
        )


        # 标记运行状态
        self.running = True
        self._asr_lock = threading.Lock()
        self._stream_tasks: dict[str, StreamTask] = {}
        self._stream_tasks_lock = threading.Lock()
        self._edit_tasks: dict[str, EditTask] = {}
        self._edit_tasks_lock = threading.Lock()
        self._voice_edit_run_lock = threading.Lock()

        # 注册信号处理
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

    def _cleanup_socket_path(self, path: str) -> None:
        """安全删除旧 socket 文件（避免误删普通文件）"""
        if not os.path.exists(path):
            return

        try:
            st = os.lstat(path)
        except OSError as exc:
            logger.warning("检查旧 socket 失败: %s", exc)
            return

        if stat.S_ISSOCK(st.st_mode) or stat.S_ISLNK(st.st_mode):
            try:
                os.remove(path)
                logger.info("已移除旧 socket: %s", path)
            except OSError as exc:
                logger.warning("移除旧 socket 失败: %s", exc)
        else:
            raise RuntimeError(f"socket 路径已存在且不是 socket: {path}")

    def _signal_handler(self, signum, frame):
        """信号处理器"""
        logger.info("收到信号 %d，准备退出...", signum)
        self.running = False

    def run(self):
        """运行 IPC 服务器"""
        # 删除旧的 socket 文件
        self._cleanup_socket_path(SOCKET_PATH)

        # 创建 Unix Socket
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(SOCKET_PATH)
        os.chmod(SOCKET_PATH, 0o600)
        sock.listen(5)
        sock.settimeout(1.0)  # 设置超时以便处理信号

        logger.info("Fcitx5 Backend 已启动，监听: %s", SOCKET_PATH)

        try:
            while self.running:
                try:
                    conn, _ = sock.accept()
                    threading.Thread(
                        target=self.handle_client,
                        args=(conn,),
                        daemon=True,
                        name="Fcitx5BackendClient",
                    ).start()
                except socket.timeout:
                    continue
                except Exception as exc:
                    if self.running:
                        logger.error("接受连接失败: %s", exc)
        finally:
            sock.close()
            try:
                self._cleanup_socket_path(SOCKET_PATH)
            except RuntimeError as exc:
                logger.warning("清理 socket 失败: %s", exc)
            logger.info("Fcitx5 Backend 已停止")

    def _start_stream_task(
        self,
        audio_path: str,
        long_mode: bool,
        polish_min_chars: int = 0,
        polish_timeout_ms: int = 0,
        enable_thinking: bool | None = None,
    ) -> StreamTask:
        task = StreamTask(
            task_id=uuid.uuid4().hex,
            long_mode=long_mode,
            polish_min_chars=max(0, int(polish_min_chars or 0)),
            polish_timeout_ms=max(0, int(polish_timeout_ms or 0)),
            enable_thinking=enable_thinking,
        )
        with self._stream_tasks_lock:
            self._cleanup_stream_tasks_locked()
            self._stream_tasks[task.task_id] = task

        threading.Thread(
            target=self._run_stream_task,
            args=(task, audio_path),
            daemon=True,
            name=f"VoCoTypeStreamTask-{task.task_id[:8]}",
        ).start()
        return task

    def _get_stream_task(self, task_id: str) -> StreamTask | None:
        with self._stream_tasks_lock:
            self._cleanup_stream_tasks_locked()
            return self._stream_tasks.get(task_id)

    def _cleanup_stream_tasks_locked(self) -> None:
        now = time.monotonic()
        expired = [
            task_id
            for task_id, task in self._stream_tasks.items()
            if task.done_at is not None and now - task.done_at > TASK_TTL_S
        ]
        for task_id in expired:
            self._stream_tasks.pop(task_id, None)

    def _run_stream_task(self, task: StreamTask, audio_path: str) -> None:
        asr_ms = 0.0
        slm_start = 0.0
        slm_reason = "not_used"
        slm_used = False
        try:
            task.add_event("status", "识别中...")
            asr_start = time.perf_counter()
            with self._asr_lock:
                result = self.asr_server.transcribe_audio(
                    audio_path,
                    options=self._asr_options,
                )
            asr_ms = (time.perf_counter() - asr_start) * 1000.0

            if task.is_cancelled():
                return

            if not result.get("success"):
                task.mark_error(str(result.get("error", "转录失败")), "asr_failed")
                return

            text = str(result.get("text", "")).strip()
            task.set_original(text)
            if not text:
                task.mark_error("转录结果为空", "empty_asr_text")
                return

            should_polish = (
                task.long_mode
                and self._slm_polisher.should_polish(
                    text,
                    long_mode=True,
                    min_chars=task.polish_min_chars,
                )
            )
            if not should_polish:
                if not task.long_mode:
                    slm_reason = "not_long_mode"
                elif not self._slm_polisher.enabled:
                    slm_reason = "disabled"
                else:
                    slm_reason = "too_short"
                task.mark_final(text, slm_reason)
                return

            slm_used = True
            slm_start = time.perf_counter()
            task.set_phase("polishing")
            task.add_event("status", "正在润色...")
            got_final = False
            for event in self._slm_polisher.stream_polish(
                text,
                long_mode=True,
                min_chars=task.polish_min_chars,
                enable_thinking=task.enable_thinking,
            ):
                if task.is_cancelled():
                    return

                kind = str(event.get("kind", ""))
                if kind == "status":
                    task.add_event("status", str(event.get("text", "")))
                elif kind == "delta":
                    task.add_event(
                        "delta",
                        str(event.get("text", "")),
                        preview=str(event.get("preview", "")),
                    )
                elif kind == "heartbeat":
                    task.touch()
                elif kind == "final":
                    final_text = str(event.get("text", "")).strip()
                    if not final_text:
                        reason = "blank_content"
                        slm_reason = reason
                        task.mark_error(
                            self._slm_polisher.format_failure_message(reason),
                            reason,
                        )
                    else:
                        got_final = True
                        slm_reason = str(event.get("reason", "ok"))
                        task.mark_final(final_text, slm_reason)
                    return
                elif kind == "error":
                    reason = str(event.get("reason", "request_error"))
                    slm_reason = reason
                    message = str(
                        event.get("message")
                        or self._slm_polisher.format_failure_message(reason)
                    )
                    task.mark_error(message, reason)
                    return

            if not got_final and not task.is_cancelled():
                reason = "empty_content"
                slm_reason = reason
                task.mark_error(self._slm_polisher.format_failure_message(reason), reason)
        except Exception as exc:  # noqa: BLE001
            logger.error("流式转录任务失败: %s", exc)
            task.mark_error(str(exc), "exception")
        finally:
            try:
                os.remove(audio_path)
            except OSError:
                pass
            slm_ms = (time.perf_counter() - slm_start) * 1000.0 if slm_start else 0.0
            if task.long_mode:
                self._slm_polisher.release()
            logger.info(
                "Fcitx 流式转录流水线 mode=%s asr_ms=%.2f slm_used=%s slm_ms=%.2f reason=%s status=%s",
                "long" if task.long_mode else "normal",
                asr_ms,
                slm_used,
                slm_ms,
                slm_reason,
                task.status,
            )

    def _start_edit_task(self, request: dict) -> EditTask:
        task = EditTask(task_id=uuid.uuid4().hex)
        with self._edit_tasks_lock:
            self._cleanup_edit_tasks_locked()
            self._edit_tasks[task.task_id] = task
        threading.Thread(
            target=self._run_edit_task,
            args=(task, dict(request)),
            daemon=True,
            name=f"VoCoTypeEditTask-{task.task_id[:8]}",
        ).start()
        return task

    def _get_edit_task(self, task_id: str) -> EditTask | None:
        with self._edit_tasks_lock:
            self._cleanup_edit_tasks_locked()
            return self._edit_tasks.get(task_id)

    def _cleanup_edit_tasks_locked(self) -> None:
        now = time.monotonic()
        expired = [
            task_id
            for task_id, task in self._edit_tasks.items()
            if task.done_at is not None and now - task.done_at > TASK_TTL_S
        ]
        for task_id in expired:
            self._edit_tasks.pop(task_id, None)

    def _run_edit_task(self, task: EditTask, request: dict) -> None:
        audio_path = str(request.get("audio_path", "")).strip()
        try:
            result = self._edit_audio(request, on_instruction=task.set_instruction)
            if task.is_cancelled():
                return
            if result.get("success"):
                task.mark_final(result)
            else:
                task.mark_error(
                    str(result.get("error", "语音编辑失败")),
                    str(result.get("reason", "edit_failed")),
                    str(result.get("instruction", task.instruction)),
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception("语音编辑任务失败")
            task.mark_error(str(exc), "exception", task.instruction)
        finally:
            if audio_path:
                try:
                    os.remove(audio_path)
                except OSError:
                    pass

    def _edit_audio(
        self,
        request: dict,
        on_instruction=None,
    ) -> dict:
        audio_path = str(request.get("audio_path", "")).strip()
        snapshot = SurroundingSnapshot.from_mapping(request.get("snapshot", {}))
        raw_replace_state = request.get("replace_state", "unknown")
        replace_state = (
            raw_replace_state
            if isinstance(raw_replace_state, str) and raw_replace_state
            else "unknown"
        )
        supports_surrounding = bool(request.get("supports_surrounding", True))
        if not audio_path:
            return {"success": False, "error": "缺少 audio_path 参数"}

        with self._asr_lock:
            transcribed = self.asr_server.transcribe_audio(
                audio_path,
                options=self._asr_options,
            )
        if not transcribed.get("success"):
            return {
                "success": False,
                "error": str(transcribed.get("error", "编辑指令识别失败")),
            }
        instruction = str(transcribed.get("text", "")).strip()
        if not instruction:
            return {
                "success": False,
                "error": "未识别到编辑指令，请靠近麦克风后重试",
                "reason": "empty_instruction",
            }
        if on_instruction is not None:
            on_instruction(instruction)

        if not self._slm_polisher.enabled or not self._slm_polisher.edit_enabled:
            return {
                "success": False,
                "error": "语音编辑完全由 AI 理解，请先在设置中心启用并测活 AI 润色",
                "instruction": instruction,
                "reason": "edit_disabled",
            }

        with self._voice_edit_run_lock:
            plan, metrics = self._slm_polisher.plan_voice_edit(
                context_text=snapshot.text,
                instruction=instruction,
                cursor_pos=snapshot.cursor_pos,
                anchor_pos=snapshot.anchor_pos,
                selected_text=snapshot.selected_text,
                supports_surrounding=supports_surrounding,
                replace_state=replace_state,
            )
        if plan is None:
            return {
                "success": False,
                "error": self._slm_polisher.format_failure_message(metrics.reason),
                "instruction": instruction,
                "reason": metrics.reason,
            }

        payload = plan.to_dict()
        payload.update(
            {
                "success": True,
                "instruction": instruction,
                "expected_text": (
                    plan.new_text if plan.mode == "replace" else snapshot.text
                ),
                "reason": metrics.reason,
            }
        )
        return payload

    def _confirm_edit_applied(self, request: dict) -> dict:
        # Compatibility endpoint retained for older frontends. Undo/redo and
        # navigation are now planned by the SLM and executed by the host app,
        # so the backend no longer maintains a parallel command/history state.
        return {"success": True}

    def handle_client(self, conn: socket.socket):
        """处理客户端请求

        IPC 协议：
        - 请求格式：JSON 字符串
        - 响应格式：JSON 字符串

        请求类型：
        1. transcribe: 语音识别
           {"type": "transcribe", "audio_path": "/tmp/xxx.wav", "long_mode": false}
           -> {"success": true, "text": "识别结果"}

        1b. transcribe_start: 异步语音识别/润色任务
           {"type": "transcribe_start", "audio_path": "/tmp/xxx.wav", "long_mode": true, "polish_min_chars": 16, "polish_timeout_ms": 12000, "enable_thinking": false}
           -> {"success": true, "task_id": "...", "status": "running"}

        1c. polish_poll: 拉取异步任务事件
           {"type": "polish_poll", "task_id": "...", "after_seq": 0}
           -> {"success": true, "status": "running", "events": [...]}

        1d. polish_cancel: 取消异步任务
           {"type": "polish_cancel", "task_id": "..."}
           -> {"success": true}

        2. edit_start / edit_poll / edit_cancel: 异步共享语音编辑任务
        2b. edit_audio / edit_applied: 同步兼容路径与成功确认

        3. slm_prewarm / slm_release: 本地模型生命周期兼容接口

        4. ping: 健康检查
           {"type": "ping"}
           -> {"pong": true}
        """
        try:
            conn.settimeout(REQUEST_TIMEOUT_S)
            # 接收请求（读到 EOF）
            chunks = []
            total_bytes = 0
            while True:
                chunk = conn.recv(8192)
                if not chunk:
                    break
                chunks.append(chunk)
                total_bytes += len(chunk)
                if total_bytes > MAX_REQUEST_BYTES:
                    response_str = json.dumps({"error": "Request too large"}, ensure_ascii=False)
                    conn.sendall(response_str.encode('utf-8'))
                    return
            if not chunks:
                return
            data = b''.join(chunks).decode('utf-8')

            request = json.loads(data)
            req_type = request.get('type')

            logger.debug("收到请求: type=%s", req_type)

            # 处理请求
            if req_type == 'edit_start':
                if not str(request.get("audio_path", "")).strip():
                    response = {"success": False, "error": "缺少 audio_path 参数"}
                else:
                    task = self._start_edit_task(request)
                    response = {
                        "success": True,
                        "task_id": task.task_id,
                        "status": task.status,
                    }

            elif req_type == 'edit_poll':
                task_id = str(request.get("task_id", "")).strip()
                task = self._get_edit_task(task_id)
                if task is None:
                    response = {"success": False, "error": "编辑任务不存在或已过期"}
                else:
                    response = task.snapshot()

            elif req_type == 'edit_cancel':
                task_id = str(request.get("task_id", "")).strip()
                task = self._get_edit_task(task_id)
                if task is not None:
                    task.cancel()
                response = {"success": True}

            elif req_type == 'edit_audio':
                response = self._edit_audio(request)

            elif req_type == 'edit_applied':
                response = self._confirm_edit_applied(request)

            elif req_type == 'asr_preview_start':
                response = self._streaming_asr.start_session()

            elif req_type == 'asr_preview_feed':
                session_id = str(request.get('session_id', '')).strip()
                encoded = request.get('pcm16', '')
                if not session_id:
                    response = {"success": False, "error": "缺少 session_id 参数"}
                elif not isinstance(encoded, str):
                    response = {"success": False, "error": "pcm16 必须是 base64 字符串"}
                else:
                    try:
                        pcm = base64.b64decode(encoded, validate=True)
                    except Exception:
                        response = {"success": False, "error": "pcm16 base64 无效"}
                    else:
                        if len(pcm) > MAX_PREVIEW_CHUNK_BYTES:
                            response = {"success": False, "error": "实时音频块过大"}
                        elif len(pcm) % 2:
                            response = {"success": False, "error": "PCM16 字节数必须为偶数"}
                        else:
                            response = self._streaming_asr.feed(
                                session_id,
                                pcm,
                                is_final=bool(request.get('is_final', False)),
                            )

            elif req_type == 'asr_preview_close':
                session_id = str(request.get('session_id', '')).strip()
                if not session_id:
                    response = {"success": False, "error": "缺少 session_id 参数"}
                else:
                    response = self._streaming_asr.close_session(
                        session_id,
                        flush=bool(request.get('flush', False)),
                    )

            elif req_type == 'transcribe_start':
                audio_path = request.get('audio_path')
                long_mode = bool(request.get('long_mode', False))
                polish_min_chars = int(request.get("polish_min_chars", 0) or 0)
                polish_timeout_ms = int(request.get("polish_timeout_ms", 0) or 0)
                enable_thinking = (
                    bool(request["enable_thinking"])
                    if "enable_thinking" in request
                    else None
                )
                if not audio_path:
                    response = {"success": False, "error": "缺少 audio_path 参数"}
                else:
                    task = self._start_stream_task(
                        str(audio_path),
                        long_mode,
                        polish_min_chars,
                        polish_timeout_ms,
                        enable_thinking,
                    )
                    response = {
                        "success": True,
                        "task_id": task.task_id,
                        "status": task.status,
                    }

            elif req_type == 'polish_poll':
                task_id = str(request.get('task_id', '')).strip()
                after_seq = int(request.get('after_seq', 0) or 0)
                task = self._get_stream_task(task_id)
                if task is None:
                    response = {"success": False, "error": "任务不存在或已过期"}
                else:
                    response = task.snapshot(after_seq, self._slm_stream_idle_timeout_s)

            elif req_type == 'polish_cancel':
                task_id = str(request.get('task_id', '')).strip()
                task = self._get_stream_task(task_id)
                if task is not None:
                    task.cancel()
                response = {"success": True}

            elif req_type == 'transcribe':
                # Backwards-compatible synchronous path. The global module uses
                # this for plain ASR and transcribe_start for polishing.
                audio_path = request.get('audio_path')
                long_mode = bool(request.get('long_mode', False))
                if not audio_path:
                    response = {"success": False, "error": "缺少 audio_path 参数"}
                else:
                    try:
                        asr_start = time.perf_counter()
                        with self._asr_lock:
                            result = self.asr_server.transcribe_audio(
                                audio_path,
                                options=self._asr_options,
                            )
                        asr_ms = (time.perf_counter() - asr_start) * 1000.0
                        slm_reason = "not_used"
                        slm_ms = 0.0
                        if result.get("success") and long_mode:
                            original = str(result.get("text", "")).strip()
                            if self._slm_polisher.should_polish(
                                original,
                                long_mode=True,
                            ):
                                polished, metrics = self._slm_polisher.polish(
                                    original,
                                    long_mode=True,
                                )
                                slm_reason = metrics.reason
                                slm_ms = metrics.latency_ms
                                if self._slm_polisher.is_failure_reason(metrics.reason):
                                    result = {
                                        "success": False,
                                        "error": self._slm_polisher.format_failure_message(
                                            metrics.reason
                                        ),
                                        "original_text": original,
                                    }
                                else:
                                    result["text"] = polished
                            else:
                                slm_reason = (
                                    "disabled"
                                    if not self._slm_polisher.enabled
                                    else "too_short"
                                )
                        logger.info(
                            "Fcitx 同步转录流水线 mode=%s asr_ms=%.2f slm_ms=%.2f reason=%s",
                            "long" if long_mode else "plain",
                            asr_ms,
                            slm_ms,
                            slm_reason,
                        )
                        response = result
                    finally:
                        if long_mode:
                            self._slm_polisher.release()

            elif req_type == 'slm_prewarm':
                self._slm_polisher.prewarm(long_mode=True)
                response = {"success": True}

            elif req_type == 'slm_release':
                self._slm_polisher.release()
                response = {"success": True}

            elif req_type == 'ping':
                # 健康检查
                response = {"pong": True}

            else:
                response = {"error": f"未知的请求类型: {req_type}"}

            # 发送响应
            response_str = json.dumps(response, ensure_ascii=False)
            conn.sendall(response_str.encode('utf-8'))

            logger.debug("已发送响应: %d 字节", len(response_str))

        except json.JSONDecodeError as exc:
            logger.error("JSON 解析失败: %s", exc)
            try:
                error_response = json.dumps({"error": "Invalid JSON"})
                conn.sendall(error_response.encode('utf-8'))
            except Exception:
                pass

        except socket.timeout:
            logger.warning("IPC 请求读取超时")
            try:
                error_response = json.dumps({"error": "Request timeout"})
                conn.sendall(error_response.encode('utf-8'))
            except Exception:
                pass

        except Exception as exc:
            logger.error("处理请求失败: %s", exc)
            import traceback
            traceback.print_exc()
            try:
                error_response = json.dumps({"error": str(exc)})
                conn.sendall(error_response.encode('utf-8'))
            except Exception:
                pass

        finally:
            conn.close()

    def cleanup(self):
        """清理资源"""
        logger.info("正在清理资源...")
        try:
            self._streaming_asr.cleanup()
        except Exception as exc:
            logger.error("清理实时 ASR 资源失败: %s", exc)
        try:
            self.asr_server.cleanup()
        except Exception as exc:
            logger.error("清理资源失败: %s", exc)


def main():
    """主入口"""
    global SOCKET_PATH
    import argparse

    parser = argparse.ArgumentParser(
        description='VoCoType Fcitx5 Backend Server'
    )
    parser.add_argument(
        '--socket',
        default=SOCKET_PATH,
        help=f'Unix socket path (default: {SOCKET_PATH})'
    )
    parser.add_argument(
        '--debug',
        action='store_true',
        help='Enable debug logging'
    )
    args = parser.parse_args()

    config, config_path = load_backend_config()
    configure_logging(config, args.debug)
    logger.info("配置文件路径: %s", config_path)

    SOCKET_PATH = args.socket

    backend = Fcitx5Backend(config=config)
    try:
        backend.run()
    except KeyboardInterrupt:
        logger.info("收到 Ctrl+C，退出...")
    finally:
        backend.cleanup()


if __name__ == '__main__':
    main()
