"""音频处理工具模块

提供音频配置加载和重采样等通用功能，供 IBus 和 Fcitx5 共享使用。
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

# 目标采样率（ASR 模型需要）
SAMPLE_RATE = 16000
# 默认原生采样率
DEFAULT_NATIVE_SAMPLE_RATE = 44100


def resolve_default_input_device() -> int | None:
    """挑选用户实际的默认麦克风。

    优先级：
      1. ALSA "default" / "pulse" 这两个由 PipeWire/PulseAudio 注入的虚拟
         PCM，跟随 wpctl/pavucontrol 选择的默认源。
      2. PortAudio 自己认定的 default (sd.default.device[0])。
      3. 第一个有输入通道的设备（兜底）。
    """
    import sounddevice as sd

    try:
        devices = list(sd.query_devices())
    except Exception as exc:
        logger.warning("查询音频设备列表失败: %s", exc)
        return None

    for preferred in ("default", "pulse"):
        for idx, info in enumerate(devices):
            if info.get("name") == preferred and info.get("max_input_channels", 0) > 0:
                logger.info("使用服务器虚拟设备 #%s (%s)", idx, preferred)
                return idx

    try:
        pa_default = sd.default.device[0]
        if pa_default is not None and pa_default >= 0:
            info = devices[pa_default]
            if info.get("max_input_channels", 0) > 0:
                logger.info(
                    "使用 PortAudio 默认设备 #%s (%s)",
                    pa_default,
                    info.get("name", "unknown"),
                )
                return pa_default
    except Exception:
        pass

    for idx, info in enumerate(devices):
        if info.get("max_input_channels", 0) > 0:
            logger.info("回退至输入设备 #%s (%s)", idx, info.get("name", "unknown"))
            return idx

    logger.warning("没有发现可用的音频输入设备")
    return None


def load_audio_config() -> tuple[int | str | None, int | None]:
    """从配置文件加载音频设备配置。

    Returns:
        (device, sample_rate): 没有配置文件时返回 (None, None)，让调用方使用
        服务器虚拟设备并直接请求 16 kHz；配置文件存在则按内容返回。
    """
    config_file = Path.home() / ".config" / "vocotype" / "audio.conf"
    if not config_file.exists():
        logger.info("未找到 %s，使用系统默认输入设备", config_file)
        return None, None

    try:
        import configparser
        config = configparser.ConfigParser()
        config.read(config_file)

        # 优先使用 device_name（更稳定），回退到 device_id（向后兼容）
        device_name = config.get('audio', 'device_name', fallback=None)
        if device_name:
            sample_rate = config.getint('audio', 'sample_rate', fallback=DEFAULT_NATIVE_SAMPLE_RATE)
            logger.info("从配置加载: 设备=%s, 采样率=%d", device_name, sample_rate)
            return device_name, sample_rate

        device_id = config.getint('audio', 'device_id', fallback=None)
        sample_rate = config.getint('audio', 'sample_rate', fallback=DEFAULT_NATIVE_SAMPLE_RATE)

        logger.info("从配置加载: 设备=%s, 采样率=%d", device_id, sample_rate)
        return device_id, sample_rate
    except Exception as e:
        logger.warning("读取音频配置失败: %s，使用默认设备", e)
        return None, None


def resample_audio(audio: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
    """重采样音频到目标采样率

    Args:
        audio: 原始音频数据
        orig_sr: 原始采样率
        target_sr: 目标采样率

    Returns:
        重采样后的音频数据
    """
    if orig_sr == target_sr:
        return audio

    import librosa

    float_audio = audio.astype(np.float32) / 32768.0
    resampled = librosa.resample(
        float_audio,
        orig_sr=orig_sr,
        target_sr=target_sr,
        res_type="soxr_hq",
    )
    return np.clip(resampled * 32768.0, -32768, 32767).astype(np.int16)
