"""Audio capture utilities built on sounddevice."""

from __future__ import annotations

import logging
import queue
import threading
from typing import Optional

import numpy as np
import sounddevice as sd

from app.audio_utils import resolve_default_input_device


logger = logging.getLogger(__name__)


class AudioCaptureError(RuntimeError):
    """Raised when the audio capture stream cannot be started."""


class AudioCapture:
    """Capture audio frames from the default (or configured) microphone."""

    def __init__(
        self,
        sample_rate: int,
        block_ms: int,
        device: int | str | None = None,
        queue_size: int = 200,
    ) -> None:
        self.sample_rate = sample_rate
        self.block_ms = block_ms
        self.device = device
        self._queue: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=queue_size)
        self._stream: Optional[sd.RawInputStream] = None
        self._lock = threading.Lock()
        self._running = False

        self._block_size = int(self.sample_rate * self.block_ms / 1000)
        if self._block_size <= 0:
            raise ValueError("block_ms too small for selected sample rate")

    @property
    def queue(self) -> "queue.Queue[np.ndarray]":
        return self._queue

    def start(self) -> None:
        with self._lock:
            if self._running:
                return

            self.flush()
            device = self.device if self.device is not None else resolve_default_input_device()
            self._stream = self._create_stream(device)
            try:
                self._stream.start()
            except Exception:
                self._stream.close()
                self._stream = self._create_stream(resolve_default_input_device())
                self._stream.start()

            self._running = True
            logger.info(
                "音频采集已启动，采样率=%sHz，块大小=%s样本，设备=%s",
                self.sample_rate,
                self._block_size,
                self._stream.device,
            )

    def stop(self) -> None:
        with self._lock:
            if not self._running:
                return

            assert self._stream is not None
            self._stream.stop()
            self._stream.close()
            self._stream = None
            self._running = False
            logger.info("音频采集已停止")

    def flush(self) -> None:
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

    def _create_stream(self, device: int | str | None) -> sd.RawInputStream:
        try:
            return sd.RawInputStream(
                samplerate=self.sample_rate,
                blocksize=self._block_size,
                dtype="int16",
                channels=1,
                callback=self._callback,
                device=device,
            )
        except Exception as exc:
            msg = f"无法创建音频输入流: {exc}"
            logger.error(msg)
            raise AudioCaptureError(msg) from exc

    def _callback(self, in_data, frames, time, status):  # type: ignore[override]
        if status:
            logger.warning("音频流状态: %s", status)

        frame = np.frombuffer(in_data, dtype=np.int16)
        try:
            self._queue.put_nowait(frame.copy())
        except queue.Full:
            logger.warning("音频队列已满，丢弃音频帧")


