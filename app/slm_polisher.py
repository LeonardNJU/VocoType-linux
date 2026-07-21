"""SLM text polisher used by long-form voice mode."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Iterator, Tuple

from app.voice_edit import VoiceEditPlan, VoiceEditPlanError


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

DEFAULT_EDIT_SYSTEM_PROMPT = """你是中文输入框的语音编辑规划器。

你会收到 ASR 识别出的用户指令、输入框全文、光标、锚点、选区和执行能力。ASR 指令可能包含同音词、近音词或错别字；必须结合输入框上下文推断用户真正指向的词和操作，不能机械做字面字符串匹配。

只允许输出一个严格 JSON 对象，不要 Markdown、解释或额外文本，也绝不能输出 null。

可用计划：
1. 修改正文：
{"mode":"replace","new_text":"编辑后的完整输入框全文","record_history":true,"hint":""}
2. 导航、选择、撤销、重做、复制、剪切、粘贴等按键动作：
{"mode":"key_actions","key_actions":[{"key":"left","modifiers":["ctrl"],"repeat":1}],"hint":""}
3. 无法安全执行或无需修改：
{"mode":"no_op","hint":"说明原因"}

key 只能是：left、right、up、down、home、end、pageup、pagedown、backspace、delete、enter、tab、escape、space、a、c、v、x、z。
modifiers 只能是：ctrl、shift、alt、super。repeat 必须是 1 到 100 的整数。

动作语义参考：
- 撤销通常是 ctrl+z，重做通常是 ctrl+shift+z；
- 上一个/下一个词通常是 ctrl+left / ctrl+right；加 shift 表示扩展选区；
- 行首/行尾通常是 home / end；若用户说的是“当前句首”而句首不等于行首，应根据全文和光标计算距离，用 left 的 repeat 精确移动，距离超过 100 时拆成多个动作；
- 全选、复制、剪切、粘贴通常是 ctrl+a / ctrl+c / ctrl+x / ctrl+v。
这些只是执行原语说明，必须由你结合用户自然语言、上下文与光标决定实际计划。

