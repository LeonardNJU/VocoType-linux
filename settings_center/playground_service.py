"""Reusable audio and AI helpers for the settings-center Playground."""

from __future__ import annotations

import hashlib
import json
import os
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import numpy as np

RECORDING_DURATION_SECONDS = 5.0
DEFAULT_BACKEND_SOCKET = Path("/tmp/vocotype-fcitx5.sock")


@dataclass(frozen=True)
class InputDevice:
    device_id: int
    name: str
    sample_rate: int
    channels: int


@dataclass(frozen=True)
class PlaygroundRecording:
    path: Path
    device_id: int
    device_name: str
    sample_rate: int
    frame_count: int
    duration_seconds: float
    peak: float
    rms: float


def playground_cache_dir(*, home: Path | None = None) -> Path:
    if home is not None:
        base = home / ".cache"
    else:
        base = Path(
            os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")
        ).expanduser()
    return base / "vocotype" / "playground"


def last_recording_path(*, home: Path | None = None) -> Path:
    return playground_cache_dir(home=home) / "last-recording.wav"


def list_input_devices() -> list[InputDevice]:
    import sounddevice as sd

    result: list[InputDevice] = []
    for index, item in enumerate(sd.query_devices()):
        channels = int(item.get("max_input_channels", 0))
        if channels <= 0:
            continue
        result.append(
            InputDevice(
                device_id=index,
                name=str(item.get("name", f"Device {index}")),
                sample_rate=max(
                    8000,
                    int(float(item.get("default_samplerate", 44100))),
                ),
                channels=channels,
            )
        )
    return result


def record_audio(
    *,
    device_id: int,
    device_name: str,
    sample_rate: int,
    duration_seconds: float = RECORDING_DURATION_SECONDS,
    output_path: Path | None = None,
) -> PlaygroundRecording:
    """Record a fixed-duration mono WAV for explicit playback and ASR tests."""

    import sounddevice as sd
    import soundfile as sf

    sample_rate = max(8000, int(sample_rate))
    duration_seconds = float(duration_seconds)
    if duration_seconds <= 0:
        raise ValueError("录音时长必须大于 0 秒")
    frame_count = max(1, int(round(sample_rate * duration_seconds)))
    frames = sd.rec(
        frame_count,
        samplerate=sample_rate,
        channels=1,
        dtype="float32",
        device=int(device_id),
    )
    sd.wait()
    samples = np.asarray(frames, dtype=np.float32).reshape(-1)
    if samples.size != frame_count:
        raise RuntimeError(
            f"录音帧数异常：期望 {frame_count}，实际 {samples.size}"
        )

    target = output_path or last_recording_path()
    target = Path(target).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp.wav")
    try:
        sf.write(temporary, samples, sample_rate, subtype="PCM_16")
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass

    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    rms = float(np.sqrt(np.mean(np.square(samples)))) if samples.size else 0.0
    return PlaygroundRecording(
        path=target,
        device_id=int(device_id),
        device_name=str(device_name),
        sample_rate=sample_rate,
        frame_count=frame_count,
        duration_seconds=samples.size / sample_rate,
        peak=peak,
        rms=rms,
    )


def play_recording(path: Path) -> float:
    """Play a WAV through the system default output device and wait for finish."""

    import sounddevice as sd
    import soundfile as sf

    source = Path(path).expanduser()
    if not source.is_file():
        raise FileNotFoundError(f"录音文件不存在：{source}")
    samples, sample_rate = sf.read(source, dtype="float32", always_2d=False)
    waveform = np.asarray(samples, dtype=np.float32)
    if waveform.size == 0:
        raise ValueError("录音文件没有音频数据")
    # Intentionally omit ``device``: playback must use the user's default
    # speaker/headphones, not the selected input device.
    sd.play(waveform, samplerate=int(sample_rate))
    sd.wait()
    return waveform.shape[0] / int(sample_rate)


def transcribe_recording(
    path: Path,
    *,
    socket_path: Path = DEFAULT_BACKEND_SOCKET,
    timeout_seconds: float = 120.0,
) -> dict[str, Any]:
    """Transcribe an existing recording through the installed Fcitx backend."""

    source = Path(path).expanduser()
    if not source.is_file():
        raise FileNotFoundError(f"录音文件不存在：{source}")
    endpoint = Path(socket_path)
    if not endpoint.is_socket():
        raise RuntimeError(
            "VoCoType ASR 后台未就绪；请先安装并启动 Fcitx 后台服务"
        )

    request = json.dumps(
        {
            "type": "transcribe",
            "audio_path": str(source),
            "long_mode": False,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(max(1.0, float(timeout_seconds)))
    chunks: list[bytes] = []
    try:
        client.connect(str(endpoint))
        client.sendall(request)
        client.shutdown(socket.SHUT_WR)
        while True:
            chunk = client.recv(8192)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        client.close()
    if not chunks:
        raise RuntimeError("ASR 后台未返回结果")
    response = json.loads(b"".join(chunks).decode("utf-8"))
    if not isinstance(response, dict):
        raise RuntimeError("ASR 后台返回格式无效")
    if not response.get("success"):
        raise RuntimeError(str(response.get("error") or "语音转录失败"))
    return response


def slm_config_fingerprint(config: Mapping[str, Any]) -> str:
    """Fingerprint endpoint/model choices without persisting credentials."""

    provider = str(config.get("provider", "remote")).strip().lower()
    payload = {
        "enabled": bool(config.get("enabled", False)),
        "provider": provider,
        "endpoint": str(config.get("endpoint", "")).strip(),
        "model": str(config.get("model", "")).strip(),
        "local_model": str(config.get("local_model", "")).strip(),
        "local_python": str(config.get("local_python", "")).strip(),
        "api_key_env": str(config.get("api_key_env", "")).strip(),
    }
    api_key_env = payload["api_key_env"]
    direct_api_key = str(config.get("api_key", "")).strip()
    effective_credential = direct_api_key or (
        str(os.environ.get(str(api_key_env), "")).strip() if api_key_env else ""
    )
    payload["credential_digest"] = (
        hashlib.sha256(effective_credential.encode("utf-8")).hexdigest()
        if effective_credential
        else ""
    )
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def slm_playground_gate(
    config: Mapping[str, Any],
    *,
    verified_fingerprint: str | None,
) -> tuple[bool, str]:
    """Explain whether AI Playground actions may use the current config."""

    if not bool(config.get("enabled", False)):
        return False, "请先在“AI 润色”页面打开 AI 润色功能。"
    provider = str(config.get("provider", "remote")).strip().lower()
    if provider in {"local", "ephemeral", "local_once", "local_ephemeral"}:
        local_model = str(config.get("local_model", "")).strip()
        model = str(config.get("model", "")).strip()
        if not local_model and not model:
            return False, "请先配置本地 AI 模型。"
        probe_name = "AI 模型测活"
    else:
        endpoint = str(config.get("endpoint", "")).strip()
        model = str(config.get("model", "")).strip()
        if not endpoint:
            return False, "请先配置 OpenAI-compatible AI 端点。"
        if not model:
            return False, "请先配置 AI 模型名称。"
        probe_name = "AI 端点测活"

    current = slm_config_fingerprint(config)
    if verified_fingerprint != current:
        return False, f"请先在“AI 润色”页面通过{probe_name}。"
    return True, "当前 AI 配置已在本次设置中心会话中测活，可以试用润色与编辑。"
