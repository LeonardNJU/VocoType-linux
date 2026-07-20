#!/usr/bin/env python3
"""Fcitx 5 全局语音模块的 Python 后端服务。

此服务作为独立进程运行，通过 Unix Socket 提供语音识别与可选 SLM 润色。
用户当前使用的 Rime、拼音或其他输入法继续由 Fcitx 5 原生管理。
"""
from __future__ import annotations

import sys
import os
import json
import socket
import logging
import signal
import stat
import threading
import time
from pathlib import Path

# 添加项目根目录到 path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import DEFAULT_CONFIG, ensure_logging_dir, load_config
from app.funasr_server import FunASRServer
from app.logging_config import setup_logging
from app.slm_polisher import SLMPolisher

logger = logging.getLogger(__name__)

SOCKET_PATH = "/tmp/vocotype-fcitx5.sock"
MAX_REQUEST_BYTES = 1024 * 1024
REQUEST_TIMEOUT_S = 2.0
DEFAULT_CONFIG_PATH = "~/.config/vocotype/fcitx5-backend.json"


def load_backend_config() -> tuple[dict, str]:
    """Load backend config from user config file if present."""
    config_path = os.environ.get("VOCOTYPE_FCITX5_CONFIG", DEFAULT_CONFIG_PATH)
    expanded_path = os.path.expanduser(config_path)
    if not os.path.exists(expanded_path):
        return dict(DEFAULT_CONFIG), expanded_path

    try:
        return load_config(expanded_path), expanded_path
    except Exception as exc:
        print(f"Failed to load config {expanded_path}: {exc}", file=sys.stderr)
        return dict(DEFAULT_CONFIG), expanded_path


def configure_logging(config: dict, debug: bool) -> None:
    """Configure logging with optional file output."""
    logging_cfg = config.get("logging", {})
    level = "DEBUG" if debug else logging_cfg.get("level", "INFO")
    write_file = bool(logging_cfg.get("file", False))
    log_dir = ensure_logging_dir(config) if write_file else None
    setup_logging(level=level, log_dir=log_dir)


