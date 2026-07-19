"""SLM text polisher used by long-form voice mode."""

from __future__ import annotations

import json
import logging
import os
import re
import select
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


logger = logging.getLogger(__name__)


DEFAULT_SYSTEM_PROMPT = """你是中文语音转写文本的后处理器。

目标：在不改变原意、不新增事实的前提下，做最小必要修正，让文本通顺、自然、易读。

仅允许：
1. 补充/修改/删除标点
2. 调整断句与分句
3. 删除明显口头禅、重复词、无意义语气词
4. 修正明显同音/近音错词、漏字、多字
5. 原句明显不通顺时，做最小限度顺句

核心约束：
- 最小编辑：能不改就不改，能少改就少改
- 含义守恒：不新增事实、细节、观点、结论；不扩写、不解释、不总结
- 技术字符串保真：英文、缩写、模型名、版本号、路径、命令、参数、代码片段按原样优先保留
- 形式保真：技术标识中的大小写、数字、连字符(-)、斜杠(/)、下划线(_)、小数点(.)尽量不改写
- 技术词纠偏：若技术词存在明显转写偏差（同音/近形/单字符误差）且上下文可确定，可做最小字符级修正
- 混排保真：字母数字混合标识保持字母/数字角色，不把字母读音替换成数字或汉字
- 术语优先：若有多个近似写法，优先更常见的技术术语拼写
- 数字规范：默认保留阿拉伯数字，非固定汉语表达不要改成汉字
- 不确定时保留原样，避免误改

输出要求：只输出最终文本，不要任何说明。"""

DEFAULT_EDIT_SYSTEM_PROMPT = """你是中文输入框的语音编辑器。

你会收到：
1) 用户的语音编辑指令
2) 输入框当前全文
3) 光标与选区信息

你的任务：
- 严格根据用户指令编辑“输入框当前全文”
- 能少改就少改，不要无关改写
- 保留原有语种、标点风格、技术字符串（路径/命令/代码/版本号）除非用户明确要求修改
- 若指令与文本无关或无法执行，返回原文

输出要求：
- 只输出“编辑后的完整输入框文本”
- 不要解释、不要加前后缀、不要输出 JSON。"""


@dataclass
class PolisherMetrics:
    """Polisher runtime metrics for logging."""

    used: bool
    applied: bool
    latency_ms: float
    reason: str

    def to_log_dict(self) -> Dict[str, Any]:
        return {
            "used": self.used,
            "applied": self.applied,
            "latency_ms": round(self.latency_ms, 2),
            "reason": self.reason,
        }


