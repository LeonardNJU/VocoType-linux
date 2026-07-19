from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import threading

MODULE_PATH = Path(__file__).resolve().parents[1] / "app" / "slm_polisher.py"
SPEC = importlib.util.spec_from_file_location("vocotype_slm_polisher", MODULE_PATH)
assert SPEC and SPEC.loader
slm_polisher = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = slm_polisher
SPEC.loader.exec_module(slm_polisher)
SLMPolisher = slm_polisher.SLMPolisher


class _FakeResponse:
    def __init__(self, payload: dict):
        self._data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def read(self) -> bytes:
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_polisher_disabled_returns_original():
    polisher = SLMPolisher({"enabled": False})
    out, metrics = polisher.polish("测试文本", long_mode=True)
    assert out == "测试文本"
    assert metrics.used is False
    assert metrics.reason == "disabled"


def test_polisher_not_long_mode_returns_original():
    polisher = SLMPolisher({"enabled": True})
    out, metrics = polisher.polish("测试文本测试文本测试文本测试文本测试文本", long_mode=False)
    assert out.startswith("测试文本")
    assert metrics.used is False
    assert metrics.reason == "not_long_mode"


def test_local_provider_default_thinking_disabled():
    polisher = SLMPolisher({"enabled": True, "provider": "local_ephemeral"})
    assert polisher.enable_thinking is False


def test_local_provider_default_keepalive_enabled():
    polisher = SLMPolisher({"enabled": True, "provider": "local_ephemeral"})
    assert polisher.keepalive_ms == 60000


def test_local_provider_ignores_http_endpoint():
    polisher = SLMPolisher(
        {
            "enabled": True,
            "provider": "local_ephemeral",
            "endpoint": "http://127.0.0.1:18080/v1/chat/completions",
        }
    )
    assert polisher.endpoint == ""


def test_remote_provider_has_network_safe_defaults():
    polisher = SLMPolisher({"enabled": True, "provider": "remote"})
    assert polisher.timeout_ms == 20000
    assert polisher.max_tokens == 128


def test_local_ready_timeout_uses_request_timeout_ceiling():
    polisher = SLMPolisher(
        {
            "enabled": True,
            "provider": "local_ephemeral",
            "timeout_ms": 12000,
            "ready_wait_ms": 2000,
            "warmup_timeout_ms": 90000,
        }
    )
    assert polisher._local_ready_timeout_s() == 12.0


def test_local_ready_timeout_respects_short_request_timeout():
    polisher = SLMPolisher(
        {
            "enabled": True,
            "provider": "local_ephemeral",
            "timeout_ms": 1500,
            "ready_wait_ms": 2000,
            "warmup_timeout_ms": 90000,
        }
    )
    assert polisher._local_ready_timeout_s() == 1.5


def test_release_with_keepalive_delays_shutdown(monkeypatch):
    class _FakeProc:
        def poll(self):
            return None

    polisher = SLMPolisher(
        {
            "enabled": True,
            "provider": "local_ephemeral",
            "keepalive_ms": 30,
        }
    )
    polisher._worker_proc = _FakeProc()
    fired = threading.Event()

    def _fake_shutdown_locked():
        polisher._worker_proc = None
        fired.set()

    monkeypatch.setattr(polisher, "_shutdown_local_worker_locked", _fake_shutdown_locked)
    polisher.release()
    assert fired.wait(0.3)


def test_polisher_too_short_returns_original():
    polisher = SLMPolisher({"enabled": True, "min_chars": 20})
    out, metrics = polisher.polish("太短", long_mode=True)
    assert out == "太短"
    assert metrics.used is False
    assert metrics.reason == "too_short"


def test_polisher_success(monkeypatch):
    polisher = SLMPolisher(
        {
            "enabled": True,
            "min_chars": 1,
            "endpoint": "http://test.local",
        }
    )

    def _fake_urlopen(request, timeout):
        assert request.full_url == "http://test.local/v1/chat/completions"
        assert timeout > 0
        return _FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": "润色后的文本。",
                        }
                    }
                ]
            }
        )

    monkeypatch.setattr(slm_polisher.urllib.request, "urlopen", _fake_urlopen)
    out, metrics = polisher.polish("原始文本", long_mode=True)
    assert out == "润色后的文本。"
    assert metrics.used is True
    assert metrics.applied is True
    assert metrics.reason == "ok"


