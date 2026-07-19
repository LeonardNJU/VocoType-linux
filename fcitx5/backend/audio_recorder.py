#!/usr/bin/env python3
"""音频采集脚本

此脚本被 C++ Addon 通过 subprocess 调用，负责录制音频。

工作流程：
1. C++ Addon 启动此脚本，传入参数
2. 脚本开始录音
3. 脚本输出临时音频文件路径到 stdout
4. C++ Addon 读取路径，将其发送到 Backend 进行识别
"""
from __future__ import annotations

import sys
import argparse
import tempfile
import queue
import threading
import logging
from pathlib import Path

import numpy as np
import sounddevice as sd

# 添加项目根目录到 path，同时兼容仓库布局与安装后布局
def discover_project_root() -> Path:
    current = Path(__file__).resolve()
    candidates = [
        current.parent.parent,
        current.parent.parent.parent,
    ]
    for candidate in candidates:
        if (candidate / "app").is_dir():
            return candidate
    return current.parent.parent


PROJECT_ROOT = discover_project_root()
sys.path.insert(0, str(PROJECT_ROOT))

from app.audio_utils import (
    load_audio_config,
    resample_audio,
    resolve_default_input_device,
    SAMPLE_RATE,
)
from app.wave_writer import write_wav

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


def resolve_requested_sample_rate(
    cli_sample_rate: int | None,
    configured_sample_rate: int | None,
) -> int:
    """Resolve CLI/config/default sample rate without using a magic sentinel."""
    if cli_sample_rate is not None:
        return cli_sample_rate
    if configured_sample_rate:
        return configured_sample_rate
    return SAMPLE_RATE


class AudioRecorder:
    """音频录制器"""

    def __init__(self, device: int | str | None, sample_rate: int):
        self.device = device
        self.sample_rate = sample_rate
        self.audio_frames = []
        self.audio_queue = queue.Queue(maxsize=500)
        self.stop_event = threading.Event()
        self.stream = None

    def _resolve_input_device(self):
        """选择可用的输入设备"""
        if self.device is not None:
            try:
                info = sd.query_devices(self.device)
                if info.get("max_input_channels", 0) > 0:
                    return self.device
                logger.warning("设备 %s 无输入通道，回退选择输入设备", self.device)
            except Exception as exc:
                logger.warning("查询设备 %s 失败: %s", self.device, exc)

        return resolve_default_input_device()

    def _resolve_sample_rate(self, device, preferred):
        """选择可用采样率"""
        if preferred:
            try:
                sd.check_input_settings(
                    device=device,
                    samplerate=preferred,
                    channels=1,
                    dtype="int16",
                )
                return preferred
            except Exception:
                pass

        try:
            info = sd.query_devices(device if device is not None else None, kind="input")
            default_sr = int(info.get("default_samplerate", 0)) if info else 0
            if default_sr:
                sd.check_input_settings(
                    device=device,
                    samplerate=default_sr,
                    channels=1,
                    dtype="int16",
                )
                return default_sr
        except Exception:
            pass

        return preferred or SAMPLE_RATE

    def record(self, duration: float | None = None) -> Path:
        """录制音频

        Args:
            duration: 录制时长（秒），None 表示持续录制直到手动停止

        Returns:
            临时音频文件路径
        """
        device = self._resolve_input_device()
        sample_rate = self._resolve_sample_rate(device, self.sample_rate)

        logger.info("使用设备: %s, 采样率: %d Hz", device, sample_rate)

        block_ms = 20
        block_size = int(sample_rate * block_ms / 1000)

        def audio_callback(indata, frame_count, time_info, status):
            if status:
                logger.warning("音频状态: %s", status)
            try:
                self.audio_queue.put_nowait(indata.copy())
            except queue.Full:
                pass

        # 创建音频流
        self.stream = sd.InputStream(
            samplerate=sample_rate,
            blocksize=block_size,
            device=device,
            channels=1,
            dtype='int16',
            callback=audio_callback,
        )
        self.stream.start()

        # 采集线程
        def capture_loop():
            while not self.stop_event.is_set():
                try:
                    frame = self.audio_queue.get(timeout=0.1)
                    self.audio_frames.append(frame)
                except queue.Empty:
                    continue

        capture_thread = threading.Thread(target=capture_loop, daemon=True)
        capture_thread.start()

        logger.info("开始录音...")

        # 如果指定了时长，等待指定时间
        if duration:
            self.stop_event.wait(timeout=duration)
        else:
            # 否则等待 stdin 输入（C++ Addon 会发送停止信号）
            sys.stdin.read()

        # 停止录音
        self.stop_event.set()
        self.stream.stop()
        self.stream.close()
        capture_thread.join(timeout=1.0)

        logger.info("录音完成，共 %d 帧", len(self.audio_frames))

        # 合并音频
        if not self.audio_frames:
            logger.error("没有录制到音频数据")
            sys.exit(1)

        audio_data = np.concatenate(self.audio_frames).flatten()
        audio_duration = len(audio_data) / sample_rate
        logger.info("录音时长: %.2f 秒", audio_duration)

        # 检查是否太短
        if audio_duration < 0.3:
            logger.warning("录音时长过短（< 0.3 秒），可能无法识别")

        # 重采样到 16kHz（FunASR 要求）
        audio_16k = resample_audio(audio_data, sample_rate, SAMPLE_RATE)

        # 写入临时文件
        temp_file = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
        temp_path = Path(temp_file.name)
        temp_file.close()

        write_wav(temp_path, audio_16k.tobytes(), SAMPLE_RATE)
        logger.info("已保存到: %s", temp_path)

        return temp_path


def main():
    parser = argparse.ArgumentParser(description='VoCoType Audio Recorder')
    parser.add_argument(
        '--duration',
        type=float,
        help='Recording duration in seconds (default: wait for stdin)'
    )
    parser.add_argument(
        '--device',
        type=str,
        help='Audio device name or ID'
    )
    parser.add_argument(
        '--sample-rate',
        type=int,
        default=None,
        help='Sample rate (default: configured rate or 16000)'
    )
    args = parser.parse_args()

    # 加载配置
    configured_device, configured_sr = load_audio_config()
    device = args.device if args.device is not None else configured_device
    if isinstance(device, str) and device.isdigit():
        device = int(device)
    # Ask the sound server for 16 kHz directly when no rate was configured.
    # An explicit --sample-rate value, including 44100, must always win.
    sample_rate = resolve_requested_sample_rate(args.sample_rate, configured_sr)

    # 录音
    recorder = AudioRecorder(device, sample_rate)
    try:
        audio_path = recorder.record(duration=args.duration)
        # 输出文件路径到 stdout（C++ Addon 会读取此路径）
        print(audio_path, flush=True)
    except KeyboardInterrupt:
        logger.info("录音被中断")
        sys.exit(1)
    except Exception as exc:
        logger.error("录音失败: %s", exc)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
