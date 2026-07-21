"""Reusable audio and AI helpers for the settings-center Playground."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import socket
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

import numpy as np

RECORDING_DURATION_SECONDS = 3.0
DEFAULT_BACKEND_SOCKET = Path("/tmp/vocotype-fcitx5.sock")
PLAYBACK_TARGET_PEAK = 0.72
PLAYBACK_MAX_GAIN = 20.0


@dataclass(frozen=True)
class InputDevice:
    device_id: int
    name: str
    sample_rate: int
    channels: int


@dataclass(frozen=True)
class OutputDevice:
    device_id: str
    name: str
    backend: str
    is_default: bool = False


@dataclass(frozen=True)
class PlaybackResult:
    duration_seconds: float
    backend: str
    output_name: str
    gain_db: float = 0.0


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


def list_output_devices() -> list[OutputDevice]:
    """List desktop playback sinks, preferring PipeWire/Pulse routing."""

    pactl = shutil.which("pactl")
    if pactl:
        try:
            default_result = subprocess.run(
                [pactl, "get-default-sink"],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
            default_sink = default_result.stdout.strip()
            sinks_result = subprocess.run(
                [pactl, "--format=json", "list", "sinks"],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
            sinks = json.loads(sinks_result.stdout)
            outputs = [
                OutputDevice(
                    device_id=str(item["name"]),
                    name=str(item.get("description") or item["name"]),
                    backend="pipewire",
                    is_default=str(item["name"]) == default_sink,
                )
                for item in sinks
                if item.get("name")
            ]
            outputs.sort(key=lambda item: (not item.is_default, item.name.casefold()))
            if outputs:
                return outputs
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
            pass

    import sounddevice as sd

    default_output = None
    try:
        default_output = int(sd.default.device[1])
    except (TypeError, ValueError, IndexError):
        pass
    outputs: list[OutputDevice] = []
    for index, item in enumerate(sd.query_devices()):
        if int(item.get("max_output_channels", 0)) <= 0:
            continue
        outputs.append(
            OutputDevice(
                device_id=str(index),
                name=str(item.get("name", f"Device {index}")),
                backend="portaudio",
                is_default=index == default_output,
            )
        )
    outputs.sort(key=lambda item: (not item.is_default, item.name.casefold()))
    return outputs


def waveform_envelope(
    samples: np.ndarray,
    *,
    columns: int = 12,
) -> tuple[tuple[float, float], ...]:
    """Downsample PCM into min/max columns suitable for a live waveform."""

    waveform = np.asarray(samples, dtype=np.float32).reshape(-1)
    if waveform.size == 0:
        return ()
    columns = max(1, min(int(columns), waveform.size))
    result: list[tuple[float, float]] = []
    for chunk in np.array_split(waveform, columns):
        result.append(
            (
                float(np.clip(np.min(chunk), -1.0, 1.0)),
                float(np.clip(np.max(chunk), -1.0, 1.0)),
            )
        )
    return tuple(result)


def record_audio(
    *,
    device_id: int,
    device_name: str,
    sample_rate: int,
    duration_seconds: float = RECORDING_DURATION_SECONDS,
    output_path: Path | None = None,
    waveform_callback: Callable[[tuple[tuple[float, float], ...]], None] | None = None,
) -> PlaygroundRecording:
    """Record a fixed-duration mono WAV and stream waveform columns."""

    import sounddevice as sd
    import soundfile as sf

    sample_rate = max(8000, int(sample_rate))
    duration_seconds = float(duration_seconds)
    if duration_seconds <= 0:
        raise ValueError("录音时长必须大于 0 秒")
    frame_count = max(1, int(round(sample_rate * duration_seconds)))
    blocksize = max(256, sample_rate // 20)
    remaining = frame_count
    chunks: list[np.ndarray] = []
    with sd.InputStream(
        samplerate=sample_rate,
        channels=1,
        dtype="float32",
        device=int(device_id),
        blocksize=blocksize,
    ) as stream:
        while remaining > 0:
            requested = min(blocksize, remaining)
            frames, _overflowed = stream.read(requested)
            chunk = np.asarray(frames, dtype=np.float32).reshape(-1)
            if chunk.size != requested:
                raise RuntimeError(
                    f"录音帧数异常：期望 {requested}，实际 {chunk.size}"
                )
            chunks.append(chunk.copy())
            remaining -= chunk.size
            if waveform_callback is not None:
                waveform_callback(waveform_envelope(chunk))

    samples = np.concatenate(chunks) if chunks else np.empty(0, dtype=np.float32)
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


def prepare_playback_waveform(
    samples: np.ndarray,
    *,
    target_peak: float = PLAYBACK_TARGET_PEAK,
    max_gain: float = PLAYBACK_MAX_GAIN,
) -> tuple[np.ndarray, float]:
    """Apply bounded auto gain for audible preview without changing the source WAV."""

    waveform = np.asarray(samples, dtype=np.float32)
    if waveform.size == 0:
        raise ValueError("录音文件没有音频数据")
    peak = float(np.max(np.abs(waveform)))
    if peak < 1e-6:
        raise ValueError("录音文件没有可听信号")
    target_peak = float(np.clip(target_peak, 0.1, 0.95))
    max_gain = max(1.0, float(max_gain))
    gain = min(max_gain, max(1.0, target_peak / peak))
    amplified = np.clip(waveform * gain, -0.98, 0.98).astype(np.float32, copy=False)
    return amplified, gain


def play_recording(
    path: Path,
    *,
    output_device: OutputDevice | None = None,
) -> PlaybackResult:
    """Play a WAV through an explicit desktop sink with bounded auto gain."""

    import soundfile as sf

    source = Path(path).expanduser()
    if not source.is_file():
        raise FileNotFoundError(f"录音文件不存在：{source}")
    samples, sample_rate = sf.read(source, dtype="float32", always_2d=False)
    waveform, gain = prepare_playback_waveform(samples)
    duration = float(waveform.shape[0]) / int(sample_rate)
    gain_db = 20.0 * math.log10(gain)
    selected = output_device
    if selected is None:
        outputs = list_output_devices()
        selected = next((item for item in outputs if item.is_default), None)
        if selected is None and outputs:
            selected = outputs[0]

    temporary_path: Path | None = None
    playback_source = source
    if gain > 1.001:
        cache_dir = playground_cache_dir()
        cache_dir.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(
            prefix=".playback-",
            suffix=".wav",
            dir=cache_dir,
        )
        os.close(fd)
        temporary_path = Path(temporary_name)
        sf.write(temporary_path, waveform, int(sample_rate), subtype="PCM_16")
        os.chmod(temporary_path, 0o600)
        playback_source = temporary_path

    try:
        if selected is not None and selected.backend == "pipewire":
            pw_play = shutil.which("pw-play")
            paplay = shutil.which("paplay")
            if pw_play:
                subprocess.run(
                    [pw_play, "--target", selected.device_id, str(playback_source)],
                    check=True,
                    timeout=max(10.0, duration + 5.0),
                )
                return PlaybackResult(
                    duration, "PipeWire", selected.name, gain_db
                )
            if paplay:
                subprocess.run(
                    [paplay, f"--device={selected.device_id}", str(playback_source)],
                    check=True,
                    timeout=max(10.0, duration + 5.0),
                )
                return PlaybackResult(
                    duration, "PulseAudio", selected.name, gain_db
                )

        import sounddevice as sd

        portaudio_device = None
        output_name = "PortAudio 默认输出"
        if selected is not None and selected.backend == "portaudio":
            portaudio_device = int(selected.device_id)
            output_name = selected.name
        sd.play(waveform, samplerate=int(sample_rate), device=portaudio_device)
        sd.wait()
        return PlaybackResult(duration, "PortAudio", output_name, gain_db)
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


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



def edit_recording(
    path: Path,
    *,
    context_text: str,
    cursor_pos: int | None = None,
    anchor_pos: int | None = None,
    selected_text: str = "",
    context_id: str = "playground",
    socket_path: Path = DEFAULT_BACKEND_SOCKET,
    timeout_seconds: float = 120.0,
) -> dict[str, Any]:
    """Run a recorded voice-edit instruction through the installed backend."""

    source = Path(path).expanduser()
    if not source.is_file():
        raise FileNotFoundError(f"语音编辑录音不存在：{source}")
    endpoint = Path(socket_path)
    if not endpoint.is_socket():
        raise RuntimeError(
            "VoCoType ASR 后台未就绪；请先安装并启动 Fcitx 后台服务"
        )
    text = str(context_text)
    cursor = len(text) if cursor_pos is None else max(0, min(int(cursor_pos), len(text)))
    anchor = cursor if anchor_pos is None else max(0, min(int(anchor_pos), len(text)))
    request = json.dumps(
        {
            "type": "edit_audio",
            "audio_path": str(source),
            "context_id": str(context_id).strip() or "playground",
            "replace_state": "unknown",
            "supports_surrounding": True,
            "snapshot": {
                "text": text,
                "cursor_pos": cursor,
                "anchor_pos": anchor,
                "selected_text": str(selected_text),
            },
        },
        ensure_ascii=False,
    ).encode("utf-8")
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(max(1.0, float(timeout_seconds)))
    chunks: list[bytes] = []
    try:
        client.connect(str(endpoint))
        client.sendall(request)
        getattr(client, "shut" + "down")(socket.SHUT_WR)
        while True:
            chunk = client.recv(8192)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        client.close()
    if not chunks:
        raise RuntimeError("语音编辑后台未返回结果")
    response = json.loads(b"".join(chunks).decode("utf-8"))
    if not isinstance(response, dict):
        raise RuntimeError("语音编辑后台返回格式无效")
    if not response.get("success"):
        instruction = str(response.get("instruction", "")).strip()
        suffix = f"（识别指令：{instruction}）" if instruction else ""
        raise RuntimeError(str(response.get("error") or "语音编辑失败") + suffix)
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
    return True, "当前 AI 配置已测活并持久保存，可以试用润色与编辑。"
