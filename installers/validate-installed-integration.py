#!/usr/bin/env python3
"""Strict post-install validation for VoCoType desktop integrations."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.download_models import inspect_required_models  # noqa: E402
from app.fcitx_session import restart_fcitx_session  # noqa: E402

SOCKET_PATH = Path("/tmp/vocotype-fcitx5.sock")
SERVICE_NAME = "vocotype-fcitx5-backend.service"


def emit(message: str) -> None:
    print(message, flush=True)


def require_file(paths: list[Path], label: str, *, executable: bool = False) -> Path:
    for path in paths:
        if path.is_file() and (not executable or os.access(path, os.X_OK)):
            emit(f"✅ {label}: {path}")
            return path
    rendered = "\n".join(str(path) for path in paths)
    raise RuntimeError(f"{label} 不存在或不可用；检查路径：\n{rendered}")


def validate_models() -> None:
    status = inspect_required_models()
    incomplete = {
        name: item for name, item in status.items() if not bool(item.get("complete"))
    }
    if incomplete:
        details = "\n".join(
            f"{name}: {item['path']}；缺少 {', '.join(item['missing'])}"
            for name, item in incomplete.items()
        )
        raise RuntimeError(f"必需模型不完整：\n{details}")
    emit("✅ 必需模型: ASR、VAD、标点均完整")


def run(command: list[str], *, timeout: float = 15.0) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    if command and Path(command[0]).name.startswith("fcitx5"):
        environment.pop("FCITX_ADDON_DIRS", None)
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=environment,
    )


def service_properties() -> dict[str, str]:
    result = run(
        [
            "systemctl",
            "--user",
            "show",
            SERVICE_NAME,
            "-p",
            "LoadState",
            "-p",
            "ActiveState",
            "-p",
            "SubState",
            "-p",
            "NRestarts",
            "-p",
            "ExecMainStatus",
            "-p",
            "MainPID",
        ]
    )
    if result.returncode != 0:
        raise RuntimeError(
            "无法查询 systemd 用户服务：" + (result.stderr.strip() or result.stdout.strip())
        )
    properties: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            properties[key] = value
    return properties


def service_journal() -> str:
    journalctl = shutil.which("journalctl")
    if journalctl is None:
        return ""
    result = run(
        [journalctl, "--user", "-u", SERVICE_NAME, "-n", "80", "--no-pager"],
        timeout=20.0,
    )
    return (result.stdout + result.stderr).strip()


def ping_backend() -> str:
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(2.0)
    try:
        client.connect(str(SOCKET_PATH))
        client.sendall(b'{"type":"ping"}')
        client.shutdown(socket.SHUT_WR)
        response = client.recv(4096).decode("utf-8", errors="replace")
    finally:
        client.close()
    payload = json.loads(response)
    if not isinstance(payload, dict) or payload.get("pong") is not True:
        raise RuntimeError(f"IPC ping 返回异常：{response}")
    return response


def wait_for_fcitx_backend(timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    latest: dict[str, str] = {}
    while time.monotonic() < deadline:
        latest = service_properties()
        active = latest.get("ActiveState", "unknown")
        sub = latest.get("SubState", "unknown")
        if sub == "auto-restart" or active == "failed":
            raise RuntimeError(
                "后台服务正在崩溃/重启："
                f"ActiveState={active}, SubState={sub}, "
                f"NRestarts={latest.get('NRestarts', '0')}, "
                f"ExecMainStatus={latest.get('ExecMainStatus', 'unknown')}\n"
                f"{service_journal()}"
            )
        if active == "active" and sub == "running" and SOCKET_PATH.is_socket():
            response = ping_backend()
            emit(f"✅ 后台服务与 IPC 已就绪: {response}")
            return
        time.sleep(0.5)
    raise RuntimeError(
        "等待后台 IPC 超时："
        + ", ".join(f"{key}={value}" for key, value in latest.items())
        + (f"\n{service_journal()}" if service_journal() else "")
    )


def validate_fcitx(runtime_root: Path, timeout_s: float) -> None:
    home = Path.home()
    require_file(
        [
            home / ".local/lib/fcitx5/vocotype.so",
            home / ".local/lib64/fcitx5/vocotype.so",
            Path("/usr/lib/fcitx5/vocotype.so"),
            Path("/usr/lib64/fcitx5/vocotype.so"),
            *sorted(Path("/usr/lib").glob("*/fcitx5/vocotype.so")),
        ],
        "Fcitx module",
    )
    require_file(
        [
            home / ".local/share/fcitx5/addon/vocotype.conf",
            Path("/usr/share/fcitx5/addon/vocotype.conf"),
        ],
        "Fcitx addon 元数据",
    )
    require_file(
        [home / ".config/systemd/user" / SERVICE_NAME],
        "Fcitx 后台服务定义",
    )
    require_file(
        [home / ".local/bin/vocotype-fcitx5-backend"],
        "Fcitx 后台启动器",
        executable=True,
    )
    require_file(
        [runtime_root / "backend/fcitx5_server.py"],
        "Fcitx 后端代码",
    )
    validate_models()

    restart = restart_fcitx_session(
        timeout=min(20.0, max(5.0, timeout_s)),
        required_addon="vocotype",
    )
    if not restart.success:
        details = restart.startup_log.strip()[-6000:]
        raise RuntimeError(
            restart.message + (f"\n{details}" if details else "")
        )
    emit("✅ 当前 Fcitx 实例已实际加载 VoCoType addon")
    wait_for_fcitx_backend(timeout_s)


def validate_ibus(runtime_root: Path) -> None:
    home = Path.home()
    require_file(
        [
            home / ".local/libexec/ibus-engine-vocotype",
            Path("/usr/libexec/vocotype-ibus-engine"),
            Path("/usr/lib/vocotype/vocotype-ibus-engine"),
        ],
        "IBus launcher",
        executable=True,
    )
    require_file(
        [
            home / ".local/share/ibus/component/vocotype.xml",
            Path("/usr/share/ibus/component/vocotype.xml"),
        ],
        "IBus component",
    )
    require_file([runtime_root / "ibus/main.py"], "IBus 引擎代码")
    validate_models()
    emit("✅ VoCoType（IBus）安装结构与模型验收通过")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--framework", choices=("fcitx5", "ibus"), required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=90.0)
    args = parser.parse_args()
    try:
        if args.framework == "fcitx5":
            validate_fcitx(args.runtime_root, max(5.0, args.timeout))
        else:
            validate_ibus(args.runtime_root)
    except Exception as exc:  # noqa: BLE001 - command must report a concise failure.
        emit(f"❌ 安装验收失败: {exc}")
        return 1
    emit("✅ 安装后验收全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
