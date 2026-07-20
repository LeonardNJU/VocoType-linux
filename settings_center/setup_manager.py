"""GUI installation, repair, and framework restart helpers."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

ProgressCallback = Callable[[str], None]
Framework = Literal["fcitx5", "ibus"]


@dataclass(frozen=True)
class InstallOptions:
    python_choice: str = "user"
    preserve_config: bool = True
    install_system_deps: bool = True
    bootstrap_uv: bool = True
    rime_enabled: bool = False
    rime_schema: str = "luna_pinyin"
    component_mode: str = "auto"


def find_project_root(start: str | os.PathLike[str] | None = None) -> Path | None:
    candidates: list[Path] = []
    if start:
        candidates.append(Path(start).expanduser().resolve())
    configured_source = os.environ.get("VOCOTYPE_PROJECT_DIR", "").strip()
    if configured_source:
        candidates.append(Path(configured_source).expanduser().resolve())
    candidates.extend([Path.cwd().resolve(), Path(__file__).resolve().parents[1]])
    seen: set[Path] = set()
    for candidate in candidates:
        for path in (candidate, *candidate.parents):
            if path in seen:
                continue
            seen.add(path)
            if (
                (path / "fcitx5/scripts/install-fcitx5.sh").is_file()
                and (path / "scripts/install-ibus-gui.sh").is_file()
                and (path / "pyproject.toml").is_file()
            ):
                return path
    return None


def _common_flags(options: InstallOptions) -> list[str]:
    flags = ["--non-interactive", "--skip-audio", "--python-choice", options.python_choice]
    if options.preserve_config:
        flags.append("--preserve-config")
    if options.install_system_deps:
        flags.append("--install-system-deps")
    if options.bootstrap_uv:
        flags.append("--bootstrap-uv")
    return flags


def fcitx_installer_command(project_root: Path, options: InstallOptions | None = None) -> list[str]:
    opts = options or InstallOptions()
    return [
        "bash",
        str(project_root / "fcitx5/scripts/install-fcitx5.sh"),
        *_common_flags(opts),
        "--slm-provider",
        "preserve" if opts.preserve_config else "disabled",
    ]


def ibus_installer_command(project_root: Path, options: InstallOptions | None = None) -> list[str]:
    opts = options or InstallOptions()
    return [
        "bash",
        str(project_root / "scripts/install-ibus-gui.sh"),
        *_common_flags(opts),
        "--slm-provider",
        "preserve" if opts.preserve_config else "disabled",
        "--rime",
        "enabled" if opts.rime_enabled else "disabled",
        "--rime-schema",
        opts.rime_schema or "luna_pinyin",
        "--component-mode",
        opts.component_mode,
    ]


def installer_command(project_root: Path) -> list[str]:
    """Backward-compatible alias for the default Fcitx graphical installer."""

    return fcitx_installer_command(project_root)


def install_or_repair(
    framework: Framework = "fcitx5",
    *,
    options: InstallOptions | None = None,
    project_root: Path | None = None,
    progress: ProgressCallback | None = None,
) -> tuple[bool, str]:
    root = project_root or find_project_root()
    if root is None:
        return False, "找不到包含安装后端的 VoCoType 源码目录。"
    if framework == "fcitx5":
        command = fcitx_installer_command(root, options)
    elif framework == "ibus":
        command = ibus_installer_command(root, options)
    else:
        return False, f"未知安装框架: {framework}"

    callback = progress or (lambda _line: None)
    callback("开始图形安装；需要管理员权限时，桌面将弹出 Polkit 授权窗口。")
    process = subprocess.Popen(
        command,
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=os.environ.copy(),
        bufsize=1,
    )
    output: list[str] = []
    assert process.stdout is not None
    for line in process.stdout:
        clean = line.rstrip()
        output.append(line)
        callback(clean)
    return_code = process.wait()
    return return_code == 0, "".join(output)


def polkit_available() -> bool:
    return shutil.which("pkexec") is not None


def restart_backend() -> tuple[bool, str]:
    if shutil.which("systemctl") is None:
        return False, "systemctl 不可用"
    result = subprocess.run(
        ["systemctl", "--user", "restart", "vocotype-fcitx5-backend.service"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    message = (result.stdout + result.stderr).strip()
    return result.returncode == 0, message or ("后台服务已重启" if result.returncode == 0 else "后台服务重启失败")


def restart_fcitx() -> tuple[bool, str]:
    executable = shutil.which("fcitx5")
    if executable is None:
        return False, "未检测到 fcitx5"
    result = subprocess.run([executable, "-r"], capture_output=True, text=True, timeout=10, check=False)
    message = (result.stdout + result.stderr).strip()
    return result.returncode == 0, message or ("Fcitx 5 已重启" if result.returncode == 0 else "Fcitx 5 重启失败")


def restart_ibus() -> tuple[bool, str]:
    executable = shutil.which("ibus")
    if executable is None:
        return False, "未检测到 ibus"
    result = subprocess.run([executable, "restart"], capture_output=True, text=True, timeout=15, check=False)
    message = (result.stdout + result.stderr).strip()
    return result.returncode == 0, message or ("IBus 已重启" if result.returncode == 0 else "IBus 重启失败")