规则：
- 文本替换、删除、翻译、LaTeX 转换、生成评论等使用 replace，并返回完整全文。
- 光标移动、选区、撤销/重做等使用 key_actions；由你根据自然语言意图选择正确按键组合。
- 只做用户要求的最小修改，保留其余文本、格式、代码、路径和技术字符串。
- 如果 ASR 把目标词识别成同音词，优先依据上下文定位实际存在且语义合理的目标。
- 所有字符串字段缺省时写空字符串，不得写 null。
"""


def looks_like_api_key(value: str) -> bool:
    """Return True when a secret was likely pasted into the env-name field."""

    text = str(value or "").strip()
    return len(text) >= 20 and text.startswith(("sk-", "ds-"))


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

    VoCoType only calls an OpenAI-compatible chat/completions endpoint.
    Starting, warming, and stopping a local model server is outside this process.
    """

    _global_request_lock = threading.Lock()
    PROVIDER_OPENAI_COMPATIBLE = "openai_compatible"
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
        legacy_provider = str(cfg.get("provider", "")).strip().lower()
        self.legacy_local_provider = legacy_provider in {
            "local",
            "ephemeral",
            "local_once",
            "local_ephemeral",
        }
        self.enabled = bool(cfg.get("enabled", False)) and not self.legacy_local_provider
        self.provider = self.PROVIDER_OPENAI_COMPATIBLE
        self.endpoint = self._normalize_remote_endpoint(
            str(cfg.get("endpoint", "http://127.0.0.1:18080/v1/chat/completions"))
        )
        self.model = str(cfg.get("model", "Qwen/Qwen3.5-0.8B")).strip()
        self.timeout_ms = int(cfg.get("timeout_ms", 20000))
        self.min_chars = max(0, int(cfg.get("min_chars", 8)))
        self.max_tokens = max(1, int(cfg.get("max_tokens", 128)))
        self.temperature = float(cfg.get("temperature", 0.0))
        self.top_p = float(cfg.get("top_p", 0.9))
        self.top_k = int(cfg.get("top_k", 20))
        enable_thinking_cfg = cfg.get("enable_thinking")
        self.enable_thinking = bool(enable_thinking_cfg) if enable_thinking_cfg is not None else False
        self.api_key_env = str(cfg.get("api_key_env", "")).strip()
        self.api_key = str(cfg.get("api_key", "")).strip()
        self.credential_warning = ""
        if self.legacy_local_provider:
            self.credential_warning = (
                "旧版内置本地 SLM 已移除；请自行启动 OpenAI-compatible 服务，"
                "填写 API 端点和模型后重新启用。"
            )
        elif not self.api_key and looks_like_api_key(self.api_key_env):
            self.api_key = self.api_key_env
            self.api_key_env = ""
            self.credential_warning = "检测到 API Key 被误填为环境变量名，已按直接密钥使用。"
        elif not self.api_key and self.api_key_env:
            self.api_key = str(os.environ.get(self.api_key_env, "")).strip()
        self.system_prompt = str(cfg.get("system_prompt", DEFAULT_SYSTEM_PROMPT))
        self.edit_enabled = bool(cfg.get("edit_enabled", True))
        self.edit_system_prompt = DEFAULT_EDIT_SYSTEM_PROMPT
        self.edit_max_tokens = max(
            self.max_tokens,
            int(cfg.get("edit_max_tokens", max(1024, self.max_tokens))),
        )
        self.retry_without_proxy = bool(cfg.get("retry_without_proxy", True))
        self.remote_stream = bool(cfg.get("remote_stream", True))
        self.stream_idle_timeout_ms = max(
            1000,
            int(cfg.get("stream_idle_timeout_ms", self.timeout_ms)),
        )
        self.transport_timeout_ms = max(0, int(cfg.get("transport_timeout_ms", 0)))
        self.remote_max_tokens = max(0, int(cfg.get("remote_max_tokens", 0) or 0))
        extra_body = cfg.get("extra_body", {})
        self.extra_body = dict(extra_body) if isinstance(extra_body, dict) else {}
        extra_headers = cfg.get("extra_headers", cfg.get("headers", {}))
        self.extra_headers = dict(extra_headers) if isinstance(extra_headers, dict) else {}

    def should_polish(
        self,
        text: str,
        *,
        long_mode: bool,
        min_chars: int | None = None,
    ) -> bool:
        if not self.enabled or not long_mode:
            return False
        threshold = self.min_chars if min_chars is None else max(0, int(min_chars))
        return len(text.strip()) >= threshold


    def stream_polish(
        self,
        text: str,
        *,
        long_mode: bool,
        min_chars: int | None = None,
        enable_thinking: bool | None = None,
    ) -> Iterator[Dict[str, Any]]:
        """Yield normalized status/delta/final/error events.

        OpenAI-compatible endpoints use SSE when enabled and otherwise return
        a normal JSON completion; both paths expose one event contract.
        """

        original = text or ""
        if not self.enabled:
            yield {"kind": "final", "text": original, "reason": "disabled"}
            return
        if not long_mode:
            yield {"kind": "final", "text": original, "reason": "not_long_mode"}
            return

        stripped = original.strip()
        threshold = self.min_chars if min_chars is None else max(0, int(min_chars))
        if len(stripped) < threshold:
            yield {"kind": "final", "text": original, "reason": "too_short"}
            return

        start = time.perf_counter()
        with self._global_request_lock:
            if self.remote_stream:
                yield from self._stream_remote(
                    original,
                    stripped,
                    start,
                    enable_thinking=enable_thinking,
                )
                return

            yield {"kind": "status", "text": "正在调用大模型..."}
            old_enable_thinking = self.enable_thinking
            try:
                if enable_thinking is not None:
                    self.enable_thinking = bool(enable_thinking)
                polished, metrics = self._polish_remote(original, stripped, start)
            finally:
                self.enable_thinking = old_enable_thinking

        if self.is_failure_reason(metrics.reason):
            yield {
                "kind": "error",
                "reason": metrics.reason,
                "message": self.format_failure_message(metrics.reason),
                "latency_ms": metrics.latency_ms,
            }
            return
        yield {
            "kind": "final",
            "text": polished,
            "reason": metrics.reason,
            "latency_ms": metrics.latency_ms,
        }

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
            return original, PolisherMetrics(False, False, 0.0, "too_short")

        # Single-flight lock across all polisher instances in this process.
        with self._global_request_lock:
            if self.remote_stream:
                return self._polish_remote_streaming_final(
                    original,
                    stripped,
                    start,
                    enable_thinking=self.enable_thinking,
                )
            return self._polish_remote(original, stripped, start)

    def _request_edit_completion(
        self,
        *,
        original: str,
        request_text: str,
        start: float,
        token_budget: int | None = None,
    ) -> Tuple[str, PolisherMetrics]:
        with self._global_request_lock:
            old_system_prompt = self.system_prompt
            old_max_tokens = self.max_tokens
            old_remote_max_tokens = self.remote_max_tokens
            old_enable_thinking = self.enable_thinking
            try:
                self.system_prompt = self.edit_system_prompt
                edit_budget = max(
                    self.edit_max_tokens,
                    int(token_budget or self.edit_max_tokens),
                )
                self.max_tokens = edit_budget
                self.remote_max_tokens = edit_budget
                self.enable_thinking = False
                if self.remote_stream:
                    return self._polish_remote_streaming_final(
                        original,
                        request_text,
                        start,
                        enable_thinking=False,
                    )
                return self._polish_remote(original, request_text, start)
            finally:
                self.system_prompt = old_system_prompt
                self.max_tokens = old_max_tokens
                self.remote_max_tokens = old_remote_max_tokens
                self.enable_thinking = old_enable_thinking

    def plan_voice_edit(
        self,
        *,
        context_text: str,
        instruction: str,
        cursor_pos: int,
        anchor_pos: int,
        selected_text: str = "",
        supports_surrounding: bool = True,
        replace_state: str = "unknown",
    ) -> Tuple[VoiceEditPlan | None, PolisherMetrics]:
        """Ask the SLM to understand the command and return a validated plan."""

        start = time.perf_counter()
        original = context_text or ""
        if not self.enabled:
            return None, PolisherMetrics(False, False, 0.0, "disabled")
        if not self.edit_enabled:
            return None, PolisherMetrics(False, False, 0.0, "edit_disabled")

        normalized_instruction = (instruction or "").strip()
        if not normalized_instruction:
            return None, PolisherMetrics(False, False, 0.0, "empty_instruction")

        request_text = self._build_edit_plan_request_text(
            context_text=original,
            instruction=normalized_instruction,
            cursor_pos=cursor_pos,
            anchor_pos=anchor_pos,
            selected_text=selected_text,
            supports_surrounding=supports_surrounding,
            replace_state=replace_state,
        )
        # A replace plan contains the complete surrounding text. Scale the
        # completion budget with context length while keeping a bounded
        # ceiling for remote cost and local model safety.
        token_budget = min(8192, max(
            self.edit_max_tokens,
            len(original) * 2 + 256,
        ))
        raw_plan, metrics = self._request_edit_completion(
            original=original,
            request_text=request_text,
            start=start,
            token_budget=token_budget,
        )
        if self.is_failure_reason(metrics.reason):
            return None, metrics
        try:
            plan = VoiceEditPlan.from_model_output(
                raw_plan,
                original_text=original,
            )
        except VoiceEditPlanError as exc:
            logger.warning("SLM voice-edit plan rejected: %s; raw=%r", exc, raw_plan)
            return None, PolisherMetrics(
                used=True,
                applied=False,
                latency_ms=(time.perf_counter() - start) * 1000.0,
                reason="bad_edit_plan",
            )

        applied = (
            (plan.mode == "replace" and plan.new_text != original)
            or (plan.mode == "key_actions" and bool(plan.key_actions))
        )
        return plan, PolisherMetrics(
            used=True,
            applied=applied,
            latency_ms=metrics.latency_ms,
            reason="ok",
        )

    def edit_with_instruction(
        self,
        *,
        context_text: str,
        instruction: str,
        cursor_pos: int,
        anchor_pos: int,
        selected_text: str = "",
    ) -> Tuple[str, PolisherMetrics]:
        """Compatibility wrapper returning text for older callers."""

        plan, metrics = self.plan_voice_edit(
            context_text=context_text,
            instruction=instruction,
            cursor_pos=cursor_pos,
            anchor_pos=anchor_pos,
            selected_text=selected_text,
        )
        if plan is not None and plan.mode == "replace":
            return plan.new_text, metrics
        return context_text or "", metrics

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
        if normalized == "idle_timeout":
            return "SLM 调用失败：长时间未收到模型输出"
        if normalized == "bad_json":
            return "SLM 调用失败：响应解析失败"
        if normalized == "bad_edit_plan":
            return "SLM 调用失败：模型返回的编辑计划格式无效"
        if normalized == "remote_error":
            return "SLM 调用失败：远端服务返回错误（请查看日志）"
        if normalized == "empty_content":
            return "SLM 调用失败：返回内容为空"
        if normalized == "blank_content":
            return "SLM 调用失败：润色结果为空"
        if normalized == "thinking_only":
            return "SLM 调用失败：仅返回思考内容"
        if normalized == "exception":
            return "SLM 调用失败：运行异常"
        return f"SLM 调用失败：{normalized}"

    @staticmethod
    def _build_edit_plan_request_text(
        *,
        context_text: str,
        instruction: str,
        cursor_pos: int,
        anchor_pos: int,
        selected_text: str,
        supports_surrounding: bool,
        replace_state: str,
    ) -> str:
        selected = selected_text if isinstance(selected_text, str) else ""
        return (
            f"ASR 用户指令：{instruction}\n"
            f"surrounding 可用：{str(bool(supports_surrounding)).lower()}\n"
            f"全文替换能力：{replace_state or 'unknown'}\n"
            f"光标位置：{int(cursor_pos)}\n"
            f"锚点位置：{int(anchor_pos)}\n"
            f"选中文本：{selected}\n"
            "输入框全文：\n"
            f"{context_text}\n"
            "请结合全文消解 ASR 同音/近音错误，并只返回严格 JSON 编辑计划。"
        )

    def _build_remote_payload(
        self,
        stripped: str,
        *,
        stream: bool,
        enable_thinking: bool | None = None,
        max_tokens: int | None = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": f"原文：{stripped}\n输出："},
            ],
            "temperature": self.temperature,
            "top_p": self.top_p,
            "stream": stream,
        }
        token_limit = self.remote_max_tokens if max_tokens is None else max(0, int(max_tokens))
        if token_limit > 0:
            payload["max_tokens"] = token_limit
        extra_body = self._request_extra_body(enable_thinking=enable_thinking)
        payload.update(extra_body)
        return payload

    def _request_headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        for key, value in self.extra_headers.items():
            key_text = str(key).strip()
            value_text = str(value).strip()
            if key_text and value_text:
                headers[key_text] = value_text
        if "openrouter.ai" in self.endpoint.lower():
            headers.setdefault(
                "HTTP-Referer",
                "https://github.com/LeonardNJU/VocoType-linux",
            )
            headers.setdefault("X-Title", "VoCoType Linux")
        return headers

    def _request_extra_body(
        self,
        *,
        enable_thinking: bool | None = None,
    ) -> Dict[str, Any]:
        extra_body = dict(self.extra_body)
        thinking_enabled = (
            self.enable_thinking if enable_thinking is None else bool(enable_thinking)
        )
        if "openrouter.ai" in self.endpoint.lower():
            if thinking_enabled:
                extra_body.setdefault("reasoning", {"enabled": True})
            else:
                extra_body.setdefault(
                    "reasoning",
                    {"effort": "none", "exclude": True},
                )
                extra_body.setdefault("include_reasoning", False)
        return extra_body

    def _polish_remote_streaming_final(
        self,
        original: str,
        stripped: str,
        start: float,
        *,
        enable_thinking: bool | None,
    ) -> Tuple[str, PolisherMetrics]:
        """Consume remote SSE while exposing only the final result to callers."""

        for event in self._stream_remote(
            original,
            stripped,
            start,
            enable_thinking=enable_thinking,
        ):
            kind = str(event.get("kind", ""))
            if kind == "final":
                polished = str(event.get("text", "")).strip()
                reason = str(event.get("reason", "ok"))
                latency_ms = float(
                    event.get(
                        "latency_ms",
                        (time.perf_counter() - start) * 1000.0,
                    )
                )
                return polished, PolisherMetrics(
                    used=True,
                    applied=(polished != original),
                    latency_ms=latency_ms,
                    reason=reason,
                )
            if kind == "error":
                return self._fallback(
                    original,
                    start,
                    str(event.get("reason", "request_error")),
                )
        return self._fallback(original, start, "empty_content")

    def _stream_remote(
        self,
        original: str,
        stripped: str,
        start: float,
        *,
        enable_thinking: bool | None,
    ) -> Iterator[Dict[str, Any]]:
        yield {"kind": "status", "text": "正在调用大模型..."}
        emitted_visible = ""
        full_content = ""
        saw_event = False

        for bypass_proxy in (False, True):
            if bypass_proxy and (not self.retry_without_proxy or saw_event):
                break
            try:
                payload = self._build_remote_payload(
                    stripped,
                    stream=True,
                    enable_thinking=enable_thinking,
                )
                request = urllib.request.Request(
                    self.endpoint,
                    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    headers=self._request_headers(),
                    method="POST",
                )
                timeout_ms = (
                    self.transport_timeout_ms
                    if self.transport_timeout_ms > 0
                    else self.stream_idle_timeout_ms
                )
                timeout_s = max(0.05, timeout_ms / 1000.0)
                with self._open_remote_request(
                    request,
                    timeout_s,
                    bypass_proxy=bypass_proxy,
                ) as response:
                    for event_payload in self._iter_sse_payloads(response):
                        saw_event = True
                        remote_error = self._extract_remote_error(event_payload)
                        if remote_error:
                            code, message = remote_error
                            logger.warning(
                                "SLM 远端流式服务返回错误 code=%s: %s",
                                code,
                                message,
                            )
                            yield {
                                "kind": "error",
                                "reason": "remote_error",
                                "message": self.format_failure_message("remote_error"),
                            }
                            return

                        delta = self._extract_stream_delta(event_payload)
                        if not delta:
                            yield {"kind": "heartbeat"}
                            continue
                        full_content += delta
                        visible = self._stream_visible_content(full_content)
                        if not visible or visible == emitted_visible:
                            yield {"kind": "heartbeat"}
                            continue
                        delta_visible = (
                            visible[len(emitted_visible) :]
                            if visible.startswith(emitted_visible)
                            else visible
                        )
                        emitted_visible = visible
                        yield {
                            "kind": "delta",
                            "text": delta_visible,
                            "preview": visible,
                        }
                break
            except TimeoutError:
                yield {
                    "kind": "error",
                    "reason": "idle_timeout",
                    "message": self.format_failure_message("idle_timeout"),
                }
                return
            except urllib.error.HTTPError as exc:
                code = str(getattr(exc, "code", "http_error"))
                message = str(exc)
                try:
                    body = exc.read().decode("utf-8", errors="replace")
                    parsed = json.loads(body) if body.strip() else {}
                    remote_error = self._extract_remote_error(parsed)
                    if remote_error:
                        code, message = remote_error
                except Exception:  # noqa: BLE001 - retain HTTP status fallback.
                    pass
                logger.warning(
                    "SLM 远端流式 HTTP 错误 code=%s: %s",
                    code,
                    message,
                )
                yield {
                    "kind": "error",
                    "reason": "remote_error",
                    "message": self.format_failure_message("remote_error"),
                }
                return
            except urllib.error.URLError as exc:
                is_timeout = isinstance(exc.reason, TimeoutError)
                if is_timeout:
                    yield {
                        "kind": "error",
                        "reason": "idle_timeout",
                        "message": self.format_failure_message("idle_timeout"),
                    }
                    return
                if bypass_proxy or not self.retry_without_proxy or saw_event:
                    logger.warning("SLM 流式请求失败: %s", exc)
                    yield {
                        "kind": "error",
                        "reason": "request_error",
                        "message": self.format_failure_message("request_error"),
                    }
                    return
                logger.info("SLM 流式代理请求失败，尝试直连: %s", exc)
            except json.JSONDecodeError as exc:
                logger.warning("SLM 流式响应 JSON 解析失败: %s", exc)
                yield {
                    "kind": "error",
                    "reason": "bad_json",
                    "message": self.format_failure_message("bad_json"),
                }
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("SLM 流式请求异常: %s", exc)
                yield {
                    "kind": "error",
                    "reason": "exception",
                    "message": self.format_failure_message("exception"),
                }
                return

        polished = self._strip_thinking_content(full_content).strip()
        if not polished:
            reason = "thinking_only" if full_content.strip() else "empty_content"
            yield {
                "kind": "error",
                "reason": reason,
                "message": self.format_failure_message(reason),
                "latency_ms": (time.perf_counter() - start) * 1000.0,
            }
            return
        yield {
            "kind": "final",
            "text": polished,
            "reason": "ok",
            "latency_ms": (time.perf_counter() - start) * 1000.0,
        }

    @classmethod
    def _iter_sse_payloads(cls, response: Any) -> Iterator[Dict[str, Any]]:
        """Parse OpenAI-compatible SSE or a single JSON response."""

        content_type = ""
        headers = getattr(response, "headers", None)
        if headers is not None:
            try:
                content_type = str(headers.get("Content-Type", ""))
            except Exception:  # noqa: BLE001
                content_type = ""

        if (
            (content_type and "text/event-stream" not in content_type.lower())
            or not hasattr(response, "readline")
        ):
            body = response.read().decode("utf-8")
            if body.strip():
                yield json.loads(body)
            return

        data_lines: list[str] = []
        while True:
            raw_line = response.readline()
            if not raw_line:
                if data_lines:
                    payload_text = "\n".join(data_lines).strip()
                    if payload_text and payload_text != "[DONE]":
                        yield json.loads(payload_text)
                return
            line = (
                raw_line.decode("utf-8", errors="replace")
                if isinstance(raw_line, (bytes, bytearray))
                else str(raw_line)
            ).rstrip("\r\n")
            if not line:
                if not data_lines:
                    continue
                payload_text = "\n".join(data_lines).strip()
                data_lines.clear()
                if not payload_text:
                    continue
                if payload_text == "[DONE]":
                    return
                yield json.loads(payload_text)
                continue
            if line.startswith(":"):
                yield {"_heartbeat": True}
                continue
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())

    @classmethod
    def _extract_stream_delta(cls, payload: Dict[str, Any]) -> str:
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0] if isinstance(choices[0], dict) else {}
            for container in (
                first.get("delta"),
                first.get("message"),
                first,
            ):
                if not isinstance(container, dict):
                    continue
                content = cls._coerce_remote_text(container.get("content"))
                if content:
                    return content
                text = cls._coerce_remote_text(container.get("text"))
                if text:
                    return text
        return cls._coerce_remote_text(payload.get("content")) or cls._coerce_remote_text(
            payload.get("text")
        )

    @staticmethod
    def _coerce_remote_text(value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts: list[str] = []
            for item in value:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    text = item.get("text", "")
                    if isinstance(text, dict):
                        text = text.get("value", "")
                    if isinstance(text, str):
                        parts.append(text)
            return "".join(parts)
        return ""

    @classmethod
    def _stream_visible_content(cls, content: str) -> str:
        text = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
        if "<think>" in text:
            text = text.split("<think>", 1)[0]
        marker_matches = list(cls._FINAL_ANSWER_MARKER_RE.finditer(text))
        if marker_matches:
            return text[marker_matches[-1].end() :].strip()
        if cls._THINKING_PREFIX_RE.match(text):
            return ""
        return text.strip()

    def _polish_remote(
        self,
        original: str,
        stripped: str,
        start: float,
    ) -> Tuple[str, PolisherMetrics]:
        try:
            payload = self._build_remote_payload(
                stripped,
                stream=False,
                max_tokens=self.remote_max_tokens,
            )
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers = self._request_headers()

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
            remote_error = self._extract_remote_error(parsed)
            if remote_error:
                code, message = remote_error
                logger.warning("SLM 远端服务返回错误 code=%s: %s", code, message)
                return self._fallback(original, start, "remote_error")

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
                    remote_error = self._extract_remote_error(parsed)
                    if remote_error:
                        code, message = remote_error
                        logger.warning(
                            "SLM 远端服务直连重试返回错误 code=%s: %s",
                            code,
                            message,
                        )
                        return self._fallback(original, start, "remote_error")

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
    def _extract_remote_error(payload: Dict[str, Any]) -> tuple[str, str] | None:
        """Extract an OpenAI-compatible structured error from a JSON body."""
        error = payload.get("error")
        if isinstance(error, dict):
            code = str(error.get("code", payload.get("error_type", "unknown")))
            message = str(error.get("message", "remote provider error"))
            return code, message
        if isinstance(error, str) and error.strip():
            return str(payload.get("error_type", "unknown")), error.strip()
        return None

    @staticmethod
    def _extract_content(payload: Dict[str, Any]) -> str:
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0] or {}
            message = first.get("message", {})
            content = message.get("content", "") if isinstance(message, dict) else ""
            if isinstance(content, str):
                extracted = SLMPolisher._strip_thinking_content(content)
                if extracted:
                    return extracted
            if isinstance(content, list):
                text_parts = []
                for part in content:
                    if isinstance(part, str):
                        text_parts.append(part)
                        continue
                    if not isinstance(part, dict):
                        continue
                    text = part.get("text", "")
                    if isinstance(text, dict):
                        text = text.get("value", "")
                    if isinstance(text, str):
                        text_parts.append(text)
                if text_parts:
                    return SLMPolisher._strip_thinking_content("".join(text_parts))

            choice_text = first.get("text", "") if isinstance(first, dict) else ""
            if isinstance(choice_text, str):
                return SLMPolisher._strip_thinking_content(choice_text)

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