def test_polisher_remote_retry_without_proxy(monkeypatch):
    polisher = SLMPolisher(
        {
            "enabled": True,
            "min_chars": 1,
            "endpoint": "http://test.local",
            "retry_without_proxy": True,
        }
    )

    calls = []

    def _fake_open_remote_request(request, timeout_s, *, bypass_proxy):
        calls.append(bypass_proxy)
        if not bypass_proxy:
            raise slm_polisher.urllib.error.URLError("proxy failed")
        return _FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": "润色后的文本。",
                        }
                    }
                ]
            }
        )

    monkeypatch.setattr(polisher, "_open_remote_request", _fake_open_remote_request)
    out, metrics = polisher.polish("原始文本", long_mode=True)
    assert out == "润色后的文本。"
    assert metrics.used is True
    assert metrics.applied is True
    assert metrics.reason == "ok"
    assert calls == [False, True]


def test_polisher_bad_json_fallback(monkeypatch):
    polisher = SLMPolisher(
        {
            "enabled": True,
            "min_chars": 1,
            "endpoint": "http://test.local",
        }
    )

    class _BadResponse:
        def read(self):
            return b"not-json"

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(slm_polisher.urllib.request, "urlopen", lambda *args, **kwargs: _BadResponse())
    out, metrics = polisher.polish("原始文本", long_mode=True)
    assert out == "原始文本"
    assert metrics.used is True
    assert metrics.reason == "bad_json"


def test_polisher_local_retry_without_thinking(monkeypatch):
    polisher = SLMPolisher(
        {
            "enabled": True,
            "provider": "local_ephemeral",
            "model": "Qwen/Qwen3.5-0.8B",
            "local_model": "Qwen/Qwen3.5-0.8B",
            "min_chars": 1,
            "max_tokens": 512,
            "enable_thinking": True,
        }
    )

    monkeypatch.setattr(polisher, "_ensure_local_worker_ready", lambda timeout_s: (True, "ok"))
    monkeypatch.setattr(polisher, "_shutdown_local_worker", lambda: None)

    payloads = []

    def _fake_local_worker_request(payload, timeout_s):
        payloads.append(dict(payload))
        if len(payloads) == 1:
            return {"ok": False, "reason": "thinking_only"}
        return {"ok": True, "text": "润色后的文本。"}

    monkeypatch.setattr(polisher, "_local_worker_request", _fake_local_worker_request)

    out, metrics = polisher.polish("原始文本", long_mode=True)
    assert out == "润色后的文本。"
    assert metrics.used is True
    assert metrics.reason == "ok"
    assert len(payloads) == 2
    assert payloads[0]["enable_thinking"] is True
    assert payloads[0]["max_tokens"] == 96
    assert payloads[1]["enable_thinking"] is False
    assert payloads[1]["max_tokens"] == 512
    assert payloads[1]["temperature"] == 0.0


def test_is_failure_reason():
    assert SLMPolisher.is_failure_reason("ok") is False
    assert SLMPolisher.is_failure_reason("too_short") is False
    assert SLMPolisher.is_failure_reason("timeout") is True
    assert SLMPolisher.is_failure_reason("load_failed:No module named 'torch'") is True


def test_format_failure_message():
    assert SLMPolisher.format_failure_message("timeout") == "SLM 调用失败：请求超时"
    assert SLMPolisher.format_failure_message("load_failed:No module named 'torch'") == "SLM 调用失败：模型加载失败"


def test_strip_thinking_tag_block():
    text = "<think>分析过程</think>\n我今天去那家公司面试了，感觉还可以。"
    assert SLMPolisher._strip_thinking_content(text) == "我今天去那家公司面试了，感觉还可以。"


def test_strip_thinking_process_with_final_marker():
    text = (
        "Thinking Process:\n"
        "1. Fix punctuation.\n"
        "2. Keep meaning.\n\n"
        "Final Answer: 我今天去那家公司面试了，感觉还可以。"
    )
    assert SLMPolisher._strip_thinking_content(text) == "我今天去那家公司面试了，感觉还可以。"


def test_strip_thinking_only_returns_empty():
    text = "Thinking Process: The user asks for post-processing and punctuation fixes."
    assert SLMPolisher._strip_thinking_content(text) == ""


def test_normalize_remote_endpoint():
    assert (
        SLMPolisher._normalize_remote_endpoint("http://8.153.102.23:13001/")
        == "http://8.153.102.23:13001/v1/chat/completions"
    )
    assert (
        SLMPolisher._normalize_remote_endpoint("http://8.153.102.23:13001/v1")
        == "http://8.153.102.23:13001/v1/chat/completions"
    )
    assert (
        SLMPolisher._normalize_remote_endpoint("http://8.153.102.23:13001/v1/chat/completions")
        == "http://8.153.102.23:13001/v1/chat/completions"
    )
