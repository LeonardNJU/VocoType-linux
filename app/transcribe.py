"""Session-based transcription worker using FunASR once per recording."""

from __future__ import annotations

import logging
import os
import queue
import tempfile
import threading
import time
import itertools
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from .audio_capture import AudioCapture
from .config import ensure_logging_dir, load_config
from app.funasr_server import FunASRServer


logger = logging.getLogger(__name__)


@dataclass
class TranscriptionResult:
    text: str
    raw_text: str
    duration: float
    inference_latency: float
    confidence: float
    error: Optional[str] = None


class TranscriptionWorker:
    """Capture full session audio and transcribe once when stopped."""

    def __init__(
        self,
        config_path: Optional[str] = None,
        on_result: Optional[Callable[[TranscriptionResult], None]] = None,
    ) -> None:
        self.config = load_config(config_path)
        self.on_result = on_result
        self.log_dir = ensure_logging_dir(self.config)
        self.last_segment_path: Optional[Path] = None
        self._session_id_counter = itertools.count(1)
        self._current_session_id: Optional[int] = None

        audio_cfg = self.config["audio"]
        self.audio = AudioCapture(
            sample_rate=audio_cfg["sample_rate"],
            block_ms=audio_cfg["block_ms"],
            device=audio_cfg.get("device"),
        )

        self.fun_server = FunASRServer()
        init_result = self.fun_server.initialize()
        if not init_result.get("success"):
            raise RuntimeError(f"FunASR 初始化失败: {init_result}")

        self._running = threading.Event()
        self._recording = threading.Event()
        self._stop_requested = threading.Event()
        self._capture_thread: Optional[threading.Thread] = None
        self._state_lock = threading.RLock()
        self._audio_cfg = audio_cfg
        self._buffer: list[np.ndarray] = []
        self._buffer_lock = threading.Lock()
        # 单次会话大小限制（字节）与计数器（配置健壮性：转换为正整型，非法回退至20MB）
        try:
            raw_limit = audio_cfg.get("max_session_bytes", 20 * 1024 * 1024)
            self._max_session_bytes: int = int(raw_limit)
            if self._max_session_bytes <= 0:
                raise ValueError
        except Exception:
            self._max_session_bytes = 20 * 1024 * 1024
            logger.warning("max_session_bytes 配置非法，已回退至 20MB")
        self._session_bytes: int = 0
        
        # 异步转录队列和工作线程
        self._transcription_queue: "queue.Queue[Optional[np.ndarray]]" = queue.Queue(maxsize=10)
        self._transcription_thread: Optional[threading.Thread] = None
        self._transcription_running = threading.Event()
        self._transcription_active = threading.Event()
        self._transcription_task_count = 0  # 已提交的任务计数
        self._transcription_completed_count = 0  # 已完成的任务计数
        
        # 启动转录工作线程
        self._start_transcription_worker()

    def __del__(self) -> None:
        """析构函数，确保资源被清理"""
        try:
            self.cleanup()
        except Exception as exc:
            logger.debug("析构函数清理时出错: %s", exc)

    def cleanup(self) -> None:
        """清理所有资源，包括缓冲区和音频设备"""
        logger.debug("开始清理 TranscriptionWorker 资源")
        try:
            # 停止录音
            if self._running.is_set():
                self.stop()
            
            # 停止转录工作线程
            self._stop_transcription_worker()
            
            # 清理缓冲区
            with self._buffer_lock:
                self._buffer.clear()
            
            # 停止音频捕获
            if hasattr(self, 'audio'):
                self.audio.stop()
                
            logger.debug("TranscriptionWorker 资源清理完成")
        except Exception as exc:
            logger.error("清理资源时出错: %s", exc)

    def _start_transcription_worker(self) -> None:
        """启动转录工作线程"""
        if self._transcription_running.is_set():
            logger.debug("转录工作线程已在运行")
            return
        
        self._transcription_running.set()
        self._transcription_thread = threading.Thread(
            target=self._transcription_worker_loop,
            daemon=True,
            name="TranscriptionWorker"
        )
        self._transcription_thread.start()
        logger.info("转录工作线程已启动")

    def _stop_transcription_worker(self, timeout: float = 3.0) -> None:
        """停止转录工作线程，等待队列清空
        
        Args:
            timeout: 等待队列清空的超时时间（秒），默认3秒
        """
        if not self._transcription_running.is_set():
            logger.debug("转录工作线程未运行")
            return
        
        pending = self._transcription_queue.qsize()
        if pending > 0:
            logger.info(f"正在停止转录工作线程，队列中还有 {pending} 个任务，最多等待 {timeout} 秒...")
        else:
            logger.info("正在停止转录工作线程...")
        
        # 等待队列中的任务完成（最多等待timeout秒）
        start_time = time.time()
        while not self._transcription_queue.empty():
            elapsed = time.time() - start_time
            if elapsed > timeout:
                remaining = self._transcription_queue.qsize()
                logger.warning(f"等待超时（{timeout}秒），强制退出，丢弃 {remaining} 个未完成任务")
                break
            time.sleep(0.1)
        
        # 发送停止信号（None表示停止）
        self._transcription_running.clear()
        try:
            self._transcription_queue.put(None, timeout=0.5)
        except queue.Full:
            logger.warning("转录队列已满，无法发送停止信号")
        
        # 等待线程结束
        if self._transcription_thread and self._transcription_thread.is_alive():
            self._transcription_thread.join(timeout=2.0)
            if self._transcription_thread.is_alive():
                logger.warning("转录工作线程未能在2秒内结束，强制继续退出")
        
        self._transcription_thread = None
        logger.info(f"转录工作线程已停止，共完成 {self._transcription_completed_count}/{self._transcription_task_count} 个任务")

    def _transcription_worker_loop(self) -> None:
        """转录工作线程的主循环，从队列中获取音频并转录"""
        logger.info("转录工作线程开始运行")
        
        while self._transcription_running.is_set():
            try:
                # 从队列获取音频数据（阻塞等待，超时1秒）
                samples = self._transcription_queue.get(timeout=1.0)
            except queue.Empty:
                # 队列为空，继续等待
                continue

            # None是停止信号
            if samples is None:
                logger.debug("收到停止信号，转录工作线程退出")
                self._transcription_queue.task_done()
                break

            # 执行转录
            self._transcription_active.set()
            logger.info(
                "开始处理转录任务 #%s，队列剩余: %s",
                self._transcription_completed_count + 1,
                self._transcription_queue.qsize(),
            )
            try:
                self._transcribe_once(samples)
            except Exception as exc:
                logger.error("转录工作线程出错: %s", exc, exc_info=True)
            finally:
                self._transcription_active.clear()
                self._transcription_completed_count += 1
                self._transcription_queue.task_done()
        
        logger.info("转录工作线程已退出")

    def start(self) -> None:
        with self._state_lock:
            if self._running.is_set():
                logger.debug("Transcription worker 已在运行，忽略重复启动")
                return

            session_id = next(self._session_id_counter)
            logger.info("Transcription worker starting (session_id=%s)", session_id)
            self._running.set()
            self._stop_requested.clear()
            with self._buffer_lock:
                self._buffer.clear()
                self._session_bytes = 0
            self.audio.start()
            self._recording.set()
            self._capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
            self._capture_thread.start()
            self._current_session_id = session_id

    def stop(self, _from_capture_thread: bool = False) -> None:
        """停止录音并提交转录任务
        
        Args:
            _from_capture_thread: 内部参数，标识是否从capture线程调用（避免死锁）
        """
        # 第一阶段：在锁内快速更新状态并保存资源引用
        with self._state_lock:
            if not self._running.is_set():
                logger.debug("Transcription worker 未运行，忽略 stop")
                return

            session_id = self._current_session_id
            reason = "size_limit" if self._session_bytes >= self._max_session_bytes else "user"
            logger.info("Transcription worker stopping (session_id=%s, reason=%s)", session_id, reason)
            self._stop_requested.set()
            self._running.clear()
            self._recording.clear()
            
            # 保存当前会话的线程引用，避免操作到新会话的线程
            capture_thread_to_join = self._capture_thread
            # 清空线程引用，允许新会话创建新线程
            self._capture_thread = None
        
        # 第二阶段：在锁外执行耗时操作
        self.audio.stop()
        
        # 只有从外部调用时才join capture线程，避免自己join自己
        # 使用保存的线程引用，而不是self._capture_thread
        if not _from_capture_thread:
            if capture_thread_to_join and capture_thread_to_join.is_alive():
                capture_thread_to_join.join(timeout=5)

        combined = self._combine_buffer()
        self.audio.flush()

        if combined is None or combined.size == 0:
            logger.warning("未捕获到任何音频样本，跳过转写 (session_id=%s)", session_id)
            with self._state_lock:
                self._current_session_id = None
            return

        # 将音频数据提交到转录队列，立即返回（异步处理）
        try:
            self._transcription_queue.put_nowait(combined)
            # 更新计数器时需要锁保护
            with self._state_lock:
                self._transcription_task_count += 1
                task_count = self._transcription_task_count
            logger.info(
                "录音已提交到转录队列（session_id=%s，任务 #%s），队列中有 %s 个待处理任务",
                session_id,
                task_count,
                self._transcription_queue.qsize(),
            )
        except queue.Full:
            logger.error("转录队列已满，无法提交新任务 (session_id=%s)！请等待当前转录完成。", session_id)
            # 即使队列满了，也不阻塞用户，只是记录错误
        
        # 最后清理session_id
        with self._state_lock:
            self._current_session_id = None

    def _capture_loop(self) -> None:
        queue_obj = self.audio.queue
        while self._recording.is_set():
            try:
                frame = queue_obj.get(timeout=0.2)
            except Exception:
                if not self._recording.is_set():
                    break
                continue

            try:
                with self._buffer_lock:
                    if isinstance(frame, np.ndarray):
                        self._buffer.append(frame)
                        bytes_added = frame.nbytes
                    else:
                        arr = np.frombuffer(frame, dtype=np.int16)
                        self._buffer.append(arr)
                        bytes_added = arr.nbytes
                    self._session_bytes += bytes_added
            except Exception as exc:
                logger.error("处理音频帧时出错: %s", exc)

            # 达到单次会话大小上限后，自动停止录音
            if self._session_bytes >= self._max_session_bytes and not self._stop_requested.is_set():
                logger.warning(
                    "单次录音大小达到上限，自动停止（%s/%s 字节，%.2f/%.2f MB）",
                    self._session_bytes,
                    self._max_session_bytes,
                    self._session_bytes / (1024 * 1024),
                    self._max_session_bytes / (1024 * 1024),
                )
                # 从capture线程调用stop，传入标志避免死锁
                self.stop(_from_capture_thread=True)
                break  # 停止后立即退出循环

        with self._buffer_lock:
            frame_count = len(self._buffer)
        logger.debug("capture loop exiting, collected %s frames", frame_count)

    def _combine_buffer(self) -> Optional[np.ndarray]:
        with self._buffer_lock:
            if not self._buffer:
                return None
            try:
                combined = np.concatenate(self._buffer, axis=0)
                logger.info("会话录音合并完成，总样本数=%s", combined.size)
                self._buffer.clear()
                return combined
            except Exception as exc:
                logger.error("合并音频缓冲区时出错: %s", exc)
                self._buffer.clear()  # 即使出错也清理缓冲区
                return None

    def _write_temp_wav(self, samples: np.ndarray) -> str:
        import wave

        sample_rate = self._audio_cfg["sample_rate"]
        recent_path = Path(self.log_dir) / "recent.wav"
        os.makedirs(recent_path.parent, exist_ok=True)
        tmp_recent_fd, tmp_recent_path = tempfile.mkstemp(prefix="recent_", suffix=".wav", dir=recent_path.parent)
        os.close(tmp_recent_fd)
        with wave.open(str(tmp_recent_path), "wb") as wf_recent:
            wf_recent.setnchannels(1)
            wf_recent.setsampwidth(2)
            wf_recent.setframerate(sample_rate)
            wf_recent.writeframes(samples.tobytes())
        os.replace(tmp_recent_path, recent_path)
        self.last_segment_path = recent_path

        fd, path = tempfile.mkstemp(prefix="asr_session_", suffix=".wav")
        os.close(fd)
        with wave.open(path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(samples.tobytes())

        return path

    def _transcribe_once(self, samples: np.ndarray) -> None:
        tmp_path = self._write_temp_wav(samples)
        start = time.time()
        try:
            asr_options = dict(self.config.get("asr", {}))
            asr_options["normalization"] = dict(
                self.config.get("normalization", {})
            )
            asr_result = self.fun_server.transcribe_audio(
                tmp_path,
                options=asr_options,
            )
        finally:
            inference_latency = time.time() - start
            try:
                os.remove(tmp_path)
            except OSError:
                logger.debug("删除临时文件失败: %s", tmp_path)


        if not asr_result.get("success"):
            result = TranscriptionResult(
                text="",
                raw_text="",
                duration=0.0,
                inference_latency=inference_latency,
                confidence=0.0,
                error=asr_result.get("error", "unknown"),
            )
        else:
            final_text = asr_result.get("text", "")
            raw_text = asr_result.get("raw_text", "")

            result = TranscriptionResult(
                text=final_text,
                raw_text=raw_text,
                duration=asr_result.get("duration", 0.0),
                inference_latency=inference_latency,
                confidence=asr_result.get("confidence", 0.0),
            )

        if self.on_result:
            try:
                self.on_result(result)
            except Exception as exc:  # noqa: BLE001
                logger.error("处理转写结果时出错: %s", exc)

    @property
    def is_running(self) -> bool:
        return self._running.is_set()

    @property
    def is_transcribing(self) -> bool:
        """是否有转录任务正在进行或等待中"""
        return self._transcription_active.is_set() or not self._transcription_queue.empty()

    @property
    def pending_transcriptions(self) -> int:
        """返回队列中等待转录的任务数"""
        return self._transcription_queue.qsize()

    @property
    def transcription_stats(self) -> dict:
        """返回转录统计信息"""
        return {
            "submitted": self._transcription_task_count,
            "completed": self._transcription_completed_count,
            "pending": self.pending_transcriptions,
            "is_recording": self._running.is_set(),
            "is_transcribing": self.is_transcribing,
        }
