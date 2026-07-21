#!/usr/bin/env python3
"""VoCoType IBus Engine - PTT语音输入法引擎

按住F9说话，松开后识别并输入到光标处（极速模式）。
按住Shift+F9可启用长句模式，支持可选 SLM 润色。
其他按键转发给 Rime 处理。
"""

from __future__ import annotations

import logging
import re
import threading
import queue
import tempfile
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, TYPE_CHECKING

import numpy as np

import gi
gi.require_version('IBus', '1.0')
from gi.repository import IBus, GLib

from app.audio_utils import (
    SAMPLE_RATE,
    DEFAULT_NATIVE_SAMPLE_RATE,
    load_audio_config,
    resample_audio,
)
from app.config import DEFAULT_CONFIG, load_config
from app.ibus_compat import build_capability_flags
from app.slm_polisher import SLMPolisher

if TYPE_CHECKING:
    from pyrime.session import Session as RimeSession

logger = logging.getLogger(__name__)

# 音频参数
BLOCK_MS = 20
DEFAULT_IBUS_CONFIG_PATH = "~/.config/vocotype/ibus.json"

AUDIO_DEVICE, CONFIGURED_SAMPLE_RATE = load_audio_config()


def load_ibus_config() -> dict:
    """Load IBus runtime config with safe fallback."""
    config_path = os.environ.get("VOCOTYPE_IBUS_CONFIG", DEFAULT_IBUS_CONFIG_PATH)
    expanded_path = os.path.expanduser(config_path)
    if not os.path.exists(expanded_path):
        return dict(DEFAULT_CONFIG)

    try:
        return load_config(expanded_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("加载 IBus 配置失败(%s): %s，回退默认配置", expanded_path, exc)
        return dict(DEFAULT_CONFIG)


@dataclass
class SurroundingSnapshot:
    """周边文本快照（用于语音编辑）"""

    text: str
    cursor_pos: int
    anchor_pos: int
    selected_text: str


@dataclass
class DirectEditResult:
    """结构化语音编辑命令执行结果"""

    handled: bool
    new_text: Optional[str] = None
    record_history: bool = True
    hint: str = ""
    mode: str = "replace"  # replace / key_events / no_replace / commit_only
    key_events: tuple[tuple[int, int, int], ...] = ()


class VoCoTypeEngine(IBus.Engine):
    """VoCoType IBus语音输入引擎"""

    __gtype_name__ = 'VoCoTypeEngine'

    # PTT触发键
    PTT_KEYVAL = IBus.KEY_F9
    # Linux evdev keycode for physical F9. This keeps PTT working even when
    # desktop firmware maps top-row F keys to media keyvals under Fn-lock.
    PTT_FALLBACK_KEYCODE = 67
    # 调试探针：Ctrl+F9 读取 surrounding text 并回填
    SURROUNDING_PROBE_CTRL_MASK = IBus.ModifierType.CONTROL_MASK
    EDIT_HISTORY_LIMIT = 20
    _PUNCTUATION_MAP = {
        "句号": "。",
        "逗号": "，",
        "问号": "？",
        "感叹号": "！",
        "冒号": "：",
        "分号": "；",
        "引号": "“”",
    }
    _KEYCODE_HINTS = {
        IBus.KEY_Left: 105,
        IBus.KEY_Right: 106,
        IBus.KEY_Up: 103,
        IBus.KEY_Down: 108,
        IBus.KEY_Home: 102,
        IBus.KEY_End: 107,
        IBus.KEY_a: 30,
        IBus.KEY_z: 44,
    }
    _CAPABILITY_FLAGS = build_capability_flags(IBus.Capabilite)

    # 全局session跟踪（用于调试）
    _active_sessions = set()
    _session_lock = threading.Lock()

    # 共享ASR服务（跨engine实例复用，避免重复加载模型）
    _shared_asr_server = None
    _shared_asr_lock = threading.Lock()
    _shared_asr_initializing = False
    _shared_asr_ready = threading.Event()
    _shared_asr_init_error: Optional[str] = None

    def __init__(self, bus: IBus.Bus, object_path: str):
        # 需要显式传入 DBus 连接与 object_path，避免 GLib g_variant object_path 断言失败。
        super().__init__(connection=bus.get_connection(), object_path=object_path)
        self._bus = bus
        self._object_path = object_path

        # 状态
        self._is_recording = False
        self._recording_long_mode = False
        self._recording_edit_mode = False
        self._audio_frames: list[np.ndarray] = []
        self._audio_queue: queue.Queue = queue.Queue(maxsize=500)
        self._stop_event = threading.Event()
        self._capture_thread: Optional[threading.Thread] = None
        self._stream = None
        self._edit_snapshot: Optional[SurroundingSnapshot] = None
        self._edit_undo_stack: list[str] = []
        self._edit_redo_stack: list[str] = []
        self._voice_clipboard = ""
        self._engine_enabled = False
        self._has_focus = False
        self._replace_capability_state = "unknown"  # unknown/supported/unsupported
        self._last_text_change_source = "none"  # none / voice_edit / app_commit
        self._last_internal_edit_text: Optional[str] = None

        # 运行配置（用于长句模式）
        self._runtime_config = load_ibus_config()
        self._asr_options = dict(self._runtime_config.get("asr", {}))
        self._asr_options["normalization"] = dict(
            self._runtime_config.get("normalization", {})
        )
        self._slm_polisher = SLMPolisher(self._runtime_config.get("slm", {}))
        logger.info("IBus SLM 长句润色: enabled=%s", self._slm_polisher.enabled)

        # ASR服务使用类级共享实例
        self._native_sample_rate = CONFIGURED_SAMPLE_RATE

        # Rime 集成（使用 pyrime 直接调用 librime）
        # 如果未安装 pyrime，则禁用 Rime 集成
        self._rime_session: Optional[RimeSession] = None
        self._rime_available = self._check_rime_available()
        self._rime_enabled = self._rime_available  # 只有 pyrime 可用时才启用
        self._rime_init_lock = threading.Lock()
        self._client_capabilities = 0
        self._window_context_cache = "window=unavailable(reason=not-collected)"
        self._window_context_cache_ts = 0.0

        if self._rime_available:
            logger.info(
                "VoCoTypeEngine 实例已创建（Rime 集成已启用, path=%s）",
                self._object_path,
            )
        else:
            logger.info(
                "VoCoTypeEngine 实例已创建（纯语音模式，Rime 集成未启用, path=%s）",
                self._object_path,
            )

    def _check_rime_available(self) -> bool:
        """检查 pyrime 是否可用"""
        try:
            import pyrime
            return True
        except ImportError:
            logger.info("pyrime 未安装，Rime 集成功能将被禁用")
            return False

    def _format_capabilities(self, caps: Optional[int] = None) -> str:
        value = int(self._client_capabilities if caps is None else caps)
        names = [
            name for flag, name in self._CAPABILITY_FLAGS
            if value & flag
        ]
        return "|".join(names) if names else "-"

    @staticmethod
    def _run_debug_command(argv: list[str], timeout: float = 0.2) -> str:
        try:
            result = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except Exception:
            return ""
        if result.returncode != 0:
            return ""
        return result.stdout.strip()

    def _get_active_window_context(self) -> str:
        now = time.monotonic()
        if (
            self._window_context_cache
            and (now - self._window_context_cache_ts) < 0.5
        ):
            return self._window_context_cache

        session_type = os.environ.get("XDG_SESSION_TYPE", "") or "unknown"
        xprop = shutil.which("xprop")
        if not xprop:
            summary = f"window=unavailable(session={session_type}, reason=xprop-missing)"
            self._window_context_cache = summary
            self._window_context_cache_ts = now
            return summary

        active_window = self._run_debug_command([xprop, "-root", "_NET_ACTIVE_WINDOW"])
        match = re.search(r"window id # (0x[0-9a-fA-F]+)", active_window)
        if not match:
            summary = f"window=unavailable(session={session_type}, reason=no-active-window)"
            self._window_context_cache = summary
            self._window_context_cache_ts = now
            return summary

        window_id = match.group(1)
        if window_id == "0x0":
            summary = f"window=unavailable(session={session_type}, reason=window-id-0)"
            self._window_context_cache = summary
            self._window_context_cache_ts = now
            return summary

        details = self._run_debug_command(
            [xprop, "-id", window_id, "WM_CLASS", "_NET_WM_NAME", "WM_NAME", "_NET_WM_PID"]
        )

        wm_class = ""
        title = ""
        pid = ""
        for line in details.splitlines():
            if line.startswith("WM_CLASS"):
                quoted = re.findall(r'"([^"]*)"', line)
                wm_class = "/".join(filter(None, quoted))
            elif line.startswith("_NET_WM_NAME") or line.startswith("WM_NAME"):
                quoted = re.findall(r'"([^"]*)"', line)
                if quoted:
                    title = quoted[0]
                elif "=" in line:
                    title = line.split("=", 1)[1].strip()
            elif line.startswith("_NET_WM_PID") and "=" in line:
                pid = line.split("=", 1)[1].strip()

        cmd = ""
        ps_cmd = shutil.which("ps")
        if pid and ps_cmd:
            cmd_output = self._run_debug_command(
                [ps_cmd, "-p", pid, "-o", "comm=", "-o", "args="],
                timeout=0.2,
            )
            if cmd_output:
                cmd = self._clip_probe_text(cmd_output, 96)

        summary_parts = [f"id={window_id}"]
        if wm_class:
            summary_parts.append(f"class='{self._clip_probe_text(wm_class, 48)}'")
        if title:
            summary_parts.append(f"title='{self._clip_probe_text(title, 64)}'")
        if pid:
            summary_parts.append(f"pid={pid}")
        if cmd:
            summary_parts.append(f"cmd='{cmd}'")

        summary = "window=" + " ".join(summary_parts)
        self._window_context_cache = summary
        self._window_context_cache_ts = now
        return summary

    def _get_surrounding_debug_context(self) -> str:
        if not self._supports_surrounding_text():
            return "sur=unsupported"

        snapshot, error = self._capture_surrounding_snapshot()
        if snapshot is None:
            reason = self._clip_probe_text(error, 64) or "unknown"
            return f"sur=unavailable reason='{reason}'"

        current_sentence, previous_sentence = self._extract_sentence_window(
            snapshot.text,
            snapshot.cursor_pos,
        )
        return (
            "sur="
            f"len={len(snapshot.text)} cursor={snapshot.cursor_pos} anchor={snapshot.anchor_pos} "
            f"sel={len(snapshot.selected_text)} "
            f"prev='{self._clip_probe_text(previous_sentence)}' "
            f"cur='{self._clip_probe_text(current_sentence)}' "
            f"selected='{self._clip_probe_text(snapshot.selected_text)}'"
        )

    def _build_lifecycle_context(self, include_surrounding: bool = False) -> str:
        parts = [
            f"path={self._object_path}",
            f"enabled={int(self._engine_enabled)}",
            f"focus={int(self._has_focus)}",
            f"active={int(self._is_engine_active())}",
            f"recording={int(self._is_recording)}",
            f"long={int(self._recording_long_mode)}",
            f"edit={int(self._recording_edit_mode)}",
            f"caps=0x{self._client_capabilities:x}[{self._format_capabilities()}]",
            (
                "rime="
                f"available:{int(self._rime_available)} "
                f"enabled:{int(self._rime_enabled)} "
                f"session:{int(self._rime_session is not None)}"
            ),
            self._get_active_window_context(),
        ]
        if include_surrounding:
            parts.append(self._get_surrounding_debug_context())
        return "; ".join(parts)

    def _log_lifecycle(self, event: str, include_surrounding: bool = False) -> None:
        logger.info(
            "Lifecycle[%s]: %s",
            event,
            self._build_lifecycle_context(include_surrounding=include_surrounding),
        )

    def _resolve_input_device(self, sd):
        """选择可用的输入设备，优先使用显式配置。"""
        if AUDIO_DEVICE is not None:
            try:
                info = sd.query_devices(AUDIO_DEVICE)
                if info.get("max_input_channels", 0) > 0:
                    return AUDIO_DEVICE
                logger.warning("设备 %s 无输入通道，回退选择输入设备", AUDIO_DEVICE)
            except Exception as exc:
                logger.warning("查询设备 %s 失败: %s", AUDIO_DEVICE, exc)

        try:
            devices = sd.query_devices()
            for idx, info in enumerate(devices):
                if info.get("max_input_channels", 0) > 0:
                    logger.info("回退至输入设备 #%s (%s)", idx, info.get("name", "unknown"))
                    return idx
        except Exception as exc:
            logger.warning("查询输入设备列表失败: %s", exc)

        return None

    def _resolve_sample_rate(self, sd, device, preferred):
        """选择可用采样率，优先使用指定值。"""
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

    def _read_schema_from_yaml(self, user_yaml: Path) -> Optional[str]:
        """从指定 user.yaml 读取用户偏好方案"""
        if not user_yaml.exists():
            return None

        try:
            import yaml
            with open(user_yaml, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if data and "var" in data:
                return data["var"].get("previously_selected_schema")
        except ImportError:
            # 没有 PyYAML，用简单的正则解析
            import re
            try:
                content = user_yaml.read_text(encoding="utf-8")
                match = re.search(r"previously_selected_schema:\s*(\S+)", content)
                if match:
                    return match.group(1)
            except Exception:
                pass
        except Exception as exc:
            logger.warning("读取 user.yaml 失败: %s", exc)

        return None

    def _get_preferred_rime_schema(self, user_data_dir: Path) -> Optional[str]:
        """优先读取 vocotype 的 user.yaml，失败再回退 user_data_dir"""
        vocotype_yaml = Path.home() / ".config" / "vocotype" / "rime" / "user.yaml"
        preferred = self._read_schema_from_yaml(vocotype_yaml)
        if preferred:
            return preferred
        return self._read_schema_from_yaml(user_data_dir / "user.yaml")

    # 默认 schema：朙月拼音，librime 自带
    DEFAULT_RIME_SCHEMA = "luna_pinyin"

    def _init_rime_session(self):
        """初始化 Rime Session（懒加载）"""
        if self._rime_session is not None:
            return True

        with self._rime_init_lock:
            if self._rime_session is not None:
                return True

            api = None
            session_id = None
            session = None
            session_tracked = False
            try:
                # 确保日志目录存在
                log_dir = Path.home() / ".local" / "share" / "vocotype" / "rime"
                log_dir.mkdir(parents=True, exist_ok=True)

                from pyrime.api import Traits, API
                from pyrime.session import Session
                from pyrime.ime import Context

                # 按优先级选择用户目录
                # 1. 优先使用有 default.yaml 的用户目录（用户自定义配置）
                # 2. 否则使用 ibus-rime 目录（如果存在）
                # 3. 最后使用 vocotype 目录
                vocotype_user_dir = Path.home() / ".config" / "vocotype" / "rime"
                ibus_rime_user = Path.home() / ".config" / "ibus" / "rime"

                if (ibus_rime_user / "default.yaml").exists():
                    user_data_dir = ibus_rime_user
                elif (vocotype_user_dir / "default.yaml").exists():
                    user_data_dir = vocotype_user_dir
                elif ibus_rime_user.exists():
                    user_data_dir = ibus_rime_user
                else:
                    user_data_dir = vocotype_user_dir
                    user_data_dir.mkdir(parents=True, exist_ok=True)

                # 查找共享数据目录
                shared_dirs = [
                    Path("/usr/share/rime-data"),
                    Path("/usr/local/share/rime-data"),
                ]
                shared_data_dir = next((d for d in shared_dirs if d.exists()), None)
                if shared_data_dir is None:
                    logger.error("找不到 Rime 共享数据目录")
                    return False

                # 验证至少有一个 default.yaml 可用（用户或系统）
                if not (user_data_dir / "default.yaml").exists() and \
                   not (shared_data_dir / "default.yaml").exists():
                    logger.error("找不到 Rime 配置文件（用户和系统目录都缺少 default.yaml）")
                    return False

                # 仅在使用 vocotype 目录时创建符号链接
                if user_data_dir == vocotype_user_dir:
                    for subdir in ["build", "lua", "cn_dicts", "en_dicts", "opencc", "others"]:
                        link_path = user_data_dir / subdir
                        if link_path.exists() or link_path.is_symlink():
                            continue
                        # 优先 ibus-rime 用户目录
                        target_path = ibus_rime_user / subdir
                        if not target_path.exists():
                            target_path = shared_data_dir / subdir
                        if target_path.exists():
                            try:
                                link_path.symlink_to(target_path)
                                logger.debug("创建 %s 符号链接: %s -> %s", subdir, link_path, target_path)
                            except OSError as e:
                                logger.warning("创建 %s 符号链接失败: %s", subdir, e)

                # 注意：pyrime 编译版本中 user_data_dir 和 log_dir 字段位置与 .pyi 存根相反。
                # 实测：传入 user_data_dir 的值被 librime 用作 log_dir，
                #       传入 log_dir 的值被 librime 用作 user_data_dir（读取 schema/build）。
                # 因此这里交换两个字段，使 librime 能正确读取用户配置目录中的 schema 和 build。
                traits = Traits(
                    shared_data_dir=str(shared_data_dir),
                    user_data_dir=str(log_dir),      # pyrime bug: 此值实为 librime log_dir
                    log_dir=str(user_data_dir),       # pyrime bug: 此值实为 librime user_data_dir
                    distribution_name="VoCoType",
                    distribution_code_name="vocotype",
                    distribution_version="1.0",
                    app_name="rime.vocotype",
                )

                logger.info("Rime traits: shared=%s, user=%s, log=%s",
                           shared_data_dir, user_data_dir, log_dir)

                # 每个engine实例创建自己的session（避免共享状态问题）
                # Traits.__post_init__ 已完成 setup+initialize，不重复调用
                api = API()
                logger.info("Rime API 创建 (addr=%s)", api.address)
                session_id = api.create_session()

                # 跟踪活跃session（用于调试）
                with self._session_lock:
                    self._active_sessions.add(session_id)
                    session_tracked = True
                    logger.info("Session ID: %s created, active sessions: %d",
                               session_id, len(self._active_sessions))

                # 创建 Session 对象
                session = Session(traits=traits, api=api, id=session_id)

                # 获取当前schema（处理可能的编码问题）
                try:
                    schema = session.get_current_schema()
                    # 如果返回的是字节串，尝试解码
                    if isinstance(schema, bytes):
                        try:
                            schema = schema.decode('utf-8')
                        except UnicodeDecodeError:
                            schema = schema.decode('gbk', errors='ignore')
                    logger.info("Rime Session 已创建，schema: %s", schema)
                except Exception as e:
                    logger.warning("获取当前schema失败: %s，使用默认值", e)
                    schema = None

                # 避免调用 get_schema_list（部分环境可能触发 librime 崩溃）
                preferred_schema = self._get_preferred_rime_schema(user_data_dir)
                if preferred_schema:
                    try:
                        logger.info("尝试使用用户配置的方案: %s", preferred_schema)
                        session.select_schema(preferred_schema)
                    except Exception as exc:
                        logger.warning("选择用户方案失败: %s", exc)
                elif schema in (None, "", ".default"):
                    try:
                        logger.info("使用默认方案: %s", self.DEFAULT_RIME_SCHEMA)
                        session.select_schema(self.DEFAULT_RIME_SCHEMA)
                    except Exception as exc:
                        logger.warning("选择默认方案失败: %s", exc)

                try:
                    logger.info("当前 schema: %s", session.get_current_schema())
                except Exception:
                    pass
                self._rime_session = session
                return True

            except Exception as exc:
                logger.error("初始化 Rime Session 失败: %s", exc)
                if api is not None and session_id is not None:
                    try:
                        api.destroy_session(session_id)
                    except Exception as cleanup_exc:
                        logger.warning("清理失败的 Rime session 失败: %s", cleanup_exc)
                if session_tracked and session_id is not None:
                    with self._session_lock:
                        self._active_sessions.discard(session_id)
                        logger.info("Session ID: %s removed after init failure, active sessions: %d",
                                   session_id, len(self._active_sessions))
                self._rime_session = None
                import traceback
                traceback.print_exc()
                self._rime_enabled = False  # Disable RIME on failure
                return False

    def do_enable(self):
        """引擎启用"""
        self._engine_enabled = True
        # 告知客户端需要 surrounding text（若客户端支持）。
        # IBus C API 文档建议在 enable 阶段调用 get_surrounding_text。
        try:
            self.get_surrounding_text()
        except Exception as exc:
            logger.debug("enable 阶段请求 surrounding text 失败: %s", exc)
        self._log_lifecycle("enable", include_surrounding=self._supports_surrounding_text())

    def do_set_capabilities(self, caps):
        """记录客户端能力（用于 surrounding text 调试输出）"""
        previous = self._client_capabilities
        self._client_capabilities = int(caps)
        logger.info(
            "Lifecycle[capabilities]: old=0x%x[%s] new=0x%x[%s]; %s",
            previous,
            self._format_capabilities(previous),
            self._client_capabilities,
            self._format_capabilities(),
            self._build_lifecycle_context(include_surrounding=False),
        )

    def do_disable(self):
        """引擎禁用时清理资源（IBus不会调用do_destroy）"""
        self._log_lifecycle("disable", include_surrounding=self._supports_surrounding_text())
        self._engine_enabled = False

        # 停止录音
        if self._is_recording:
            self._stop_recording()

        # 清除UI
        self._clear_preedit()
        self.hide_lookup_table()
        self._edit_snapshot = None

        # 释放Rime session（因为IBus不会调用do_destroy）
        if self._rime_session:
            try:
                self._rime_session.clear_composition()
                session_id = self._rime_session.id
                api = self._rime_session.api
                api.destroy_session(session_id)

                # 从活跃session中移除
                with self._session_lock:
                    self._active_sessions.discard(session_id)
                    logger.info("Rime session %s released on disable, active sessions: %d",
                               session_id, len(self._active_sessions))
            except Exception as e:
                logger.warning("Failed to release Rime session: %s", e)
            self._rime_session = None
            self._rime_enabled = self._rime_available  # 重置状态，下次启用时重新初始化

    def do_destroy(self):
        """引擎销毁时清理资源"""
        self._log_lifecycle("destroy", include_surrounding=False)

        # 停止录音
        if self._is_recording:
            self._stop_recording()

        # 关闭音频流
        if self._stream:
            try:
                self._stream.close()
            except Exception:
                pass

        # 释放Rime session
        if self._rime_session:
            try:
                session_id = self._rime_session.id
                api = self._rime_session.api
                api.destroy_session(session_id)

                # 从活跃session中移除
                with self._session_lock:
                    self._active_sessions.discard(session_id)
                    logger.info("Rime session %s destroyed, active sessions: %d",
                               session_id, len(self._active_sessions))
            except Exception as e:
                logger.warning("Failed to destroy Rime session: %s", e)
            self._rime_session = None

    def do_focus_in(self):
        """获得输入焦点"""
        self._has_focus = True
        self._log_lifecycle("focus-in", include_surrounding=False)

    def do_focus_out(self):
        """失去输入焦点"""
        self._has_focus = False
        self._log_lifecycle("focus-out", include_surrounding=False)
        if self._is_recording:
            self._stop_recording()
        # 清除 Rime 组合
        if self._rime_session:
            try:
                self._rime_session.clear_composition()
            except Exception:
                pass
        self._clear_preedit()
        self.hide_lookup_table()

    def _ensure_asr_ready(self):
        """确保共享ASR服务器已初始化（懒加载）"""
        cls = type(self)
        if cls._shared_asr_server is not None and cls._shared_asr_ready.is_set():
            return True

        with cls._shared_asr_lock:
            if cls._shared_asr_server is not None and cls._shared_asr_ready.is_set():
                return True
            if cls._shared_asr_initializing:
                return False
            cls._shared_asr_initializing = True
            cls._shared_asr_init_error = None
            cls._shared_asr_ready.clear()

        def init_asr_shared():
            server = None
            try:
                logger.info("开始初始化FunASR（共享实例）...")
                from app.funasr_server import FunASRServer
                server = FunASRServer()
                result = server.initialize()
                if result["success"]:
                    with cls._shared_asr_lock:
                        cls._shared_asr_server = server
                        cls._shared_asr_ready.set()
                    logger.info("FunASR共享实例初始化成功")
                else:
                    error_msg = str(result.get("error", "未知错误"))
                    logger.error("FunASR共享实例初始化失败: %s", error_msg)
                    with cls._shared_asr_lock:
                        cls._shared_asr_server = None
                        cls._shared_asr_init_error = error_msg
                        cls._shared_asr_ready.clear()
                    try:
                        if server is not None:
                            server.cleanup()
                    except Exception:
                        pass
            except Exception as e:
                logger.error("FunASR共享实例初始化异常: %s", e)
                with cls._shared_asr_lock:
                    cls._shared_asr_server = None
                    cls._shared_asr_init_error = str(e)
                    cls._shared_asr_ready.clear()
                try:
                    if server is not None:
                        server.cleanup()
                except Exception:
                    pass
            finally:
                with cls._shared_asr_lock:
                    cls._shared_asr_initializing = False

        # 后台初始化
        threading.Thread(target=init_asr_shared, daemon=True).start()
        return False

    @classmethod
    def shutdown_shared_asr(cls):
        """在进程退出时主动释放共享ASR资源"""
        with cls._shared_asr_lock:
            server = cls._shared_asr_server
            cls._shared_asr_server = None
            cls._shared_asr_initializing = False
            cls._shared_asr_init_error = None
            cls._shared_asr_ready.clear()
        if server is not None:
            try:
                server.cleanup()
                logger.info("FunASR共享实例已释放")
            except Exception as exc:
                logger.warning("释放FunASR共享实例失败: %s", exc)

    def do_process_key_event(self, keyval, keycode, state):
        """处理按键事件"""
        # 调试：记录所有按键
        is_release = bool(state & IBus.ModifierType.RELEASE_MASK)
        logger.debug(
            "Key event: keyval=%s, keycode=%s, state=%s, is_release=%s, F9=%s",
            keyval,
            keycode,
            state,
            is_release,
            self.PTT_KEYVAL,
        )

        # 检查是否是松开事件
        is_release = bool(state & IBus.ModifierType.RELEASE_MASK)

        # 处理 F9 键：优先 keyval，其次兼容物理 keycode（Fn 锁/多媒体键场景）
        is_ptt_key = (keyval == self.PTT_KEYVAL) or (keycode == self.PTT_FALLBACK_KEYCODE)

        # Ctrl+F9: 语音编辑模式
        # Ctrl+Shift+F9: surrounding text 探针（保留调试能力）
        disallowed_mods = IBus.ModifierType.MOD1_MASK | IBus.ModifierType.SUPER_MASK | IBus.ModifierType.MOD4_MASK
        ctrl_held = bool(state & self.SURROUNDING_PROBE_CTRL_MASK)
        shift_held = bool(state & IBus.ModifierType.SHIFT_MASK)
        is_ctrl_edit = is_ptt_key and ctrl_held and not shift_held and not (state & disallowed_mods)
        is_ctrl_probe = is_ptt_key and ctrl_held and shift_held and not (state & disallowed_mods)

        if is_ctrl_edit:
            if not is_release:
                self._start_voice_edit_recording()
            elif self._is_recording and self._recording_edit_mode:
                self._stop_and_transcribe()
            return True

        if is_ctrl_probe:
            if not is_release:
                self._probe_surrounding_text()
            return True

        if not is_ptt_key:
            if self._is_ibus_switch_hotkey(keyval, state):
                return False
            return self._forward_key_to_rime(keyval, keycode, state)

        if not is_release:
            # F9按下 -> 开始录音
            long_mode = bool(state & IBus.ModifierType.SHIFT_MASK)
            if not self._is_recording:
                self._start_recording(long_mode=long_mode)
            return True
        else:
            # F9松开 -> 停止录音并转录
            if self._is_recording:
                self._stop_and_transcribe()
            return True

    def _supports_surrounding_text(self) -> bool:
        return bool(self._client_capabilities & int(IBus.Capabilite.SURROUNDING_TEXT))

    def _is_engine_active(self) -> bool:
        """是否仍是当前活跃输入法引擎（避免切换输入法后误上屏）"""
        return bool(self._engine_enabled and self._has_focus)

    def _clear_auxiliary_text(self):
        try:
            self.hide_auxiliary_text()
        except Exception:
            pass
        return False

    def _update_auxiliary_status(self, text: str):
        try:
            aux = IBus.Text.new_from_string(text)
            self.update_auxiliary_text(aux, True)
        except Exception as exc:
            logger.debug("更新辅助状态失败: %s", exc)

    def _show_nonintrusive_error(self, error: str, timeout_ms: int = 2000) -> bool:
        self._update_auxiliary_status(f"❌ {error}")
        GLib.timeout_add(timeout_ms, self._clear_auxiliary_text)
        return False

    def _build_edit_env_status(self, snapshot: Optional[SurroundingSnapshot]) -> str:
        has_sur = int(self._supports_surrounding_text())
        if self._replace_capability_state == "supported":
            replace_flag = "del=ok"
        elif self._replace_capability_state == "unsupported":
            replace_flag = "del=no"
        else:
            replace_flag = "del=?"
        sel_len = 0
        if snapshot is not None:
            sel_len = max(0, abs(int(snapshot.cursor_pos) - int(snapshot.anchor_pos)))
        active = int(self._is_engine_active())
        return f"🎤 编辑中({replace_flag} sur={has_sur} sel={sel_len} active={active})"

    def _capture_surrounding_snapshot(self) -> tuple[Optional[SurroundingSnapshot], str]:
        if not self._supports_surrounding_text():
            return None, "当前输入框不支持获取输入内容"

        try:
            ibus_text, cursor_pos, anchor_pos = self.get_surrounding_text()
            surrounding = ibus_text.get_text() if ibus_text else ""
        except Exception as exc:
            logger.warning("读取 surrounding text 失败: %s", exc)
            return None, "当前输入框不支持获取输入内容"

        text_len = len(surrounding)
        cursor = max(0, min(int(cursor_pos), text_len))
        anchor = max(0, min(int(anchor_pos), text_len))

        selected = ""
        if anchor != cursor:
            sel_start, sel_end = sorted((anchor, cursor))
            selected = surrounding[sel_start:sel_end]

        return (
            SurroundingSnapshot(
                text=surrounding,
                cursor_pos=cursor,
                anchor_pos=anchor,
                selected_text=selected,
            ),
            "",
        )

    def _reload_runtime_config(self) -> None:
        """Reload GUI-managed runtime settings before a new recording."""

        latest = load_ibus_config()
        if latest == self._runtime_config:
            return
        previous_polisher = self._slm_polisher
        self._runtime_config = latest
        self._asr_options = dict(latest.get("asr", {}))
        self._asr_options["normalization"] = dict(
            latest.get("normalization", {})
        )
        self._slm_polisher = SLMPolisher(latest.get("slm", {}))
        previous_polisher.release()
        logger.info(
            "VoCoType 运行配置已重新加载: slm_enabled=%s normalization_enabled=%s",
            self._slm_polisher.enabled,
            self._asr_options["normalization"].get("enabled", True),
        )

    def _start_voice_edit_recording(self):
        """Ctrl+F9: 开始语音编辑（先验证 surrounding 能力）"""
        if self._is_recording:
            return
        self._reload_runtime_config()

        if not self._is_engine_active():
            self._show_nonintrusive_error("当前输入法未激活，已取消编辑")
            return

        if not self._slm_polisher.enabled:
            self._show_nonintrusive_error("SLM 未启用，无法语音编辑")
            return

        snapshot, error = self._capture_surrounding_snapshot()
        if snapshot is None:
            self._show_nonintrusive_error(error or "当前输入框不支持获取输入内容")
            return

        self._edit_snapshot = snapshot
        self._start_recording(edit_mode=True)

    def _show_hint(self, text: str, timeout_ms: int = 1600) -> bool:
        """短暂显示提示，不改写输入框正文"""
        self._update_auxiliary_status(text)
        GLib.timeout_add(timeout_ms, self._clear_auxiliary_text)
        return False

    @staticmethod
    def _parse_count_from_command(cmd: str) -> int:
        """从命令中解析重复次数，默认 1，最大 20。"""
        digit_match = re.search(r"(\d+)", cmd)
        if digit_match:
            return max(1, min(20, int(digit_match.group(1))))

        cn_map = {
            "一": 1,
            "二": 2,
            "两": 2,
            "三": 3,
            "四": 4,
            "五": 5,
            "六": 6,
            "七": 7,
            "八": 8,
            "九": 9,
            "十": 10,
        }
        for ch, value in cn_map.items():
            if ch in cmd:
                return value
        return 1

    @staticmethod
    def _normalize_voice_command(command: str) -> str:
        cmd = " ".join((command or "").strip().split())
        if not cmd:
            return ""
        cmd = re.sub(r"^(?:请|麻烦|帮我|帮忙)\s*", "", cmd)
        cmd = re.sub(r"(一下子?|吧)$", "", cmd)
        cmd = re.sub(r"[。！？!?，,；;：:]+$", "", cmd)
        return cmd.strip()

    def _rewrite_insert_generation_instruction(self, command: str) -> str:
        """将“输入/写一段/生成一段 ...”重写为可执行的编辑指令。"""
        cmd = self._normalize_voice_command(command)
        if not cmd:
            return ""

        match = re.match(
            r"^(?:输入|写|写一段|生成|生成一段|来一段)\s*(.+)\s*$",
            cmd,
        )
        if not match:
            return ""

        request = self._strip_command_quotes(match.group(1))
        if not request:
            return ""

        return (
            "请按以下要求生成并插入文本："
            f"{request}。"
            "将生成结果插入到当前光标位置；如果当前有选中文本，则替换选中内容。"
            "除插入/替换位置外，不要改动任何其他文本。"
            "只输出编辑后的完整输入框文本。"
        )

    def _keycode_for_keyval(self, keyval: int) -> int:
        return int(self._KEYCODE_HINTS.get(int(keyval), 0))

    def _key_events(
        self,
        keyval: int,
        *,
        state: int = 0,
        repeat: int = 1,
        keycode: Optional[int] = None,
    ) -> tuple[tuple[int, int, int], ...]:
        events: list[tuple[int, int, int]] = []
        count = max(1, min(20, int(repeat)))
        resolved_keycode = self._keycode_for_keyval(keyval) if keycode is None else int(keycode)
        for _ in range(count):
            events.append((int(keyval), resolved_keycode, int(state)))
        return tuple(events)

    def _run_key_events(self, events: tuple[tuple[int, int, int], ...], hint: str = "") -> bool:
        """在主线程执行导航/选区按键序列。"""
        if not self._is_engine_active():
            self._show_nonintrusive_error("当前输入法未激活，已取消导航")
            return False

        if not events:
            if hint:
                self._show_hint(hint)
            return False

        try:
            release_mask = int(IBus.ModifierType.RELEASE_MASK)
            logger.info("执行导航按键序列: %s", events)
            for keyval, keycode, state in events:
                pressed_state = int(state)
                self.forward_key_event(int(keyval), int(keycode), pressed_state)
                self.forward_key_event(int(keyval), int(keycode), pressed_state | release_mask)
            if hint:
                self._show_hint(hint)
            return False
        except Exception as exc:
            logger.warning("导航按键下发失败: %s", exc)
            self._show_nonintrusive_error("当前输入框不支持导航命令")
            return False

    def _push_undo_state(self, text: str) -> None:
        if self._edit_undo_stack and self._edit_undo_stack[-1] == text:
            return
        self._edit_undo_stack.append(text)
        if len(self._edit_undo_stack) > self.EDIT_HISTORY_LIMIT:
            self._edit_undo_stack.pop(0)
        self._edit_redo_stack.clear()

    @staticmethod
    def _strip_command_quotes(text: str) -> str:
        return str(text or "").strip().strip("“”\"'")

    @staticmethod
    def _predict_commit_result(snapshot: SurroundingSnapshot, payload: str) -> str:
        """预测 commit_text 后的 surrounding 文本（用于撤销分流判断）。"""
        text = snapshot.text or ""
        cursor = max(0, min(int(snapshot.cursor_pos), len(text)))
        anchor = max(0, min(int(snapshot.anchor_pos), len(text)))
        sel_start, sel_end = sorted((anchor, cursor))
        if sel_end > sel_start:
            return text[:sel_start] + payload + text[sel_end:]
        return text[:cursor] + payload + text[cursor:]

    @staticmethod
    def _sentence_spans(text: str) -> list[tuple[int, int]]:
        if not text:
            return []
        delimiters = set("。！？!?；;.\n")
        spans: list[tuple[int, int]] = []
        start = 0
        for idx, ch in enumerate(text):
            if ch in delimiters:
                end = idx + 1
                if end > start:
                    spans.append((start, end))
                start = end
        if start < len(text):
            spans.append((start, len(text)))
        return spans

    @staticmethod
    def _locate_sentence_index(spans: list[tuple[int, int]], cursor_pos: int) -> int:
        if not spans:
            return -1
        cursor = max(0, cursor_pos)
        for idx, (seg_start, seg_end) in enumerate(spans):
            if seg_start <= cursor <= seg_end:
                return idx
        return len(spans) - 1

    def _apply_direct_edit_command(
        self,
        snapshot: SurroundingSnapshot,
        instruction: str,
    ) -> DirectEditResult:
        """优先处理高频、确定性语音编辑命令。"""
        cmd = self._normalize_voice_command(instruction)
        if not cmd:
            return DirectEditResult(False)

        text = snapshot.text
        cursor = snapshot.cursor_pos
        anchor = snapshot.anchor_pos
        lower_cmd = cmd.lower()

        if lower_cmd in {
            "显示上下文",
            "显示上下文信息",
            "输出上下文",
            "输出上下文信息",
            "显示surrounding信息",
            "输出surrounding信息",
            "surrounding info",
            "context info",
        }:
            current_sentence, previous_sentence = self._extract_sentence_window(text, cursor)
            report = (
                "[VT-SURR "
                f"cap={int(self._supports_surrounding_text())} "
                f"active={int(self._is_engine_active())} "
                f"del={self._replace_capability_state} "
                f"len={len(text)} cursor={cursor} anchor={anchor} "
                f"prev='{self._clip_probe_text(previous_sentence)}' "
                f"cur='{self._clip_probe_text(current_sentence)}' "
                f"sel='{self._clip_probe_text(snapshot.selected_text)}' "
                f"all='{self._clip_probe_text(text, 120)}'"
                "]"
            )
            return DirectEditResult(
                handled=True,
                mode="commit_only",
                new_text=report,
                record_history=True,
                hint="已输出上下文信息",
            )

        sel_start, sel_end = sorted((anchor, cursor))
        selected_text = text[sel_start:sel_end] if sel_end > sel_start else ""

        if lower_cmd in {"撤销", "撤回", "撤销修改", "撤销上一步", "undo"}:
            can_internal_undo = (
                bool(self._edit_undo_stack)
                and self._last_text_change_source == "voice_edit"
                and self._last_internal_edit_text == text
            )
            if can_internal_undo:
                previous = self._edit_undo_stack.pop()
                self._edit_redo_stack.append(text)
                if len(self._edit_redo_stack) > self.EDIT_HISTORY_LIMIT:
                    self._edit_redo_stack.pop(0)
                return DirectEditResult(True, previous, record_history=False, hint="已撤销语音编辑")

            self._last_text_change_source = "app_commit"
            self._last_internal_edit_text = None
            return DirectEditResult(
                handled=True,
                mode="key_events",
                key_events=self._key_events(
                    IBus.KEY_z,
                    state=int(IBus.ModifierType.CONTROL_MASK),
                ),
                record_history=False,
                hint="已发送应用撤销",
            )

        if lower_cmd in {"重做", "恢复", "redo"}:
            can_internal_redo = (
                bool(self._edit_redo_stack)
                and self._last_text_change_source == "voice_edit"
                and self._last_internal_edit_text == text
            )
            if can_internal_redo:
                recovered = self._edit_redo_stack.pop()
                self._edit_undo_stack.append(text)
                if len(self._edit_undo_stack) > self.EDIT_HISTORY_LIMIT:
                    self._edit_undo_stack.pop(0)
                return DirectEditResult(True, recovered, record_history=False, hint="已重做语音编辑")

            self._last_text_change_source = "app_commit"
            self._last_internal_edit_text = None
            return DirectEditResult(
                handled=True,
                mode="key_events",
                key_events=self._key_events(
                    IBus.KEY_z,
                    state=int(IBus.ModifierType.CONTROL_MASK | IBus.ModifierType.SHIFT_MASK),
                ),
                record_history=False,
                hint="已发送应用重做",
            )

        if lower_cmd in {"复制全部", "复制全文", "copy all"}:
            self._voice_clipboard = text
            return DirectEditResult(True, text, record_history=False, hint="已复制全文")

        if lower_cmd in {"复制选中", "复制选中内容", "copy that"}:
            if not selected_text:
                return DirectEditResult(True, text, record_history=False, hint="当前没有选中内容")
            self._voice_clipboard = selected_text
            return DirectEditResult(True, text, record_history=False, hint="已复制选中内容")

        if lower_cmd in {"剪切全部", "剪切全文", "cut all"}:
            self._voice_clipboard = text
            return DirectEditResult(True, "", record_history=True, hint="已剪切全文")

        if lower_cmd in {"剪切选中", "剪切选中内容", "cut that"}:
            if not selected_text:
                return DirectEditResult(True, text, record_history=False, hint="当前没有选中内容")
            self._voice_clipboard = selected_text
            return DirectEditResult(
                True,
                text[:sel_start] + text[sel_end:],
                record_history=True,
                hint="已剪切选中内容",
            )

        if lower_cmd in {"粘贴", "贴上", "paste"}:
            if not self._voice_clipboard:
                return DirectEditResult(True, text, record_history=False, hint="剪贴板为空")
            if sel_end > sel_start:
                merged = text[:sel_start] + self._voice_clipboard + text[sel_end:]
            else:
                merged = text[:cursor] + self._voice_clipboard + text[cursor:]
            return DirectEditResult(True, merged, record_history=True, hint="已粘贴")

        if lower_cmd in {"清空", "清空输入框", "删除全部", "删掉全部", "全选删除"}:
            return DirectEditResult(True, "", record_history=True, hint="已清空")

        if lower_cmd in {"删除选中", "删除选中内容"}:
            if not selected_text:
                return DirectEditResult(True, text, record_history=False, hint="当前没有选中内容")
            return DirectEditResult(
                True,
                text[:sel_start] + text[sel_end:],
                record_history=True,
                hint="已删除选中内容",
            )

        if lower_cmd in {"删除当前句", "删掉当前句"}:
            spans = self._sentence_spans(text)
            idx = self._locate_sentence_index(spans, cursor)
            if idx < 0:
                return DirectEditResult(True, text, record_history=False, hint="未找到当前句")
            start, end = spans[idx]
            return DirectEditResult(True, text[:start] + text[end:], record_history=True, hint="已删除当前句")

        if lower_cmd in {"删除上一句", "删掉上一句"}:
            spans = self._sentence_spans(text)
            idx = self._locate_sentence_index(spans, cursor)
            if idx <= 0:
                return DirectEditResult(True, text, record_history=False, hint="没有上一句可删除")
            start, end = spans[idx - 1]
            return DirectEditResult(True, text[:start] + text[end:], record_history=True, hint="已删除上一句")

        replace_match = re.match(
            r"^(?:把|将)\s*(.+?)\s*(?:改成|改为|替换成|替换为)\s*(.+)\s*$",
            cmd,
        )
        if replace_match:
            old = self._strip_command_quotes(replace_match.group(1))
            new = self._strip_command_quotes(replace_match.group(2))
            if not old:
                return DirectEditResult(True, text, record_history=False, hint="替换目标为空")
            if old not in text:
                return DirectEditResult(True, text, record_history=False, hint=f"未找到“{old}”")
            return DirectEditResult(
                True,
                text.replace(old, new, 1),
                record_history=True,
                hint="已替换",
            )

        insert_before_match = re.match(r"^在\s*(.+?)\s*(?:前面|前)\s*插入\s*(.+)\s*$", cmd)
        if insert_before_match:
            marker = self._strip_command_quotes(insert_before_match.group(1))
            payload = self._strip_command_quotes(insert_before_match.group(2))
            idx = text.find(marker)
            if idx < 0:
                return DirectEditResult(True, text, record_history=False, hint=f"未找到“{marker}”")
            return DirectEditResult(True, text[:idx] + payload + text[idx:], record_history=True, hint="已插入")

        insert_after_match = re.match(r"^在\s*(.+?)\s*(?:后面|后)\s*插入\s*(.+)\s*$", cmd)
        if insert_after_match:
            marker = self._strip_command_quotes(insert_after_match.group(1))
            payload = self._strip_command_quotes(insert_after_match.group(2))
            idx = text.find(marker)
            if idx < 0:
                return DirectEditResult(True, text, record_history=False, hint=f"未找到“{marker}”")
            end = idx + len(marker)
            return DirectEditResult(True, text[:end] + payload + text[end:], record_history=True, hint="已插入")

        prepend_match = re.match(r"^(?:在)?(?:开头|最前面)(?:插入|添加|加上)\s*(.+)\s*$", cmd)
        if prepend_match:
            payload = self._strip_command_quotes(prepend_match.group(1))
            return DirectEditResult(True, payload + text, record_history=True, hint="已在开头插入")

        append_match = re.match(r"^(?:在)?(?:结尾|末尾|最后)(?:插入|添加|加上|追加)\s*(.+)\s*$", cmd)
        if append_match:
            payload = self._strip_command_quotes(append_match.group(1))
            return DirectEditResult(True, text + payload, record_history=True, hint="已在结尾插入")

        append_simple_match = re.match(r"^(?:追加|添加|加上)\s*(.+)\s*$", cmd)
        if append_simple_match:
            payload = self._strip_command_quotes(append_simple_match.group(1))
            return DirectEditResult(True, text + payload, record_history=True, hint="已追加")

        punct_match = re.match(r"^(?:加|插入)\s*(句号|逗号|问号|感叹号|冒号|分号|引号)\s*$", cmd)
        if punct_match:
            punct = self._PUNCTUATION_MAP.get(punct_match.group(1), "")
            if punct:
                return DirectEditResult(True, text + punct, record_history=True, hint="已添加标点")

        if lower_cmd in {"全部大写", "全大写", "uppercase"}:
            return DirectEditResult(True, text.upper(), record_history=True, hint="已转为大写")
        if lower_cmd in {"全部小写", "全小写", "lowercase"}:
            return DirectEditResult(True, text.lower(), record_history=True, hint="已转为小写")
        if lower_cmd in {"首字母大写", "标题格式", "title case"}:
            return DirectEditResult(True, text.title(), record_history=True, hint="已转为首字母大写")
        if lower_cmd in {"加粗", "加粗选中", "bold", "bold that"}:
            if sel_end > sel_start:
                styled = text[:sel_start] + f"**{selected_text}**" + text[sel_end:]
            else:
                styled = f"**{text}**"
            return DirectEditResult(True, styled, record_history=True, hint="已加粗")
        if lower_cmd in {"斜体", "斜体选中", "italic", "italicize"}:
            if sel_end > sel_start:
                styled = text[:sel_start] + f"*{selected_text}*" + text[sel_end:]
            else:
                styled = f"*{text}*"
            return DirectEditResult(True, styled, record_history=True, hint="已设为斜体")

        delete_match = re.match(r"^(?:删除|删掉|去掉)\s*(.+)\s*$", cmd)
        if delete_match:
            target = self._strip_command_quotes(delete_match.group(1))
            if target in {"当前句", "上一句", "全部", "选中内容", "选中"}:
                return DirectEditResult(False)
            if target and target in text:
                return DirectEditResult(
                    True,
                    text.replace(target, "", 1),
                    record_history=True,
                    hint="已删除",
                )
            return DirectEditResult(True, text, record_history=False, hint=f"未找到“{target}”")

        count = self._parse_count_from_command(cmd)
        if lower_cmd in {"全选", "选中全部", "select all"}:
            return DirectEditResult(
                handled=True,
                mode="key_events",
                key_events=self._key_events(
                    IBus.KEY_a,
                    state=int(IBus.ModifierType.CONTROL_MASK),
                ),
                record_history=False,
                hint="已全选",
            )

        if lower_cmd in {"移动到开头", "跳到开头", "到开头", "行首", "到行首", "移动到行首"}:
            return DirectEditResult(
                handled=True,
                mode="key_events",
                key_events=self._key_events(IBus.KEY_Home),
                record_history=False,
                hint="已移动到开头",
            )
        if lower_cmd in {"移动到结尾", "跳到结尾", "到结尾", "行尾", "到行尾", "移动到行尾"}:
            return DirectEditResult(
                handled=True,
                mode="key_events",
                key_events=self._key_events(IBus.KEY_End),
                record_history=False,
                hint="已移动到结尾",
            )
        if lower_cmd in {"段首", "到段首", "移动到段首"}:
            return DirectEditResult(
                handled=True,
                mode="key_events",
                key_events=self._key_events(
                    IBus.KEY_Up,
                    state=int(IBus.ModifierType.CONTROL_MASK),
                ),
                record_history=False,
                hint="已尝试移动到段首",
            )
        if lower_cmd in {"段尾", "到段尾", "移动到段尾"}:
            return DirectEditResult(
                handled=True,
                mode="key_events",
                key_events=self._key_events(
                    IBus.KEY_Down,
                    state=int(IBus.ModifierType.CONTROL_MASK),
                ),
                record_history=False,
                hint="已尝试移动到段尾",
            )

        if re.match(r"^(?:向|往)?左(?:移|移动)?(?:\s*\d+|\s*[一二两三四五六七八九十])?(?:次|个字|个字符)?$", cmd) or lower_cmd in {"左移", "向左"}:
            return DirectEditResult(
                handled=True,
                mode="key_events",
                key_events=self._key_events(IBus.KEY_Left, repeat=count),
                record_history=False,
                hint=f"已左移{count}次",
            )
        if re.match(r"^(?:向|往)?右(?:移|移动)?(?:\s*\d+|\s*[一二两三四五六七八九十])?(?:次|个字|个字符)?$", cmd) or lower_cmd in {"右移", "向右"}:
            return DirectEditResult(
                handled=True,
                mode="key_events",
                key_events=self._key_events(IBus.KEY_Right, repeat=count),
                record_history=False,
                hint=f"已右移{count}次",
            )

        if lower_cmd in {"下一个词", "到下一个词", "移动到下一个词", "next word"}:
            return DirectEditResult(
                handled=True,
                mode="key_events",
                key_events=self._key_events(
                    IBus.KEY_Right,
                    state=int(IBus.ModifierType.CONTROL_MASK),
                    repeat=count,
                ),
                record_history=False,
                hint="已移动到下一个词",
            )
        if lower_cmd in {"上一个词", "到上一个词", "移动到上一个词", "previous word"}:
            return DirectEditResult(
                handled=True,
                mode="key_events",
                key_events=self._key_events(
                    IBus.KEY_Left,
                    state=int(IBus.ModifierType.CONTROL_MASK),
                    repeat=count,
                ),
                record_history=False,
                hint="已移动到上一个词",
            )

        if lower_cmd in {"选中下一个词", "选择下一个词"}:
            return DirectEditResult(
                handled=True,
                mode="key_events",
                key_events=self._key_events(
                    IBus.KEY_Right,
                    state=int(IBus.ModifierType.CONTROL_MASK | IBus.ModifierType.SHIFT_MASK),
                    repeat=count,
                ),
                record_history=False,
                hint="已尝试选中下一个词",
            )
        if lower_cmd in {"选中上一个词", "选择上一个词"}:
            return DirectEditResult(
                handled=True,
                mode="key_events",
                key_events=self._key_events(
                    IBus.KEY_Left,
                    state=int(IBus.ModifierType.CONTROL_MASK | IBus.ModifierType.SHIFT_MASK),
                    repeat=count,
                ),
                record_history=False,
                hint="已尝试选中上一个词",
            )

        return DirectEditResult(False)

    def _replace_surrounding_text(
        self,
        new_text: str,
        original_text: str,
        cursor_pos: int,
        record_history: bool = True,
        hint: str = "",
    ) -> bool:
        """用新文本替换当前 surrounding 区域"""
        try:
            if not self._is_engine_active():
                self._show_nonintrusive_error("当前输入法未激活，已取消上屏")
                return False

            # 防止录音期间用户继续编辑导致替换错位。
            live_text_obj, live_cursor, _ = self.get_surrounding_text()
            live_text = live_text_obj.get_text() if live_text_obj else ""
            if live_text != original_text or int(live_cursor) != int(cursor_pos):
                self._show_nonintrusive_error("输入框内容已变化，请重试")
                return False

            if new_text == original_text:
                if hint:
                    self._show_hint(hint)
                else:
                    self._clear_preedit()
                return False

            original_len = len(original_text)
            safe_cursor = max(0, min(int(cursor_pos), int(original_len)))
            safe_len = max(0, int(original_len))
            self.delete_surrounding_text(-safe_cursor, safe_len)
            GLib.timeout_add(
                40,
                self._finalize_surrounding_replace,
                new_text,
                original_text,
                int(cursor_pos),
                int(record_history),
                hint,
                4,
            )
            return False
        except Exception as exc:
            logger.warning("替换 surrounding text 失败: %s", exc)
            self._replace_capability_state = "unsupported"
            self._show_nonintrusive_error("当前输入框不支持替换文本")
            return False

    def _finalize_surrounding_replace(
        self,
        new_text: str,
        original_text: str,
        cursor_pos: int,
        record_history_int: int,
        hint: str,
        retries_left: int,
    ) -> bool:
        """删除后确认文本确实发生变化，再提交新文本，避免“删除失败 + 重复插入”"""
        try:
            if not self._is_engine_active():
                self._show_nonintrusive_error("当前输入法未激活，已取消上屏")
                return False

            live_text_obj, live_cursor, _ = self.get_surrounding_text()
            live_text = live_text_obj.get_text() if live_text_obj else ""
            unchanged = (
                original_text
                and live_text == original_text
                and int(live_cursor) == int(cursor_pos)
            )

            if unchanged and retries_left > 0:
                GLib.timeout_add(
                    40,
                    self._finalize_surrounding_replace,
                    new_text,
                    original_text,
                    int(cursor_pos),
                    int(record_history_int),
                    hint,
                    int(retries_left - 1),
                )
                return False

            if unchanged:
                self._replace_capability_state = "unsupported"
                self._show_nonintrusive_error("当前输入框不支持替换文本")
                return False

            self._replace_capability_state = "supported"
            if bool(record_history_int):
                self._push_undo_state(original_text)
            self._last_internal_edit_text = new_text
            self._commit_text(new_text, "voice_edit")
            if hint:
                GLib.timeout_add(30, self._show_hint, hint, 1200)
            return False
        except Exception as exc:
            logger.warning("确认替换结果失败: %s", exc)
            self._replace_capability_state = "unsupported"
            self._show_nonintrusive_error("当前输入框不支持替换文本")
            return False

    def _forward_key_to_rime(self, keyval, keycode, state) -> bool:
        """将按键事件转发给 Rime（使用 pyrime）"""
        if not self._rime_enabled:
            logger.info("Rime 未启用，按键不处理")
            return False

        # 懒加载初始化 Rime
        if not self._init_rime_session():
            logger.warning("Rime 初始化失败，按键不处理")
            return False

        try:
            # 将 IBus modifier 转换为 Rime modifier
            # IBus 和 Rime 都使用 X11 keysym 和类似的 modifier mask
            is_release = bool(state & IBus.ModifierType.RELEASE_MASK)

            # Rime 不处理 key release 事件
            if is_release:
                return False

            # 构建 Rime modifier mask
            rime_mask = 0
            if state & IBus.ModifierType.SHIFT_MASK:
                rime_mask |= 1 << 0  # kShiftMask
            if state & IBus.ModifierType.LOCK_MASK:
                rime_mask |= 1 << 1  # kLockMask
            if state & IBus.ModifierType.CONTROL_MASK:
                rime_mask |= 1 << 2  # kControlMask
            if state & IBus.ModifierType.MOD1_MASK:
                rime_mask |= 1 << 3  # kAltMask

            # 处理按键
            handled = self._rime_session.process_key(keyval, rime_mask)
            logger.info("Rime process_key: keyval=%s mask=%s handled=%s", keyval, rime_mask, handled)

            # 检查是否有提交的文本
            commit = self._rime_session.get_commit()
            if commit and commit.text:
                self._clear_preedit()
                self.hide_lookup_table()
                self.commit_text(IBus.Text.new_from_string(commit.text))
                logger.info("Rime 提交文本: %s", commit.text)

            # 更新预编辑和候选词
            context = self._rime_session.get_context()
            if context:
                self._update_rime_ui(context)
            else:
                self._clear_preedit()
                self.hide_lookup_table()

            return handled

        except Exception as exc:
            logger.error("Rime 处理按键失败: %s", exc)
            import traceback
            traceback.print_exc()
            return False

    def _update_rime_ui(self, context):
        """根据 Rime Context 更新 IBus UI"""
        try:
            # 更新预编辑文本
            composition = getattr(context, "composition", None)
            preedit_text = composition.preedit if composition and composition.preedit else ""
            if preedit_text:
                ibus_text = IBus.Text.new_from_string(preedit_text)
                # 添加下划线样式
                ibus_text.append_attribute(
                    IBus.AttrType.UNDERLINE,
                    IBus.AttrUnderline.SINGLE,
                    0,
                    len(preedit_text)
                )
                cursor_pos = composition.cursor_pos if composition else len(preedit_text)
                self.update_preedit_text(ibus_text, cursor_pos, True)
            else:
                self._clear_preedit()

            # 更新候选词列表
            menu = getattr(context, "menu", None)
            if not menu or not getattr(menu, "candidates", None):
                self.hide_lookup_table()
                return

            logger.debug("Rime menu: candidates=%d, page_size=%d, highlighted=%d",
                        len(menu.candidates),
                        menu.page_size, menu.highlighted_candidate_index)
            if menu.candidates:
                lookup_table = IBus.LookupTable.new(
                    page_size=menu.page_size,
                    cursor_pos=menu.highlighted_candidate_index,
                    cursor_visible=True,
                    round=False
                )

                for i, candidate in enumerate(menu.candidates):
                    text = candidate.text
                    if candidate.comment:
                        text = f"{text} {candidate.comment}"
                    lookup_table.append_candidate(IBus.Text.new_from_string(text))
                    logger.debug("  候选 %d: %s", i, text)

                self.update_lookup_table(lookup_table, True)
                logger.debug("update_lookup_table called with %d candidates", len(menu.candidates))
            else:
                self.hide_lookup_table()

        except Exception as exc:
            logger.warning("更新 Rime UI 失败: %s", exc)

    def _is_ibus_switch_hotkey(self, keyval, state) -> bool:
        """让输入法切换热键走 IBus 全局处理"""
        # 只拦截 Super+Space (输入法切换)，不拦截 Ctrl+Space (中英切换)
        if keyval == IBus.KEY_space and state & (IBus.ModifierType.SUPER_MASK | IBus.ModifierType.MOD4_MASK):
            return True
        if keyval in (IBus.KEY_Shift_L, IBus.KEY_Shift_R) and state & IBus.ModifierType.MOD1_MASK:
            return True
        if keyval in (IBus.KEY_Shift_L, IBus.KEY_Shift_R) and state & IBus.ModifierType.CONTROL_MASK:
            return True
        return False

    def _start_recording(self, long_mode: bool = False, edit_mode: bool = False):
        """开始录音"""
        if self._is_recording:
            return
        self._reload_runtime_config()

        try:
            import sounddevice as sd

            self._is_recording = True
            self._recording_long_mode = long_mode
            self._recording_edit_mode = edit_mode
            self._audio_frames.clear()
            self._stop_event.clear()

            # 清空队列
            while not self._audio_queue.empty():
                try:
                    self._audio_queue.get_nowait()
                except queue.Empty:
                    break

            device = self._resolve_input_device(sd)
            sample_rate = self._resolve_sample_rate(sd, device, CONFIGURED_SAMPLE_RATE)
            self._native_sample_rate = sample_rate
            block_size = int(sample_rate * BLOCK_MS / 1000)

            def audio_callback(indata, frame_count, time_info, status):
                if status:
                    logger.warning(f"音频状态: {status}")
                try:
                    self._audio_queue.put_nowait(indata.copy())
                except queue.Full:
                    pass

            # 创建音频流
            self._stream = sd.InputStream(
                samplerate=sample_rate,
                blocksize=block_size,
                device=device,
                channels=1,
                dtype='int16',
                callback=audio_callback,
            )
            self._stream.start()

            # 启动采集线程
            def capture_loop():
                while not self._stop_event.is_set():
                    try:
                        frame = self._audio_queue.get(timeout=0.1)
                        self._audio_frames.append(frame)
                    except queue.Empty:
                        continue

            self._capture_thread = threading.Thread(target=capture_loop, daemon=True)
            self._capture_thread.start()

            # 显示录音状态
            if edit_mode:
                self._update_auxiliary_status(self._build_edit_env_status(self._edit_snapshot))
                # 编辑模式也可能调用本地 SLM，录音期间预热减少松键后等待。
                self._slm_polisher.prewarm(long_mode=True)
            elif long_mode:
                self._update_preedit("🎤 录音中(长句)...")
                # 录音期间并行预加载本地一次性 SLM，减少松键后的等待时间
                self._slm_polisher.prewarm(long_mode=True)
            else:
                self._update_preedit("🎤 录音中...")
            if edit_mode:
                mode_name = "edit"
            elif long_mode:
                mode_name = "long"
            else:
                mode_name = "normal"
            logger.info("开始录音 mode=%s", mode_name)

            # 确保ASR已初始化
            self._ensure_asr_ready()

        except Exception as e:
            logger.error(f"启动录音失败: {e}")
            self._is_recording = False
            self._recording_long_mode = False
            self._recording_edit_mode = False
            self._update_preedit(f"❌ 录音失败: {e}")
            GLib.timeout_add(2000, self._clear_preedit)

    def _stop_recording(self):
        """停止录音（不转录）"""
        if not self._is_recording:
            return

        long_mode = self._recording_long_mode
        edit_mode = self._recording_edit_mode
        self._stop_event.set()

        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except:
                pass
            self._stream = None

        if self._capture_thread:
            self._capture_thread.join(timeout=1.0)
            self._capture_thread = None

        self._is_recording = False
        self._recording_long_mode = False
        self._recording_edit_mode = False
        self._clear_preedit()
        if long_mode or edit_mode:
            self._slm_polisher.release()
        if edit_mode:
            self._edit_snapshot = None
        logger.info("录音已停止")

    def _stop_and_transcribe(self):
        """停止录音并转录"""
        if not self._is_recording:
            return

        long_mode = self._recording_long_mode
        edit_mode = self._recording_edit_mode
        edit_snapshot = self._edit_snapshot if edit_mode else None

        # 停止录音
        self._stop_event.set()

        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except:
                pass
            self._stream = None

        if self._capture_thread:
            self._capture_thread.join(timeout=1.0)
            self._capture_thread = None

        self._is_recording = False
        self._recording_long_mode = False
        self._recording_edit_mode = False
        self._edit_snapshot = None

        if edit_mode and not self._is_engine_active():
            self._clear_preedit()
            self._show_nonintrusive_error("当前输入法已非活动状态，已取消编辑")
            if long_mode or edit_mode:
                self._slm_polisher.release()
            return

        # 检查是否有音频数据
        if not self._audio_frames:
            self._clear_preedit()
            if long_mode or edit_mode:
                self._slm_polisher.release()
            return

        # 合并音频
        audio_data = np.concatenate(self._audio_frames).flatten()
        self._audio_frames.clear()

        duration = len(audio_data) / self._native_sample_rate
        if edit_mode:
            mode_name = "edit"
        elif long_mode:
            mode_name = "long"
        else:
            mode_name = "normal"
        logger.info("录音完成，时长: %.2f秒, mode=%s", duration, mode_name)

        # 检查是否太短
        if duration < 0.3:
            self._clear_preedit()
            if long_mode or edit_mode:
                self._slm_polisher.release()
            return

        # 显示识别中状态
        if edit_mode:
            self._update_auxiliary_status("⏳ 识别编辑指令中...")
        elif long_mode:
            self._update_preedit("⏳ 识别+润色中...")
        else:
            self._update_preedit("⏳ 识别中...")

        # 在后台线程中转录
        def do_transcribe():
            try:
                # 重采样
                audio_16k = resample_audio(audio_data, self._native_sample_rate, SAMPLE_RATE)

                # 写入临时文件
                with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
                    temp_path = f.name
                    from app.wave_writer import write_wav
                    write_wav(Path(temp_path), audio_16k.tobytes(), SAMPLE_RATE)

                try:
                    # 等待共享ASR就绪
                    cls = type(self)
                    if not cls._shared_asr_ready.wait(timeout=30):
                        with cls._shared_asr_lock:
                            err = cls._shared_asr_init_error or "ASR未就绪"
                        GLib.idle_add(self._show_error, err)
                        return

                    with cls._shared_asr_lock:
                        asr_server = cls._shared_asr_server
                    if asr_server is None:
                        GLib.idle_add(self._show_error, "ASR实例不可用")
                        return

                    # 转录
                    asr_start = time.perf_counter()
                    result = asr_server.transcribe_audio(
                        temp_path,
                        options=self._asr_options,
                    )
                    asr_ms = (time.perf_counter() - asr_start) * 1000.0

                    if result.get("success"):
                        text = result.get("text", "").strip()
                        if text:
                            final_text = text
                            slm_ms = 0.0
                            slm_reason = "not_used"
                            slm_used = False

                            if edit_mode:
                                if edit_snapshot is None:
                                    logger.warning("编辑模式缺少上下文快照")
                                    GLib.idle_add(self._show_nonintrusive_error, "编辑上下文获取失败，请重试")
                                    return

                                rewritten_instruction = self._rewrite_insert_generation_instruction(text)
                                direct_result = self._apply_direct_edit_command(edit_snapshot, text)
                                if direct_result.handled:
                                    if direct_result.mode == "key_events":
                                        GLib.idle_add(
                                            self._run_key_events,
                                            direct_result.key_events,
                                            direct_result.hint,
                                        )
                                    elif direct_result.mode == "commit_only":
                                        if direct_result.record_history:
                                            self._push_undo_state(edit_snapshot.text)
                                        if direct_result.new_text:
                                            self._last_internal_edit_text = self._predict_commit_result(
                                                edit_snapshot,
                                                direct_result.new_text,
                                            )
                                            GLib.idle_add(
                                                self._commit_text,
                                                direct_result.new_text,
                                                "voice_edit",
                                            )
                                        if direct_result.hint:
                                            GLib.idle_add(self._show_hint, direct_result.hint, 1200)
                                    elif direct_result.mode == "no_replace":
                                        GLib.idle_add(self._show_hint, direct_result.hint, 1200)
                                    else:
                                        target_text = (
                                            edit_snapshot.text
                                            if direct_result.new_text is None
                                            else direct_result.new_text
                                        )
                                        GLib.idle_add(
                                            self._replace_surrounding_text,
                                            target_text,
                                            edit_snapshot.text,
                                            edit_snapshot.cursor_pos,
                                            direct_result.record_history,
                                            direct_result.hint,
                                        )
                                    logger.info(
                                        "编辑模式命中确定性命令: instruction=%s mode=%s hint=%s",
                                        text,
                                        direct_result.mode,
                                        direct_result.hint,
                                    )
                                    return

                                GLib.idle_add(self._update_auxiliary_status, "✍️ 正在编辑...")
                                slm_instruction = rewritten_instruction or text
                                if rewritten_instruction:
                                    logger.info(
                                        "编辑模式命中输入生成指令: instruction=%s rewritten=%s",
                                        text,
                                        slm_instruction,
                                    )
                                edited_text, metrics = self._slm_polisher.edit_with_instruction(
                                    context_text=edit_snapshot.text,
                                    instruction=slm_instruction,
                                    cursor_pos=edit_snapshot.cursor_pos,
                                    anchor_pos=edit_snapshot.anchor_pos,
                                    selected_text=edit_snapshot.selected_text,
                                )
                                slm_ms = metrics.latency_ms
                                slm_reason = metrics.reason
                                slm_used = metrics.used

                                if self._slm_polisher.is_failure_reason(metrics.reason):
                                    logger.warning(
                                        "编辑模式 SLM 调用失败: reason=%s",
                                        metrics.reason,
                                    )
                                    GLib.idle_add(
                                        self._show_nonintrusive_error,
                                        self._slm_polisher.format_failure_message(metrics.reason),
                                    )
                                    return

                                logger.info(
                                    "转录流水线 mode=%s asr_ms=%.2f slm_used=%s slm_ms=%.2f fallback_reason=%s",
                                    "edit",
                                    asr_ms,
                                    slm_used,
                                    slm_ms,
                                    slm_reason,
                                )
                                GLib.idle_add(
                                    self._replace_surrounding_text,
                                    edited_text,
                                    edit_snapshot.text,
                                    edit_snapshot.cursor_pos,
                                    True,
                                    "",
                                )
                                return

                            if long_mode:
                                should_polish = self._slm_polisher.should_polish(
                                    text,
                                    long_mode=True,
                                )
                                if should_polish:
                                    GLib.idle_add(self._update_preedit, "✨ 润色中...")
                                    polished_text, metrics = self._slm_polisher.polish(
                                        text,
                                        long_mode=True,
                                    )
                                    slm_ms = metrics.latency_ms
                                    slm_reason = metrics.reason
                                    slm_used = metrics.used
                                    if self._slm_polisher.is_failure_reason(metrics.reason):
                                        logger.warning(
                                            "长句 SLM 调用失败: reason=%s",
                                            metrics.reason,
                                        )
                                        logger.info(
                                            "转录流水线 mode=%s asr_ms=%.2f slm_used=%s slm_ms=%.2f fallback_reason=%s",
                                            "long",
                                            asr_ms,
                                            slm_used,
                                            slm_ms,
                                            slm_reason,
                                        )
                                        GLib.idle_add(
                                            self._show_error,
                                            self._slm_polisher.format_failure_message(
                                                metrics.reason
                                            ),
                                        )
                                        return
                                    final_text = polished_text
                                else:
                                    slm_reason = (
                                        "disabled"
                                        if not self._slm_polisher.enabled
                                        else "too_short"
                                    )

                            logger.info(
                                "转录流水线 mode=%s asr_ms=%.2f slm_used=%s slm_ms=%.2f fallback_reason=%s",
                                "long" if long_mode else "normal",
                                asr_ms,
                                slm_used,
                                slm_ms,
                                slm_reason,
                            )
                            GLib.idle_add(self._commit_text, final_text)
                        else:
                            logger.info(
                                "转录流水线 mode=%s asr_ms=%.2f slm_used=false slm_ms=0.00 fallback_reason=empty_asr_text",
                                "edit" if edit_mode else ("long" if long_mode else "normal"),
                                asr_ms,
                            )
                            GLib.idle_add(self._clear_preedit)
                    else:
                        error = result.get("error", "未知错误")
                        GLib.idle_add(self._show_error, error)
                finally:
                    # 删除临时文件
                    try:
                        os.unlink(temp_path)
                    except:
                        pass
                    if long_mode or edit_mode:
                        self._slm_polisher.release()

            except Exception as e:
                logger.error(f"转录失败: {e}")
                GLib.idle_add(self._show_error, str(e))

        threading.Thread(target=do_transcribe, daemon=True).start()

    def _update_preedit(self, text: str):
        """更新预编辑文本"""
        preedit = IBus.Text.new_from_string(text)
        self.update_preedit_text(preedit, len(text), True)

    @staticmethod
    def _clip_probe_text(text: str, limit: int = 48) -> str:
        """裁剪并清洗 probe 输出，避免回填文本过长"""
        cleaned = (text or "").replace("\n", "⏎").replace("\t", "⇥")
        cleaned = " ".join(cleaned.split())
        if len(cleaned) <= limit:
            return cleaned
        return f"{cleaned[:limit]}..."

    @staticmethod
    def _extract_sentence_window(text: str, cursor_pos: int) -> tuple[str, str]:
        """提取当前句与上一句（宽松规则，面向调试）"""
        if not text:
            return "", ""

        delimiters = set("。！？!?；;.\n")
        spans: list[tuple[int, int]] = []
        start = 0
        for idx, ch in enumerate(text):
            if ch in delimiters:
                end = idx + 1
                if end > start:
                    spans.append((start, end))
                start = end
        if start < len(text):
            spans.append((start, len(text)))
        if not spans:
            return text.strip(), ""

        cursor = max(0, min(cursor_pos, len(text)))
        current_idx = len(spans) - 1
        for i, (seg_start, seg_end) in enumerate(spans):
            if seg_start <= cursor <= seg_end:
                current_idx = i
                break

        cur_start, cur_end = spans[current_idx]
        current_sentence = text[cur_start:cur_end].strip()

        previous_sentence = ""
        if current_idx > 0:
            prev_start, prev_end = spans[current_idx - 1]
            previous_sentence = text[prev_start:prev_end].strip()

        return current_sentence, previous_sentence

    def _probe_surrounding_text(self):
        """调试：读取 surrounding text 并回填到当前输入框"""
        try:
            ibus_text, cursor_pos, anchor_pos = self.get_surrounding_text()
            surrounding = ibus_text.get_text() if ibus_text else ""

            text_len = len(surrounding)
            cursor = max(0, min(int(cursor_pos), text_len))
            anchor = max(0, min(int(anchor_pos), text_len))

            selected = ""
            if anchor != cursor:
                sel_start, sel_end = sorted((anchor, cursor))
                selected = surrounding[sel_start:sel_end]

            current_sentence, previous_sentence = self._extract_sentence_window(surrounding, cursor)
            has_surrounding_cap = bool(
                self._client_capabilities & int(IBus.Capabilite.SURROUNDING_TEXT)
            )

            probe_text = (
                "[VT-SURR "
                f"cap={int(has_surrounding_cap)} len={text_len} cursor={cursor} anchor={anchor} "
                f"prev='{self._clip_probe_text(previous_sentence)}' "
                f"cur='{self._clip_probe_text(current_sentence)}' "
                f"sel='{self._clip_probe_text(selected)}' "
                f"all='{self._clip_probe_text(surrounding, 72)}']"
            )
            logger.info("SURROUNDING_PROBE %s", probe_text)
            self._commit_text(probe_text)
        except Exception as exc:
            logger.warning("SURROUNDING_PROBE failed: %s", exc)
            self._commit_text(f"[VT-SURR error='{self._clip_probe_text(str(exc), 64)}']")

    def _clear_preedit(self):
        """清除预编辑文本"""
        self.update_preedit_text(IBus.Text.new_from_string(""), 0, False)
        self._clear_auxiliary_text()
        return False  # 用于GLib.timeout_add

    def _commit_text(self, text: str, mutation_source: str = "app_commit"):
        """提交文本到应用"""
        self._clear_preedit()
        self.commit_text(IBus.Text.new_from_string(text))
        if mutation_source == "voice_edit":
            self._last_text_change_source = "voice_edit"
        else:
            self._last_text_change_source = "app_commit"
            self._last_internal_edit_text = None
        logger.info(f"已提交文本: {text}")
        return False

    def _show_error(self, error: str):
        """显示错误信息"""
        self._update_preedit(f"❌ {error}")
        GLib.timeout_add(2000, self._clear_preedit)
        return False