class SLMPolisher:
    """Best-effort SLM polishing with timeout and fallback.

    Supported providers:
    - remote: HTTP endpoint (OpenAI-compatible chat/completions)
    - local_ephemeral: spawn local worker on demand, release after one polish
    """

    _global_request_lock = threading.Lock()
    PROVIDER_REMOTE = "remote"
    PROVIDER_LOCAL_EPHEMERAL = "local_ephemeral"
    _NON_FAILURE_REASONS = {
        "ok",
        "disabled",
        "edit_disabled",
        "not_long_mode",
        "too_short",
        "empty_instruction",
    }
    _THINKING_PREFIX_RE = re.compile(
        r"^\s*(?:thinking\s*process|thought\s*process|reasoning|analysis|chain\s*of\s*thought|思考过程|推理过程|分析过程)\s*[:：]",
        flags=re.IGNORECASE,
    )
    _FINAL_ANSWER_MARKER_RE = re.compile(
        r"(?:(?:^|\n)\s*)(?:final\s*answer|final\s*response|answer|最终答案|最终输出|润色结果|输出结果|输出)\s*[:：]",
        flags=re.IGNORECASE,
    )
    _REASONING_LINE_RE = re.compile(
        r"^\s*(?:"
        r"(?:thinking\s*process|thought\s*process|reasoning|analysis|chain\s*of\s*thought|let'?s\s+think|step\s*\d*)"
        r"|(?:思考过程|推理过程|分析过程|推理|分析|思路)"
        r"|(?:\d+[\.\)]\s+)"
        r"|(?:[-*]\s+)"
        r")",
        flags=re.IGNORECASE,
    )

    def __init__(self, config: Dict[str, Any] | None = None):
        cfg = dict(config or {})
        self.enabled = bool(cfg.get("enabled", False))
        provider = str(cfg.get("provider", self.PROVIDER_REMOTE)).strip().lower()
        if provider in {"local", "ephemeral", "local_once", "local_ephemeral"}:
            self.provider = self.PROVIDER_LOCAL_EPHEMERAL
        else:
            self.provider = self.PROVIDER_REMOTE

        endpoint = str(cfg.get("endpoint", "http://127.0.0.1:18080/v1/chat/completions"))
        if self.provider == self.PROVIDER_REMOTE:
            self.endpoint = self._normalize_remote_endpoint(endpoint)
        else:
            # local_ephemeral communicates with slm_local_worker over stdio.
            self.endpoint = ""
        self.model = str(cfg.get("model", "Qwen/Qwen3.5-0.8B"))
        default_timeout_ms = 12000 if self.provider == self.PROVIDER_LOCAL_EPHEMERAL else 20000
        default_max_tokens = 96 if self.provider == self.PROVIDER_LOCAL_EPHEMERAL else 128
        default_warmup_timeout_ms = (
            90000 if self.provider == self.PROVIDER_LOCAL_EPHEMERAL else 12000
        )
        self.timeout_ms = int(cfg.get("timeout_ms", default_timeout_ms))
        self.min_chars = max(1, int(cfg.get("min_chars", 8)))
        self.max_tokens = max(1, int(cfg.get("max_tokens", default_max_tokens)))
        self.temperature = float(cfg.get("temperature", 0.0))
        self.top_p = float(cfg.get("top_p", 0.9))
        self.top_k = int(cfg.get("top_k", 20))
        enable_thinking_cfg = cfg.get("enable_thinking")
        if enable_thinking_cfg is None:
            self.enable_thinking = False
        else:
            self.enable_thinking = bool(enable_thinking_cfg)
        self.api_key = str(cfg.get("api_key", "")).strip()
        self.system_prompt = str(cfg.get("system_prompt", DEFAULT_SYSTEM_PROMPT))
        self.edit_enabled = bool(cfg.get("edit_enabled", True))
        self.edit_system_prompt = str(
            cfg.get("edit_system_prompt", DEFAULT_EDIT_SYSTEM_PROMPT)
        )
        self.edit_max_tokens = max(
            self.max_tokens,
            int(cfg.get("edit_max_tokens", max(256, self.max_tokens))),
        )
        self.retry_without_proxy = bool(cfg.get("retry_without_proxy", True))

        # Local ephemeral worker options.
        self.local_model = str(cfg.get("local_model", self.model)).strip()
        self.local_python = os.path.expanduser(
            str(cfg.get("local_python", sys.executable)).strip() or sys.executable
        )
        self.local_device = str(cfg.get("local_device", "cpu")).strip() or "cpu"
        self.local_dtype = str(cfg.get("local_dtype", "auto")).strip() or "auto"
        self.warmup_timeout_ms = max(
            200,
            int(cfg.get("warmup_timeout_ms", default_warmup_timeout_ms)),
        )
        default_keepalive_ms = 60000 if self.provider == self.PROVIDER_LOCAL_EPHEMERAL else 0
        self.keepalive_ms = max(0, int(cfg.get("keepalive_ms", default_keepalive_ms)))
        default_ready_wait_ms = 2000 if self.provider == self.PROVIDER_LOCAL_EPHEMERAL else self.timeout_ms
        self.ready_wait_ms = max(
            50,
            int(cfg.get("ready_wait_ms", default_ready_wait_ms)),
        )

        self._worker_lock = threading.Lock()
        self._worker_proc: Optional[subprocess.Popen[str]] = None
        self._worker_ready = False
        self._release_timer: Optional[threading.Timer] = None

    def should_polish(self, text: str, *, long_mode: bool) -> bool:
        if not self.enabled or not long_mode:
            return False
        return len(text.strip()) >= self.min_chars

    def prewarm(self, *, long_mode: bool) -> None:
        """Best-effort prewarm for local_ephemeral provider.

        Key-down path should return quickly; only start worker process here and
        defer model-ready waiting to key-up polish stage.
        """
        if not self.enabled or not long_mode:
            return
        if self.provider != self.PROVIDER_LOCAL_EPHEMERAL:
            return
        ok, reason = self._start_local_worker_if_needed()
        if not ok:
            logger.info("SLM 预加载失败或跳过: %s", reason)

    def release(self) -> None:
        """Release local worker to free memory."""
        if self.provider == self.PROVIDER_LOCAL_EPHEMERAL:
            self._schedule_or_shutdown_local_worker()

    def polish(self, text: str, *, long_mode: bool) -> Tuple[str, PolisherMetrics]:
        """Return polished text; fallback to original text on any failure."""

        start = time.perf_counter()
        original = text or ""

        if not self.enabled:
            return original, PolisherMetrics(False, False, 0.0, "disabled")

        if not long_mode:
            return original, PolisherMetrics(False, False, 0.0, "not_long_mode")

        stripped = original.strip()
        if len(stripped) < self.min_chars:
            self.release()
            return original, PolisherMetrics(False, False, 0.0, "too_short")

        # Single-flight lock across all polisher instances in this process.
        with self._global_request_lock:
            if self.provider == self.PROVIDER_LOCAL_EPHEMERAL:
                return self._polish_local(original, stripped, start)
            return self._polish_remote(original, stripped, start)

    def edit_with_instruction(
        self,
        *,
        context_text: str,
        instruction: str,
        cursor_pos: int,
        anchor_pos: int,
        selected_text: str = "",
    ) -> Tuple[str, PolisherMetrics]:
        """Edit full context text according to a voice instruction."""

        start = time.perf_counter()
        original = context_text or ""

        if not self.enabled:
            return original, PolisherMetrics(False, False, 0.0, "disabled")
        if not self.edit_enabled:
            return original, PolisherMetrics(False, False, 0.0, "edit_disabled")

        normalized_instruction = (instruction or "").strip()
        if not normalized_instruction:
            return original, PolisherMetrics(False, False, 0.0, "empty_instruction")

        request_text = self._build_edit_request_text(
            context_text=original,
            instruction=normalized_instruction,
            cursor_pos=cursor_pos,
            anchor_pos=anchor_pos,
            selected_text=selected_text,
        )

        with self._global_request_lock:
            old_system_prompt = self.system_prompt
            old_max_tokens = self.max_tokens
            old_enable_thinking = self.enable_thinking
            try:
                self.system_prompt = self.edit_system_prompt
                self.max_tokens = self.edit_max_tokens
                self.enable_thinking = False
                if self.provider == self.PROVIDER_LOCAL_EPHEMERAL:
                    return self._polish_local(original, request_text, start)
                return self._polish_remote(original, request_text, start)
            finally:
                self.system_prompt = old_system_prompt
                self.max_tokens = old_max_tokens
                self.enable_thinking = old_enable_thinking

    @classmethod
    def is_failure_reason(cls, reason: str) -> bool:
        """Return whether the reason indicates a real SLM failure."""
        normalized = str(reason or "").strip()
        if normalized in cls._NON_FAILURE_REASONS:
            return False
        return True

    @staticmethod
    def format_failure_message(reason: str) -> str:
        """Format user-facing failure text for UI."""
        normalized = str(reason or "").strip()
        if not normalized:
            return "SLM 调用失败"
        if normalized == "edit_disabled":
            return "SLM 编辑未启用"
        if normalized == "timeout":
            return "SLM 调用失败：请求超时"
        if normalized == "request_error":
            return "SLM 调用失败：请求错误"
        if normalized == "bad_json":
            return "SLM 调用失败：响应解析失败"
        if normalized == "empty_content":
            return "SLM 调用失败：返回内容为空"
        if normalized == "blank_content":
            return "SLM 调用失败：润色结果为空"
        if normalized == "thinking_only":
            return "SLM 调用失败：仅返回思考内容"
        if normalized == "local_timeout":
            return "SLM 调用失败：本地推理超时"
        if normalized == "local_warmup_timeout":
            return "SLM 调用失败：模型未就绪（请延长按键时长后重试）"
        if normalized == "local_model_not_set":
            return "SLM 调用失败：未配置本地模型"
        if normalized == "local_python_not_found":
            return "SLM 调用失败：本地 Python 不可用"
        if normalized.startswith("load_failed:"):
            return "SLM 调用失败：模型加载失败"
        if normalized == "exception":
            return "SLM 调用失败：运行异常"
        return f"SLM 调用失败：{normalized}"

    @staticmethod
    def _build_edit_request_text(
        *,
        context_text: str,
        instruction: str,
        cursor_pos: int,
        anchor_pos: int,
        selected_text: str,
    ) -> str:
        selected = (selected_text or "").strip() or "(无选中文本)"
        return (
            f"用户指令：{instruction}\n"
            f"光标位置：{int(cursor_pos)}\n"
            f"锚点位置：{int(anchor_pos)}\n"
            f"选中文本：{selected}\n"
            "输入框全文：\n"
            f"{context_text}\n"
            "请直接输出编辑后的完整输入框文本。"
        )

    def _polish_remote(
        self,
        original: str,
        stripped: str,
        start: float,
    ) -> Tuple[str, PolisherMetrics]:
        try:
            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": f"原文：{stripped}\n输出："},
                ],
                "max_tokens": self.max_tokens,
                "temperature": self.temperature,
                "top_p": self.top_p,
                "stream": False,
            }
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"

            request = urllib.request.Request(
                self.endpoint,
                data=data,
                headers=headers,
                method="POST",
            )
            timeout_s = max(0.05, self.timeout_ms / 1000.0)
            with self._open_remote_request(request, timeout_s, bypass_proxy=False) as response:
                body = response.read().decode("utf-8")

            parsed = json.loads(body)
            content = self._extract_content(parsed)
            if not content:
                return self._fallback(
                    original,
                    start,
                    "empty_content",
                )

            polished = content.strip()
            if not polished:
                return self._fallback(
                    original,
                    start,
                    "blank_content",
                )

            return polished, PolisherMetrics(
                used=True,
                applied=(polished != original),
                latency_ms=(time.perf_counter() - start) * 1000.0,
                reason="ok",
            )
        except TimeoutError:
            return self._fallback(original, start, "timeout")
        except urllib.error.URLError as exc:
            if self.retry_without_proxy and not isinstance(exc.reason, TimeoutError):
                try:
                    timeout_s = max(0.05, self._remaining_timeout(start))
                    if timeout_s <= 0.0:
                        return self._fallback(original, start, "timeout")
                    retry_request = urllib.request.Request(
                        self.endpoint,
                        data=data,
                        headers=headers,
                        method="POST",
                    )
                    with self._open_remote_request(
                        retry_request,
                        timeout_s,
                        bypass_proxy=True,
                    ) as response:
                        body = response.read().decode("utf-8")
                    parsed = json.loads(body)
                    content = self._extract_content(parsed)
                    if not content:
                        return self._fallback(original, start, "empty_content")
                    polished = content.strip()
                    if not polished:
                        return self._fallback(original, start, "blank_content")
                    logger.info("SLM 远端请求已切换为直连重试并成功")
                    return polished, PolisherMetrics(
                        used=True,
                        applied=(polished != original),
                        latency_ms=(time.perf_counter() - start) * 1000.0,
                        reason="ok",
                    )
                except TimeoutError:
                    return self._fallback(original, start, "timeout")
                except urllib.error.URLError as retry_exc:
                    reason = (
                        "timeout"
                        if isinstance(retry_exc.reason, TimeoutError)
                        else "request_error"
                    )
                    return self._fallback(original, start, reason)
                except json.JSONDecodeError:
                    return self._fallback(original, start, "bad_json")
                except Exception as retry_exc:  # noqa: BLE001
                    logger.warning("SLM 直连重试失败: %s", retry_exc)
                    return self._fallback(original, start, "exception")
            reason = "timeout" if isinstance(exc.reason, TimeoutError) else "request_error"
            return self._fallback(original, start, reason)
        except json.JSONDecodeError:
            return self._fallback(original, start, "bad_json")
        except Exception as exc:  # noqa: BLE001
            logger.warning("SLM polish failed: %s", exc)
            return self._fallback(original, start, "exception")

    @staticmethod
    def _open_remote_request(
        request: urllib.request.Request,
        timeout_s: float,
        *,
        bypass_proxy: bool,
    ):
        if not bypass_proxy:
            return urllib.request.urlopen(request, timeout=timeout_s)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        return opener.open(request, timeout=timeout_s)

    def _polish_local(
        self,
        original: str,
        stripped: str,
        start: float,
    ) -> Tuple[str, PolisherMetrics]:
        try:
            ready_timeout_s = self._local_ready_timeout_s()
            ok, reason = self._ensure_local_worker_ready(timeout_s=ready_timeout_s)
            if not ok:
                return self._fallback(original, start, reason)

            # Warmup waiting and generation serve different phases; keep full
            # generation budget instead of consuming it by readiness wait.
            timeout_s = max(0.05, self.timeout_ms / 1000.0)
            request_payload = {
                "type": "polish",
                "text": stripped,
                "system_prompt": self.system_prompt,
                "max_tokens": self.max_tokens,
                "temperature": self.temperature,
                "top_p": self.top_p,
                "top_k": self.top_k,
                "enable_thinking": self.enable_thinking,
            }

            if self.enable_thinking:
                # Thinking may consume most tokens on tiny local models;
                # probe with smaller budget, then retry once with thinking disabled.
                request_payload["max_tokens"] = min(self.max_tokens, 96)

            response = self._local_worker_request(
                request_payload,
                timeout_s=timeout_s,
            )
            if (
                self.enable_thinking
                and (not bool(response.get("ok", False)))
                and str(response.get("reason", "")) == "thinking_only"
            ):
                retry_payload = dict(request_payload)
                retry_payload["enable_thinking"] = False
                retry_payload["max_tokens"] = self.max_tokens
                retry_payload["temperature"] = 0.0
                timeout_s = max(0.05, self.timeout_ms / 1000.0)
                response = self._local_worker_request(
                    retry_payload,
                    timeout_s=timeout_s,
                )
            if not bool(response.get("ok", False)):
                return self._fallback(
                    original,
                    start,
                    str(response.get("reason", "local_worker_error")),
                )

            polished = str(response.get("text", "")).strip()
            if not polished:
                return self._fallback(original, start, "blank_content")

            return polished, PolisherMetrics(
                used=True,
                applied=(polished != original),
                latency_ms=(time.perf_counter() - start) * 1000.0,
                reason="ok",
            )
        except TimeoutError:
            return self._fallback(original, start, "local_timeout")
        except json.JSONDecodeError:
            return self._fallback(original, start, "bad_json")
        except Exception as exc:  # noqa: BLE001
            logger.warning("SLM local polish failed: %s", exc)
            return self._fallback(original, start, "exception")
        finally:
            # Release on idle; allow fast reuse within keepalive window.
            self.release()

    def _local_ready_timeout_s(self) -> float:
        """Return ready-wait timeout for local worker.

        Prefer `ready_wait_ms` for responsiveness, but honor the larger warmup
        window when caller allows long local timeout.
        """
        ready_wait_s = max(0.05, self.ready_wait_ms / 1000.0)
        warmup_wait_s = max(0.05, self.warmup_timeout_ms / 1000.0)
        request_timeout_s = max(0.05, self.timeout_ms / 1000.0)
        return min(request_timeout_s, max(ready_wait_s, warmup_wait_s))

    def _remaining_timeout(self, start: float) -> float:
        remaining = (self.timeout_ms / 1000.0) - (time.perf_counter() - start)
        return max(0.0, remaining)

    def _schedule_or_shutdown_local_worker(self) -> None:
        with self._worker_lock:
            self._cancel_release_timer_locked()
            if self.keepalive_ms <= 0:
                self._shutdown_local_worker_locked()
                return

            proc = self._worker_proc
            if proc is None or proc.poll() is not None:
                return

            timer = threading.Timer(
                max(0.05, self.keepalive_ms / 1000.0),
                self._release_timer_fired,
            )
            timer.daemon = True
            self._release_timer = timer
            timer.start()

    def _release_timer_fired(self) -> None:
        with self._worker_lock:
            self._release_timer = None
            self._shutdown_local_worker_locked()

    def _cancel_release_timer_locked(self) -> None:
        timer = self._release_timer
        self._release_timer = None
        if timer is None:
            return
        try:
            timer.cancel()
        except Exception:
            pass

    def _start_local_worker_if_needed(self) -> Tuple[bool, str]:
        with self._worker_lock:
            self._cancel_release_timer_locked()
            proc = self._worker_proc
            if proc is not None and proc.poll() is None:
                return True, "ok"
            self._shutdown_local_worker_locked()
            if not self.local_model:
                return False, "local_model_not_set"

            worker_script = str(Path(__file__).with_name("slm_local_worker.py"))
            cmd = [
                self.local_python,
                worker_script,
                "--model",
                self.local_model,
                "--device",
                self.local_device,
                "--dtype",
                self.local_dtype,
            ]
            worker_env = os.environ.copy()
            worker_env.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
            worker_env.setdefault("TRANSFORMERS_VERBOSITY", "error")
            worker_env.setdefault("TOKENIZERS_PARALLELISM", "false")
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    encoding="utf-8",
                    bufsize=1,
                    cwd=str(Path(__file__).resolve().parent.parent),
                    env=worker_env,
                )
            except FileNotFoundError:
                return False, "local_python_not_found"
            except Exception as exc:  # noqa: BLE001
                logger.warning("启动本地 SLM worker 失败: %s", exc)
                return False, "local_worker_spawn_error"

            self._worker_proc = proc
            self._worker_ready = False
            return True, "ok"

    def _ensure_local_worker_ready(self, timeout_s: float) -> Tuple[bool, str]:
        ok, reason = self._start_local_worker_if_needed()
        if not ok:
            return False, reason

        with self._worker_lock:
            proc = self._worker_proc
            if proc is None or proc.poll() is not None:
                self._shutdown_local_worker_locked()
                return False, "local_worker_not_ready"
            if self._worker_ready:
                return True, "ok"

            try:
                ready_msg = self._read_worker_json_line_locked(proc, timeout_s)
            except TimeoutError:
                return False, "local_warmup_timeout"
            except Exception:  # noqa: BLE001
                self._shutdown_local_worker_locked()
                return False, "local_worker_init_error"

            if not isinstance(ready_msg, dict):
                self._shutdown_local_worker_locked()
                return False, "local_worker_bad_ready"
            if ready_msg.get("type") != "ready":
                reason = str(ready_msg.get("reason", "local_worker_not_ready"))
                self._shutdown_local_worker_locked()
                return False, reason
            if not bool(ready_msg.get("ok", False)):
                reason = str(ready_msg.get("reason", "local_worker_not_ready"))
                self._shutdown_local_worker_locked()
                return False, reason

            self._worker_ready = True
            return True, "ok"

    def _local_worker_request(self, payload: Dict[str, Any], timeout_s: float) -> Dict[str, Any]:
        with self._worker_lock:
            proc = self._worker_proc
            if proc is None or proc.poll() is not None or not self._worker_ready:
                raise RuntimeError("local_worker_not_ready")
            if proc.stdin is None or proc.stdout is None:
                raise RuntimeError("local_worker_pipe_unavailable")

            proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            proc.stdin.flush()
            return self._read_worker_json_line_locked(proc, timeout_s)

    @staticmethod
    def _read_worker_json_line_locked(proc: subprocess.Popen[str], timeout_s: float) -> Dict[str, Any]:
        if proc.stdout is None:
            raise RuntimeError("worker_stdout_unavailable")

        fd = proc.stdout.fileno()
        ready, _, _ = select.select([fd], [], [], timeout_s)
        if not ready:
            raise TimeoutError("local_worker_timeout")

        line = proc.stdout.readline()
        if not line:
            raise RuntimeError("local_worker_closed")
        return json.loads(line)

    def _shutdown_local_worker(self) -> None:
        with self._worker_lock:
            self._cancel_release_timer_locked()
            self._shutdown_local_worker_locked()

    def _shutdown_local_worker_locked(self) -> None:
        proc = self._worker_proc
        self._worker_proc = None
        self._worker_ready = False
        if proc is None:
            return

        if proc.poll() is None:
            try:
                if proc.stdin is not None:
                    proc.stdin.write('{"type":"exit"}\n')
                    proc.stdin.flush()
            except Exception:
                pass

            try:
                proc.wait(timeout=0.3)
            except Exception:
                try:
                    proc.terminate()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=0.5)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    try:
                        proc.wait(timeout=0.2)
                    except Exception:
                        pass

    def _fallback(
        self,
        original: str,
        start: float,
        reason: str,
    ) -> Tuple[str, PolisherMetrics]:
        return original, PolisherMetrics(
            used=True,
            applied=False,
            latency_ms=(time.perf_counter() - start) * 1000.0,
            reason=reason,
        )

    @staticmethod
    def _extract_content(payload: Dict[str, Any]) -> str:
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0] or {}
            message = first.get("message", {})
            content = message.get("content", "")
            if isinstance(content, str):
                return SLMPolisher._strip_thinking_content(content)

        output_text = payload.get("output_text")
        if isinstance(output_text, str):
            return SLMPolisher._strip_thinking_content(output_text)

        return ""

    @staticmethod
    def _normalize_remote_endpoint(endpoint: str) -> str:
        text = str(endpoint or "").strip()
        if not text:
            return "http://127.0.0.1:18080/v1/chat/completions"

        parsed = urllib.parse.urlparse(text)
        if not parsed.scheme or not parsed.netloc:
            return text

        path = parsed.path or ""
        stripped_path = path.rstrip("/")
        if stripped_path in {"", "/"}:
            path = "/v1/chat/completions"
        elif stripped_path == "/v1":
            path = "/v1/chat/completions"

        parsed = parsed._replace(path=path)
        return urllib.parse.urlunparse(parsed)

    @staticmethod
    def _strip_thinking_content(content: str) -> str:
        """Remove reasoning traces and keep final user-facing text only."""
        text = str(content or "")
        if not text:
            return ""

        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
        if "<think>" in text:
            text = text.split("<think>", 1)[0]
        text = text.strip()
        if not text:
            return ""

        marker_matches = list(SLMPolisher._FINAL_ANSWER_MARKER_RE.finditer(text))
        if marker_matches:
            candidate = text[marker_matches[-1].end() :].strip()
            if candidate:
                text = candidate
            else:
                return ""

        if not SLMPolisher._THINKING_PREFIX_RE.match(text):
            return text

        paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", text) if p.strip()]
        if len(paragraphs) >= 2:
            last_para = paragraphs[-1]
            if not SLMPolisher._is_reasoning_line(last_para):
                return last_para

        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        for line in reversed(lines):
            if SLMPolisher._is_reasoning_line(line):
                continue
            return line
        return ""

    @classmethod
    def _is_reasoning_line(cls, text: str) -> bool:
        return bool(cls._REASONING_LINE_RE.match(str(text or "").strip()))
