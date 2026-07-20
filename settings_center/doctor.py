"""Self-diagnostics used by both the GUI and ``vocotype-doctor``."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable

from .config_service import (
    fcitx_backend_path,
    ibus_config_path,
    load_audio_config,
    load_json_mapping,
    terms_path,
)


@dataclass(frozen=True)
class DoctorCheck:
    check_id: str
    title: str
    status: str
    summary: str
    details: str = ""
    repair_hint: str = ""

    @property
    def ok(self) -> bool:
        return self.status in {"pass", "info"}


def _check(check_id: str, title: str, fn: Callable[[], DoctorCheck]) -> DoctorCheck:
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - diagnostics must continue.
        return DoctorCheck(check_id, title, "fail", f"检查异常：{exc}")


def _run(argv: list[str], timeout: float = 5.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=os.environ.copy(),
    )


def _pass(check_id: str, title: str, summary: str, details: str = "") -> DoctorCheck:
    return DoctorCheck(check_id, title, "pass", summary, details)


def _info(check_id: str, title: str, summary: str, details: str = "") -> DoctorCheck:
    return DoctorCheck(check_id, title, "info", summary, details)


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _warn(
    check_id: str,
    title: str,
    summary: str,
    details: str = "",
    repair_hint: str = "",
) -> DoctorCheck:
    return DoctorCheck(check_id, title, "warn", summary, details, repair_hint)


def _fail(
    check_id: str,
    title: str,
    summary: str,
    details: str = "",
    repair_hint: str = "",
) -> DoctorCheck:
    return DoctorCheck(check_id, title, "fail", summary, details, repair_hint)


def run_doctor(*, include_slm_probe: bool = False) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    fcitx_module_candidates = [
        Path.home() / ".local/lib/fcitx5/vocotype.so",
        Path.home() / ".local/lib64/fcitx5/vocotype.so",
    ]
    fcitx_addon_path = Path.home() / ".local/share/fcitx5/addon/vocotype.conf"
    fcitx_service_path = (
        Path.home() / ".config/systemd/user/vocotype-fcitx5-backend.service"
    )
    ibus_engine_path = Path.home() / ".local/libexec/ibus-engine-vocotype"
    ibus_component_paths = [
        Path.home() / ".local/share/ibus/component/vocotype.xml",
        Path("/usr/share/ibus/component/vocotype.xml"),
    ]
    fcitx_vocotype_installed = bool(
        fcitx_addon_path.exists()
        and any(path.exists() for path in fcitx_module_candidates)
    )
    ibus_vocotype_installed = bool(
        ibus_engine_path.exists()
        and any(path.exists() for path in ibus_component_paths)
    )
    fcitx_installed = bool(
        shutil.which("fcitx5")
        or fcitx_service_path.exists()
        or fcitx_vocotype_installed
    )
    ibus_installed = bool(
        shutil.which("ibus")
        or ibus_vocotype_installed
    )

    def python_check() -> DoctorCheck:
        version = sys.version_info
        summary = f"Python {version.major}.{version.minor}.{version.micro}"
        if version.major == 3 and 11 <= version.minor <= 12:
            return _pass("python", "Python 运行时", summary, sys.executable)
        return _fail(
            "python",
            "Python 运行时",
            f"{summary} 不在支持范围 3.11–3.12",
            sys.executable,
            "使用安装器的用户级 Python 3.12 虚拟环境。",
        )

    checks.append(_check("python", "Python 运行时", python_check))

    def deps_check() -> DoctorCheck:
        required = {
            "sounddevice": "录音",
            "soundfile": "音频文件",
            "funasr_onnx": "ASR",
            "yaml": "术语库",
            "itn.chinese.inverse_normalizer": "ITN",
            "modelscope": "模型下载",
        }
        missing = [
            f"{name}（{purpose}）"
            for name, purpose in required.items()
            if not _module_available(name)
        ]
        if not missing:
            return _pass("dependencies", "Python 依赖", "核心依赖均可导入")
        return _fail(
            "dependencies",
            "Python 依赖",
            "缺少运行依赖",
            "\n".join(missing),
            "重新运行安装/修复。",
        )

    checks.append(_check("dependencies", "Python 依赖", deps_check))

    def fcitx_binary_check() -> DoctorCheck:
        executable = shutil.which("fcitx5")
        if executable:
            return _pass("fcitx", "Fcitx 5", "已检测到 Fcitx 5", executable)
        if ibus_vocotype_installed:
            return _info("fcitx", "Fcitx 5", "当前是 IBus-only 环境，可忽略 Fcitx 检查")
        return _warn(
            "fcitx",
            "Fcitx 5",
            "未检测到 fcitx5 命令",
            repair_hint="使用 Fcitx 时请先安装 Fcitx 5。",
        )

    checks.append(_check("fcitx", "Fcitx 5", fcitx_binary_check))

    def module_check() -> DoctorCheck:
        existing = [path for path in fcitx_module_candidates if path.is_file()]
        if existing and fcitx_addon_path.is_file():
            return _pass(
                "fcitx_module",
                "Fcitx 全局模块",
                "模块和 addon 元数据已安装",
                "\n".join(str(path) for path in [*existing, fcitx_addon_path]),
            )
        if ibus_vocotype_installed and not fcitx_vocotype_installed:
            return _info(
                "fcitx_module",
                "Fcitx 全局模块",
                "IBus-only 环境未安装 Fcitx module",
            )
        return _fail(
            "fcitx_module",
            "Fcitx 全局模块",
            "全局模块安装不完整",
            f"module={existing or 'missing'}\naddon={fcitx_addon_path if fcitx_addon_path.exists() else 'missing'}",
            "在设置中心点击“安装/修复 Fcitx 5”。",
        )

    checks.append(_check("fcitx_module", "Fcitx 全局模块", module_check))

    def ibus_check() -> DoctorCheck:
        components = [path for path in ibus_component_paths if path.is_file()]
        if ibus_engine_path.is_file() and components:
            return _pass(
                "ibus_engine",
                "IBus 引擎",
                "IBus launcher 和 component 已安装",
                "\n".join(str(path) for path in [ibus_engine_path, *components]),
            )
        if fcitx_vocotype_installed and not ibus_vocotype_installed:
            return _info("ibus_engine", "IBus 引擎", "Fcitx-only 环境未安装 IBus 引擎")
        if ibus_installed:
            return _warn(
                "ibus_engine",
                "IBus 引擎",
                "IBus 安装不完整",
                f"launcher={ibus_engine_path if ibus_engine_path.exists() else 'missing'}\ncomponent={components or 'missing'}",
                "从设置中心启动 IBus 安装器完成修复。",
            )
        return _info("ibus_engine", "IBus 引擎", "未检测到 IBus 环境")

    checks.append(_check("ibus_engine", "IBus 引擎", ibus_check))

    def legacy_check() -> DoctorCheck:
        legacy = Path.home() / ".local/share/fcitx5/inputmethod/vocotype.conf"
        if ibus_vocotype_installed and not fcitx_vocotype_installed:
            return _info("legacy_entry", "旧版输入法条目", "IBus-only 环境无需检查")
        if not legacy.exists():
            return _pass("legacy_entry", "旧版输入法条目", "未发现旧版独立输入法条目")
        return _warn(
            "legacy_entry",
            "旧版输入法条目",
            "仍存在旧版 VoCoType 输入法描述",
            str(legacy),
            f"删除 {legacy} 后重启 Fcitx。",
        )

    checks.append(_check("legacy_entry", "旧版输入法条目", legacy_check))

    def service_check() -> DoctorCheck:
        if ibus_vocotype_installed and not fcitx_vocotype_installed:
            return _info("service", "Fcitx 后台服务", "IBus-only 环境无需该服务")
        if shutil.which("systemctl") is None:
            return _warn("service", "后台服务", "systemctl 不可用，无法自动判断")
        if not os.environ.get("DBUS_SESSION_BUS_ADDRESS") and not os.environ.get(
            "XDG_RUNTIME_DIR"
        ):
            return _warn(
                "service",
                "后台服务",
                "当前进程不在桌面用户会话中，无法连接 systemd user bus",
                repair_hint="请从桌面应用菜单运行设置中心后重试。",
            )
        result = _run(["systemctl", "--user", "is-active", "vocotype-fcitx5-backend.service"])
        state = result.stdout.strip() or result.stderr.strip() or f"exit={result.returncode}"
        if result.returncode == 0 and state == "active":
            return _pass("service", "后台服务", "vocotype-fcitx5-backend.service 正在运行")
        return _fail(
            "service",
            "后台服务",
            f"服务状态：{state}",
            repair_hint="点击“重启后台服务”，或执行 systemctl --user restart vocotype-fcitx5-backend.service。",
        )

    checks.append(_check("service", "后台服务", service_check))

    def socket_check() -> DoctorCheck:
        path = "/tmp/vocotype-fcitx5.sock"
        if ibus_vocotype_installed and not fcitx_vocotype_installed:
            return _info("socket", "Fcitx 后端 IPC", "IBus-only 环境无需该 socket")
        if not Path(path).exists():
            return _fail("socket", "后端 IPC", "Unix socket 不存在", path, "先启动后台服务。")
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(2.0)
        try:
            client.connect(path)
            client.sendall(b'{"type":"ping"}')
            client.shutdown(socket.SHUT_WR)
            response = client.recv(4096).decode("utf-8", errors="replace")
        finally:
            client.close()
        try:
            payload = json.loads(response)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict) and payload.get("pong") is True:
            return _pass("socket", "后端 IPC", "后端 ping 成功", response.strip())
        return _fail("socket", "后端 IPC", "后端返回了非预期响应", response.strip())

    checks.append(_check("socket", "后端 IPC", socket_check))

    def config_check() -> DoctorCheck:
        problems: list[str] = []
        valid: list[str] = []
        for path in (fcitx_backend_path(), ibus_config_path()):
            if not path.exists():
                continue
            try:
                load_json_mapping(path)
                valid.append(str(path))
            except Exception as exc:  # noqa: BLE001
                problems.append(f"{path}: {exc}")
        if problems:
            return _fail(
                "runtime_config",
                "运行配置",
                "发现无效 JSON 配置",
                "\n".join(problems),
                "在设置中心重新保存一次配置。",
            )
        if valid:
            return _pass("runtime_config", "运行配置", "运行配置可解析", "\n".join(valid))
        return _warn("runtime_config", "运行配置", "尚未创建运行配置；将使用默认值")

    checks.append(_check("runtime_config", "运行配置", config_check))

    def terms_check() -> DoctorCheck:
        path = terms_path()
        if not path.exists():
            return _warn("terms", "用户词典", "术语库尚未创建", str(path))
        try:
            import yaml
            value = yaml.safe_load(path.read_text(encoding="utf-8"))
            if value is not None and not isinstance(value, dict):
                raise ValueError("顶层必须是映射")
        except Exception as exc:  # noqa: BLE001
            return _fail("terms", "用户词典", "术语库 YAML 无效", str(exc), "在用户词典页面修正后保存。")
        return _pass("terms", "用户词典", "术语库可解析", str(path))

    checks.append(_check("terms", "用户词典", terms_check))

    def microphone_check() -> DoctorCheck:
        try:
            import sounddevice as sd

            devices = list(sd.query_devices())
            inputs = [
                (index, str(item.get("name", "")))
                for index, item in enumerate(devices)
                if int(item.get("max_input_channels", 0)) > 0
            ]
            configured = load_audio_config()
        except Exception as exc:  # noqa: BLE001
            return _fail("microphone", "麦克风", "无法枚举录音设备", str(exc))
        if not inputs:
            return _fail("microphone", "麦克风", "没有可用的输入设备")

        configured_name = str(configured.get("device_name") or "")
        configured_id = configured.get("device_id")
        selected = next(
            (
                (device_id, name)
                for device_id, name in inputs
                if (configured_name and name == configured_name)
                or (configured_id is not None and device_id == configured_id)
            ),
            None,
        )
        details = "\n".join(f"[{device_id}] {name}" for device_id, name in inputs[:20])
        if configured_name or configured_id is not None:
            if selected is None:
                return _warn(
                    "microphone",
                    "麦克风",
                    "已配置的输入设备当前不可用",
                    f"configured={configured_name or configured_id}\n{details}",
                    "在“语音识别与 ITN”页面重新选择并测试麦克风。",
                )
            return _pass(
                "microphone",
                "麦克风",
                f"已配置并检测到输入设备：{selected[1]}",
                details,
            )
        return _warn(
            "microphone",
            "麦克风",
            f"检测到 {len(inputs)} 个输入设备，但尚未固定选择",
            details,
            "在设置中心选择设备并保存，避免系统默认设备变化。",
        )

    checks.append(_check("microphone", "麦克风", microphone_check))

    def normalization_check() -> DoctorCheck:
        try:
            from app.text_normalizer import normalize_text
            result = normalize_text("二零二六年五月十一号下午三点二十分跑了三百二十米花了一百二十八元")
        except Exception as exc:  # noqa: BLE001
            return _fail("normalization", "ITN 预览", "归一化运行失败", str(exc))
        return _pass("normalization", "ITN 预览", "归一化运行成功", result)

    checks.append(_check("normalization", "ITN 预览", normalization_check))

    if include_slm_probe:
        def slm_check() -> DoctorCheck:
            from .config_service import load_runtime_config
            from app.slm_polisher import SLMPolisher
            config = load_runtime_config().get("slm", {})
            if not isinstance(config, dict) or not config.get("enabled"):
                return _warn("slm", "AI 润色", "AI 润色未启用")
            polisher = SLMPolisher({**config, "min_chars": 1})
            output, metrics = polisher.polish("这是一次连接测试。", long_mode=True)
            if metrics.reason == "ok":
                return _pass("slm", "AI 润色", "远程/本地模型调用成功", output)
            return _fail("slm", "AI 润色", f"调用失败：{metrics.reason}")

        checks.append(_check("slm", "AI 润色", slm_check))

    return checks


def doctor_summary(checks: Iterable[DoctorCheck]) -> dict[str, int]:
    result = {"pass": 0, "info": 0, "warn": 0, "fail": 0}
    for check in checks:
        result[check.status] = result.get(check.status, 0) + 1
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="VoCoType self-diagnostics")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    parser.add_argument("--probe-slm", action="store_true", help="perform a real SLM request")
    args = parser.parse_args(argv)
    checks = run_doctor(include_slm_probe=args.probe_slm)
    if args.json:
        print(json.dumps({"summary": doctor_summary(checks), "checks": [asdict(item) for item in checks]}, ensure_ascii=False, indent=2))
    else:
        icons = {"pass": "✓", "info": "·", "warn": "!", "fail": "✗"}
        for item in checks:
            print(f"{icons.get(item.status, '?')} {item.title}: {item.summary}")
            if item.details:
                print("  " + item.details.replace("\n", "\n  "))
            if item.repair_hint:
                print(f"  建议：{item.repair_hint}")
        print(json.dumps(doctor_summary(checks), ensure_ascii=False))
    return 1 if any(item.status == "fail" for item in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
