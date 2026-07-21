"""Configuration helpers for the speak-keyboard runtime."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional


DEFAULT_CONFIG: Dict[str, Any] = {
    "hotkeys": {
        "toggle": "f2",
        "ptt_key": "F9",
        "ptt_fallback_keycode": 67,
        "ptt_hold_threshold_ms": 0,
        "long_mode_modifier": "Shift",
    },
    "audio": {
        "sample_rate": 16000,
        "block_ms": 20,
        "device": None,
        # Recordings shorter than this are discarded before final ASR.
        "min_recording_ms": 1000,
        # 单次录音的最大大小（字节），默认20MB
        # 达到此限制后将自动停止录音并开始转录
        "max_session_bytes": 20 * 1024 * 1024,
    },
    "vad": {
        "start_threshold": 0.02,
        "stop_threshold": 0.01,
        "min_speech_ms": 300,
        "min_silence_ms": 200,
        "pad_ms": 200,
    },
    "asr": {
        "use_vad": False,
        "use_punc": True,
        "language": "zh",
        "hotword": "",
        "batch_size_s": 60.0,
    },
    "asr_streaming": {
        # Official FunASR 2-pass preview. Disabled by default; when enabled,
        # online output is UI-only and final commit still uses the offline ASR.
        "enabled": False,
        "model": "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online-onnx",
        "chunk_size": [5, 10, 5],
        "intra_op_num_threads": 1,
        # The existing VoCoType host lazily spawns a native local worker. Its
        # ONNX allocations are reclaimed by process exit without disturbing
        # the final offline ASR backend.
        "idle_timeout_s": 30,
        "session_idle_timeout_s": 15,
        "startup_timeout_s": 180,
        "request_timeout_s": 2,
    },
    "normalization": {
        # The settings center may disable all numeric/ITN rewriting while
        # terminology canonicalization remains active.
        "enabled": True,
        # Compact written styles requested by the product owner. Each can be
        # changed independently in the GUI and previewed before saving.
        "compact_dates": True,
        "compact_times": True,
        "compact_distances": True,
        "currency_symbols": True,
    },
    "slm": {
        "enabled": False,
        "endpoint": "http://127.0.0.1:18080/v1/chat/completions",
        "model": "Qwen/Qwen3.5-0.8B",
        "timeout_ms": 20000,
        "remote_stream": True,
        # 流式模式按最后一次模型事件重新计时；0 表示不限制远端输出 token。
        "stream_idle_timeout_ms": 20000,
        "transport_timeout_ms": 0,
        "remote_max_tokens": 0,
        "extra_headers": {},
        "extra_body": {},
        "min_chars": 8,
        "max_tokens": 128,
        "temperature": 0.0,
        "top_p": 0.9,
        "top_k": 20,
        "enable_thinking": False,
        "edit_enabled": True,
        "edit_max_tokens": 1024,
        "api_key": "",
        # Prefer an environment variable for credentials on shared machines.
        "api_key_env": "",
    },
    "feedback": {
        # The official project endpoint is compiled into the settings center.
        # These fields only enable an explicit self-hosted override.
        "use_custom_endpoint": False,
        "endpoint": "",
    },
    "output": {
        "dedupe": True,
        "max_history": 5,
        "min_chars": 1,
        "method": "auto",
        "append_newline": False,
    },
    "logging": {"dir": "logs", "level": "INFO"},
}


def _merge_dict(base: Dict[str, Any], overrides: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_dict(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path: Optional[str] = None) -> Dict[str, Any]:
    """Load configuration from JSON file if provided, otherwise defaults."""

    config = dict(DEFAULT_CONFIG)
    if not path:
        return config

    expanded_path = os.path.expanduser(path)
    if not os.path.exists(expanded_path):
        raise FileNotFoundError(f"Config file not found: {expanded_path}")

    with open(expanded_path, "r", encoding="utf-8") as f:
        overrides = json.load(f)

    return _merge_dict(config, overrides)


def ensure_logging_dir(config: Dict[str, Any]) -> str:
    """Ensure the logging directory exists and return its absolute path.
    
    日志目录相对于项目根目录（main.py 所在目录），而不是当前工作目录。
    这样即使从其他目录运行脚本，日志也能正确保存到项目目录下。
    """
    log_dir = config["logging"].get("dir", "logs")
    
    # 如果已经是绝对路径，直接使用
    if os.path.isabs(log_dir):
        pass
    else:
        # 相对路径：基于项目根目录（向上两级到达项目根目录）
        # app/config.py -> app/ -> 项目根目录
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        log_dir = os.path.join(project_root, log_dir)
    
    os.makedirs(log_dir, exist_ok=True)
    return log_dir
