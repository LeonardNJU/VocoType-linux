#!/usr/bin/env bash
# Shared, side-effect-free installer helpers used by both integrations.

get_python_version() {
    "$1" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null
}

write_slm_config_json() {
    local config_file="$1"
    local python_bin="$2"
    local enabled="$3"
    local provider="$4"
    local endpoint="$5"
    local model="$6"
    local local_model="$7"
    local local_python="$8"
    local timeout_ms="$9"
    local min_chars="${10}"
    local max_tokens="${11}"
    local warmup_timeout_ms="${12}"
    local enable_thinking="${13}"
    local api_key="${14}"

    "$python_bin" - "$config_file" "$enabled" "$provider" "$endpoint" "$model" "$local_model" "$local_python" "$timeout_ms" "$min_chars" "$max_tokens" "$warmup_timeout_ms" "$enable_thinking" "$api_key" << 'PY'
import json
import os
import sys
from typing import Any


def load_json(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


target = os.path.expanduser(sys.argv[1])
enabled = bool(int(sys.argv[2]))
provider = sys.argv[3]
endpoint = sys.argv[4]
model = sys.argv[5]
local_model = sys.argv[6]
local_python = sys.argv[7]
timeout_ms = int(sys.argv[8])
min_chars = int(sys.argv[9])
max_tokens = int(sys.argv[10])
warmup_timeout_ms = int(sys.argv[11])
enable_thinking = bool(int(sys.argv[12]))
api_key = sys.argv[13]

cfg = load_json(target)
slm = cfg.get("slm", {})
if not isinstance(slm, dict):
    slm = {}

slm.update(
    {
        "enabled": enabled,
        "provider": provider,
        "model": model,
        "local_model": local_model,
        "local_python": local_python,
        "timeout_ms": timeout_ms,
        "warmup_timeout_ms": warmup_timeout_ms,
        "min_chars": min_chars,
        "max_tokens": max_tokens,
        "enable_thinking": enable_thinking,
        "api_key": api_key,
    }
)
if provider == "remote":
    slm["endpoint"] = endpoint
    slm["remote_stream"] = True
    slm["stream_idle_timeout_ms"] = timeout_ms
    slm.setdefault("transport_timeout_ms", 0)
    slm.setdefault("remote_max_tokens", 0)
    slm.setdefault("extra_headers", {})
    slm.setdefault("extra_body", {})
    slm.pop("max_tokens", None)
else:
    slm.pop("endpoint", None)
    slm["max_tokens"] = max_tokens
cfg["slm"] = slm

os.makedirs(os.path.dirname(target), exist_ok=True)
with open(target, "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
    f.write("\n")
PY
}
