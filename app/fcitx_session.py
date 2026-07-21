"""Fcitx desktop-session queries and safe replacement startup."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

FCITX_SERVICE = "org.fcitx.Fcitx5"
@dataclass(frozen=True)
class FcitxRestartResult:
    success: bool
    message: str
    startup_log: str = ""
    owner: str | None = None


def session_environment(base: Mapping[str, str] | None = None) -> dict[str, str]:
    environment = dict(base or os.environ)
    environment.pop("FCITX_ADDON_DIRS", None)
    runtime_dir = environment.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    if Path(runtime_dir).is_dir():
        environment.setdefault("XDG_RUNTIME_DIR", runtime_dir)
    bus_path = Path(runtime_dir) / "bus"
    if bus_path.is_socket():
        environment.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={bus_path}")
    return environment


def _busctl_json_call(arguments: list[str]) -> dict[str, object] | None:
    busctl = shutil.which("busctl")
    environment = session_environment()
    if busctl is None or not environment.get("DBUS_SESSION_BUS_ADDRESS"):
        return None
    try:
        result = subprocess.run(
            [busctl, "--user", "--json=short", "call", *arguments],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
            env=environment,
        )
        if result.returncode != 0:
            return None
        payload = json.loads(result.stdout)
        return payload if isinstance(payload, dict) else None
    except (
        OSError,
        subprocess.SubprocessError,
        json.JSONDecodeError,
        TypeError,
    ):
        return None


def query_fcitx_bus_owner() -> str | None:
    payload = _busctl_json_call(
        [
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "GetNameOwner",
            "s",
            FCITX_SERVICE,
        ]
    )
    if payload is None:
        return None
    try:
        owner = payload.get("data", [None])[0]
    except (AttributeError, IndexError, TypeError):
        return None
    return str(owner) if owner else None


def query_fcitx_addon_names() -> set[str] | None:
    payload = _busctl_json_call(
        [
            FCITX_SERVICE,
            "/controller",
            "org.fcitx.Fcitx.Controller1",
            "GetAddons",
        ]
    )
    if payload is None:
        return None
    try:
        rows = payload.get("data", [[]])[0]
        return {
            str(row[0])
            for row in rows
            if isinstance(row, list) and row
        }
    except (AttributeError, IndexError, TypeError):
        return None


def restart_fcitx_session(
    *,
    timeout: float = 10.0,
    required_addon: str | None = None,
    poll_interval: float = 0.1,
) -> FcitxRestartResult:
    """Replace Fcitx without waiting for the persistent daemon to exit."""

    executable = shutil.which("fcitx5")
    if executable is None:
        return FcitxRestartResult(False, "未检测到 fcitx5")

    previous_owner = query_fcitx_bus_owner()
    try:
        process = subprocess.Popen(
            [executable, "-r", "-d"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=session_environment(),
            start_new_session=True,
            close_fds=True,
        )
    except OSError as exc:
        return FcitxRestartResult(False, f"无法启动 Fcitx 5：{exc}")

    deadline = time.monotonic() + max(0.5, timeout)
    latest_owner: str | None = None
    latest_addons: set[str] | None = None
    while time.monotonic() < deadline:
        latest_owner = query_fcitx_bus_owner()
        latest_addons = query_fcitx_addon_names()
        owner_replaced = latest_owner is not None and (
            previous_owner is None or latest_owner != previous_owner
        )
        addon_ready = required_addon is None or (
            latest_addons is not None and required_addon in latest_addons
        )
        if owner_replaced and addon_ready:
            message = "Fcitx 5 已重新启动"
            if required_addon:
                message += f"，并已加载 {required_addon} addon"
            return FcitxRestartResult(True, message, owner=latest_owner)

        return_code = process.poll()
        if return_code not in (None, 0):
            return FcitxRestartResult(
                False,
                f"Fcitx 5 重启进程异常退出（exit={return_code}）",
                owner=latest_owner,
            )
        time.sleep(max(0.01, poll_interval))

    addon_text = (
        "无法查询"
        if latest_addons is None
        else ", ".join(sorted(latest_addons)) or "空"
    )
    required_text = (
        f"；未加载所需 addon={required_addon}" if required_addon else ""
    )
    return FcitxRestartResult(
        False,
        "等待新 Fcitx 5 实例就绪超时；"
        f"owner={latest_owner or '无'}，addons={addon_text}{required_text}",
        owner=latest_owner,
    )