class Fcitx5Backend:
    """Fcitx 5 Python 后端服务

    职责：
    1. 接收语音识别请求，调用 FunASRServer
    2. 对长句执行可选 SLM 润色
    3. 通过 IPC 返回结果给 Fcitx 5 全局 module
    """

    def __init__(self, config: dict | None = None):
        self.config = dict(config or DEFAULT_CONFIG)

        # 语音识别服务
        logger.info("正在初始化 FunASR 服务器...")
        self.asr_server = FunASRServer()
        asr_result = self.asr_server.initialize()
        if not asr_result['success']:
            logger.error("FunASR 初始化失败: %s", asr_result.get('error'))
            sys.exit(1)
        logger.info("FunASR 服务器初始化成功")

        self._asr_options = dict(self.config.get("asr", {}))
        self._slm_polisher = SLMPolisher(self.config.get("slm", {}))
        logger.info("SLM 长句润色: enabled=%s", self._slm_polisher.enabled)


        # 标记运行状态
        self.running = True
        self._asr_lock = threading.Lock()

        # 注册信号处理
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

    def _cleanup_socket_path(self, path: str) -> None:
        """安全删除旧 socket 文件（避免误删普通文件）"""
        if not os.path.exists(path):
            return

        try:
            st = os.lstat(path)
        except OSError as exc:
            logger.warning("检查旧 socket 失败: %s", exc)
            return

        if stat.S_ISSOCK(st.st_mode) or stat.S_ISLNK(st.st_mode):
            try:
                os.remove(path)
                logger.info("已移除旧 socket: %s", path)
            except OSError as exc:
                logger.warning("移除旧 socket 失败: %s", exc)
        else:
            raise RuntimeError(f"socket 路径已存在且不是 socket: {path}")

    def _signal_handler(self, signum, frame):
        """信号处理器"""
        logger.info("收到信号 %d，准备退出...", signum)
        self.running = False

    def run(self):
        """运行 IPC 服务器"""
        # 删除旧的 socket 文件
        self._cleanup_socket_path(SOCKET_PATH)

        # 创建 Unix Socket
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(SOCKET_PATH)
        os.chmod(SOCKET_PATH, 0o600)
        sock.listen(5)
        sock.settimeout(1.0)  # 设置超时以便处理信号

        logger.info("Fcitx5 Backend 已启动，监听: %s", SOCKET_PATH)

        try:
            while self.running:
                try:
                    conn, _ = sock.accept()
                    threading.Thread(
                        target=self.handle_client,
                        args=(conn,),
                        daemon=True,
                        name="Fcitx5BackendClient",
                    ).start()
                except socket.timeout:
                    continue
                except Exception as exc:
                    if self.running:
                        logger.error("接受连接失败: %s", exc)
        finally:
            sock.close()
            try:
                self._cleanup_socket_path(SOCKET_PATH)
            except RuntimeError as exc:
                logger.warning("清理 socket 失败: %s", exc)
            logger.info("Fcitx5 Backend 已停止")

    def handle_client(self, conn: socket.socket):
        """处理客户端请求

        IPC 协议：
        - 请求格式：JSON 字符串
        - 响应格式：JSON 字符串

        请求类型：
        1. transcribe: 语音识别
           {"type": "transcribe", "audio_path": "/tmp/xxx.wav", "long_mode": false}
           -> {"success": true, "text": "识别结果"}

        2. slm_prewarm: 预加载 SLM（长句模式按下时调用）
           {"type": "slm_prewarm"}
           -> {"success": true}

        3. slm_release: 释放 SLM（长句流程结束时调用）
           {"type": "slm_release"}
           -> {"success": true}

        4. ping: 健康检查
           {"type": "ping"}
           -> {"pong": true}
        """
        try:
            conn.settimeout(REQUEST_TIMEOUT_S)
            # 接收请求（读到 EOF）
            chunks = []
            total_bytes = 0
            while True:
                chunk = conn.recv(8192)
                if not chunk:
                    break
                chunks.append(chunk)
                total_bytes += len(chunk)
                if total_bytes > MAX_REQUEST_BYTES:
                    response_str = json.dumps({"error": "Request too large"}, ensure_ascii=False)
                    conn.sendall(response_str.encode('utf-8'))
                    return
            if not chunks:
                return
            data = b''.join(chunks).decode('utf-8')

            request = json.loads(data)
            req_type = request.get('type')

            logger.debug("收到请求: type=%s", req_type)

            # 处理请求
            if req_type == 'transcribe':
                # 语音识别
                audio_path = request.get('audio_path')
                long_mode = bool(request.get('long_mode', False))
                if not audio_path:
                    response = {"success": False, "error": "缺少 audio_path 参数"}
                else:
                    try:
                        asr_start = time.perf_counter()
                        with self._asr_lock:
                            result = self.asr_server.transcribe_audio(
                                audio_path,
                                options=self._asr_options,
                            )
                        asr_ms = (time.perf_counter() - asr_start) * 1000.0

                        slm_used = False
                        slm_ms = 0.0
                        slm_reason = "not_used"
                        if result.get("success"):
                            text = str(result.get("text", "")).strip()
                            if text:
                                should_polish = (
                                    long_mode
                                    and self._slm_polisher.should_polish(
                                        text,
                                        long_mode=True,
                                    )
                                )
                                if should_polish:
                                    polished_text, metrics = self._slm_polisher.polish(
                                        text,
                                        long_mode=long_mode,
                                    )
                                    slm_used = metrics.used
                                    slm_ms = metrics.latency_ms
                                    slm_reason = metrics.reason
                                    if self._slm_polisher.is_failure_reason(metrics.reason):
                                        logger.warning(
                                            "长句 SLM 调用失败: reason=%s",
                                            metrics.reason,
                                        )
                                        result = {
                                            "success": False,
                                            "error": self._slm_polisher.format_failure_message(
                                                metrics.reason
                                            ),
                                            "original_text": text,
                                        }
                                    else:
                                        result["text"] = polished_text
                                elif long_mode:
                                    slm_reason = (
                                        "disabled"
                                        if not self._slm_polisher.enabled
                                        else "too_short"
                                    )
                            else:
                                slm_reason = "empty_asr_text"

                        logger.info(
                            "Fcitx 转录流水线 mode=%s asr_ms=%.2f slm_used=%s slm_ms=%.2f fallback_reason=%s",
                            "long" if long_mode else "normal",
                            asr_ms,
                            slm_used,
                            slm_ms,
                            slm_reason,
                        )
                        response = result
                    finally:
                        if long_mode:
                            self._slm_polisher.release()

            elif req_type == 'slm_prewarm':
                self._slm_polisher.prewarm(long_mode=True)
                response = {"success": True}

            elif req_type == 'slm_release':
                self._slm_polisher.release()
                response = {"success": True}

            elif req_type == 'ping':
                # 健康检查
                response = {"pong": True}

            else:
                response = {"error": f"未知的请求类型: {req_type}"}

            # 发送响应
            response_str = json.dumps(response, ensure_ascii=False)
            conn.sendall(response_str.encode('utf-8'))

            logger.debug("已发送响应: %d 字节", len(response_str))

        except json.JSONDecodeError as exc:
            logger.error("JSON 解析失败: %s", exc)
            try:
                error_response = json.dumps({"error": "Invalid JSON"})
                conn.sendall(error_response.encode('utf-8'))
            except Exception:
                pass

        except socket.timeout:
            logger.warning("IPC 请求读取超时")
            try:
                error_response = json.dumps({"error": "Request timeout"})
                conn.sendall(error_response.encode('utf-8'))
            except Exception:
                pass

        except Exception as exc:
            logger.error("处理请求失败: %s", exc)
            import traceback
            traceback.print_exc()
            try:
                error_response = json.dumps({"error": str(exc)})
                conn.sendall(error_response.encode('utf-8'))
            except Exception:
                pass

        finally:
            conn.close()

    def cleanup(self):
        """清理资源"""
        logger.info("正在清理资源...")
        try:
            self.asr_server.cleanup()
        except Exception as exc:
            logger.error("清理资源失败: %s", exc)


def main():
    """主入口"""
    global SOCKET_PATH
    import argparse

    parser = argparse.ArgumentParser(
        description='VoCoType Fcitx5 Backend Server'
    )
    parser.add_argument(
        '--socket',
        default=SOCKET_PATH,
        help=f'Unix socket path (default: {SOCKET_PATH})'
    )
    parser.add_argument(
        '--debug',
        action='store_true',
        help='Enable debug logging'
    )
    args = parser.parse_args()

    config, config_path = load_backend_config()
    configure_logging(config, args.debug)
    logger.info("配置文件路径: %s", config_path)

    SOCKET_PATH = args.socket

    backend = Fcitx5Backend(config=config)
    try:
        backend.run()
    except KeyboardInterrupt:
        logger.info("收到 Ctrl+C，退出...")
    finally:
        backend.cleanup()


if __name__ == '__main__':
    main()
