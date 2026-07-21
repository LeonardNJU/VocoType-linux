#!/usr/bin/env python3
"""音频采集脚本

此脚本被 C++ Addon 通过 subprocess 调用，负责录制音频。

工作流程：
1. C++ Addon 启动此脚本，传入参数
2. 脚本始终保存完整录音；可选地把 600 ms PCM 副本送往在线模型
3. stdout 以 JSON-lines 输出可替换的 partial 预览
4. 结束时输出完整临时 WAV 路径，仍由离线模型生成最终结果
"""
from __future__ import annotations

import sys
import argparse
import base64
import json
import socket
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
from app.streaming_asr import StreamingAudioChunker

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


SOCKET_PATH = "/tmp/vocotype-fcitx5.sock"


def _backend_request(payload: dict, *, timeout: float = 2.0) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(SOCKET_PATH)
        client.sendall(data)
        client.shutdown(socket.SHUT_WR)
        chunks = []
        while True:
            chunk = client.recv(8192)
            if not chunk:
                break
            chunks.append(chunk)
    return json.loads(b"".join(chunks).decode("utf-8"))


class BackendPreviewClient:
    """Small per-recording client for the backend's shared online model."""

    def __init__(self, session_id: str, chunk_samples: int):
        self.session_id = session_id
        self.chunk_samples = chunk_samples
        self.closed = False

    @classmethod
    def start(cls) -> "BackendPreviewClient | None":
        try:
            response = _backend_request({"type": "asr_preview_start"}, timeout=12.0)
        except Exception as exc:  # noqa: BLE001
            logger.debug("实时预览后端不可用，继续普通录音: %s", exc)
            return None
        if not response.get("success"):
            logger.info("本次录音不启用实时预览: %s", response.get("error", "unknown"))
            return None
        return cls(
            str(response["session_id"]),
            max(1, int(response.get("chunk_samples", 9600))),
        )

    def feed(self, pcm: np.ndarray, *, is_final: bool = False) -> str:
        if self.closed:
            return ""
        payload = np.asarray(pcm, dtype="<i2").reshape(-1).tobytes()
        response = _backend_request(
            {
                "type": "asr_preview_feed",
                "session_id": self.session_id,
                "pcm16": base64.b64encode(payload).decode("ascii"),
                "is_final": is_final,
            }
        )
        if not response.get("success"):
            raise RuntimeError(str(response.get("error", "实时预览失败")))
        return str(response.get("text", ""))

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            _backend_request(
                {
                    "type": "asr_preview_close",
                    "session_id": self.session_id,
                    "flush": False,
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("关闭实时预览 session 失败: %s", exc)


def _emit_protocol_event(event_type: str, **payload) -> None:
    print(json.dumps({"type": event_type, **payload}, ensure_ascii=False), flush=True)


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
        self.preview_queue = queue.Queue(maxsize=100)
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

    def _drain_pending_frames(self) -> int:
        """Move callback frames still queued after stream shutdown into the capture."""
        drained = 0
        while True:
            try:
                frame = self.audio_queue.get_nowait()
                self.audio_frames.append(frame)
                try:
                    self.preview_queue.put_nowait(frame)
                except queue.Full:
                    pass
                drained += 1
            except queue.Empty:
                return drained

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

        # 采集线程始终优先保存完整录音。预览队列只是有界副本，
        # 在线 worker 的加载或失败绝不能阻塞录音控制路径。
        def capture_loop():
            while not self.stop_event.is_set():
                try:
                    frame = self.audio_queue.get(timeout=0.1)
                    self.audio_frames.append(frame)
                    try:
                        self.preview_queue.put_nowait(frame)
                    except queue.Full:
                        logger.debug("实时预览队列已满，丢弃预览块但保留最终录音")
                except queue.Empty:
                    continue

        capture_thread = threading.Thread(target=capture_loop, daemon=True)
        capture_thread.start()

        def preview_loop():
            preview_client = BackendPreviewClient.start()
            if preview_client is None:
                return
            try:
                if self.stop_event.is_set():
                    return
                preview_chunker = StreamingAudioChunker(
                    sample_rate, preview_client.chunk_samples
                )
                last_text = ""
                while not self.stop_event.is_set():
                    try:
                        frame = self.preview_queue.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    for chunk in preview_chunker.push(frame):
                        if self.stop_event.is_set():
                            break
                        text = preview_client.feed(chunk)
                        if text and text != last_text:
                            last_text = text
                            _emit_protocol_event("partial", text=text)
            except Exception as exc:  # noqa: BLE001
                logger.warning("实时预览已停用，本次最终识别不受影响: %s", exc)
            finally:
                preview_client.close()

        preview_thread = threading.Thread(
            target=preview_loop,
            daemon=True,
            name="VoCoTypeRecorderPreview",
        )
        preview_thread.start()

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

        # ``stop_event`` makes the consumer thread exit immediately. A final
        # callback may already have queued one or more blocks at that point,
        # especially while PipeWire/PortAudio is warming up on the first
        # recording. Preserve those blocks instead of silently shortening the
        # capture passed to FunASR.
        self._drain_pending_frames()
        preview_thread.join(timeout=0.15)
        if preview_thread.is_alive():
            logger.debug("实时预览仍在退出；不等待，优先执行最终离线识别")

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
        # JSON-lines protocol: partial events may precede this terminal path.
        _emit_protocol_event("audio", path=str(audio_path))
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
