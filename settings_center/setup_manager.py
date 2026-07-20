"""GUI installation, repair, and framework restart helpers."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

ProgressCallback = Callable[[str], None]
Framework = Literal["fcitx5", "ibus"]
InstallState = Literal["complete", "partial", "absent"]


@dataclass(frozen=True)
class InstallOptions:
    python_choice: str = "user"
    preserve_config: bool = True
    install_system_deps: bool = True
    bootstrap_uv: bool = True
    rime_enabled: bool = False
    rime_schema: str = "luna_pinyin"
    component_mode: str = "auto"


@dataclass(frozen=True)
class UninstallOptions:
    purge_runtime: bool = False
    remove_user_data: bool = False
    remove_system_component: bool = False


@dataclass(frozen=True)
class InstallationPaths:
    fcitx_modules: tuple[Path, ...]
    fcitx_addons: tuple[Path, ...]
    fcitx_services: tuple[Path, ...]
    fcitx_backend_launchers: tuple[Path, ...]
    fcitx_runtime_entries: tuple[Path, ...]
    ibus_launchers: tuple[Path, ...]
    ibus_components: tuple[Path, ...]
    ibus_runtime_entries: tuple[Path, ...]
    python_runtimes: tuple[Path, ...]


@dataclass(frozen=True)
class IntegrationStatus:
    state: InstallState
    present: tuple[str, ...]
    missing: tuple[str, ...]


def _unique_paths(paths: list[Path]) -> tuple[Path, ...]:
    return tuple(dict.fromkeys(paths))


def installation_paths(
    *,
    home: Path | None = None,
    system_prefix: Path = Path("/usr"),
) -> InstallationPaths:
    """Return user and native-package integration paths in priority order."""

    user_home = home or Path.home()
    system_lib = system_prefix / "lib"
    system_modules = [
        system_lib / "fcitx5/vocotype.so",
        system_prefix / "lib64/fcitx5/vocotype.so",
        *sorted(system_lib.glob("*/fcitx5/vocotype.so")),
    ]
    return InstallationPaths(
        fcitx_modules=_unique_paths(
            [
                user_home / ".local/lib/fcitx5/vocotype.so",
                user_home / ".local/lib64/fcitx5/vocotype.so",
                *system_modules,
            ]
        ),
        fcitx_addons=_unique_paths(
            [
                user_home / ".local/share/fcitx5/addon/vocotype.conf",
                system_prefix / "share/fcitx5/addon/vocotype.conf",
            ]
        ),
        fcitx_services=_unique_paths(
            [
                user_home / ".config/systemd/user/vocotype-fcitx5-backend.service",
                system_lib / "systemd/user/vocotype-fcitx5-backend.service",
            ]
        ),
        fcitx_backend_launchers=_unique_paths(
            [
                user_home / ".local/bin/vocotype-fcitx5-backend",
                system_prefix / "bin/vocotype-fcitx5-backend",
            ]
        ),
        fcitx_runtime_entries=_unique_paths(
            [
                user_home / ".local/share/vocotype-fcitx5/backend/fcitx5_server.py",
                system_prefix / "share/vocotype/fcitx5/backend/fcitx5_server.py",
            ]
        ),
        ibus_launchers=_unique_paths(
            [
                user_home / ".local/libexec/ibus-engine-vocotype",
                system_prefix / "libexec/vocotype-ibus-engine",
                system_prefix / "lib/vocotype/vocotype-ibus-engine",
            ]
        ),
        ibus_components=_unique_paths(
            [
                user_home / ".local/share/ibus/component/vocotype.xml",
                system_prefix / "share/ibus/component/vocotype.xml",
            ]
        ),
        ibus_runtime_entries=_unique_paths(
            [
                user_home / ".local/share/vocotype/ibus/main.py",
                system_prefix / "share/vocotype/ibus/main.py",
            ]
        ),
        python_runtimes=_unique_paths(
            [
                user_home / ".local/share/vocotype-fcitx5/.venv/bin/python",
                user_home / ".local/share/vocotype/.venv/bin/python",
            ]
        ),
    )


def _group_present(paths: tuple[Path, ...]) -> bool:
    return any(path.is_file() for path in paths)


def integration_status(
    framework: Framework,
    *,
    home: Path | None = None,
    system_prefix: Path = Path("/usr"),
    project_root: Path | None = None,
) -> IntegrationStatus:
    """Classify a framework integration as complete, partial, or absent."""

    paths = installation_paths(home=home, system_prefix=system_prefix)
    if framework == "fcitx5":
        groups = {
            "module": paths.fcitx_modules,
            "addon": paths.fcitx_addons,
            "后台服务": paths.fcitx_services,
            "后端启动器": paths.fcitx_backend_launchers,
            "后端代码": paths.fcitx_runtime_entries,
        }
        structural_groups = groups
        user_launcher = (home or Path.home()) / ".local/bin/vocotype-fcitx5-backend"
        python_candidates = list(paths.python_runtimes)
        if user_launcher.is_file() and project_root is not None:
            python_candidates.append(project_root / ".venv/bin/python")
    elif framework == "ibus":
        groups = {
            "launcher": paths.ibus_launchers,
            "component": paths.ibus_components,
            "引擎代码": paths.ibus_runtime_entries,
        }
        structural_groups = groups
        python_candidates = list(paths.python_runtimes)
    else:
        raise ValueError(f"unknown framework: {framework}")

    present = [name for name, candidates in groups.items() if _group_present(candidates)]
    missing = [name for name, candidates in groups.items() if not _group_present(candidates)]
    python_ready = any(path.is_file() and os.access(path, os.X_OK) for path in python_candidates)
    if python_ready:
        present.append("Python 运行环境")
    else:
        missing.append("Python 运行环境")

    any_structural_artifact = any(_group_present(candidates) for candidates in structural_groups.values())
    if not any_structural_artifact:
        state: InstallState = "absent"
    elif missing:
        state = "partial"
    else:
        state = "complete"
    return IntegrationStatus(state, tuple(present), tuple(missing))


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
                (path / "fcitx5/scripts/install-gui.sh").is_file()
                and (path / "fcitx5/scripts/uninstall-gui.sh").is_file()
                and (path / "ibus/scripts/install-gui.sh").is_file()
                and (path / "ibus/scripts/uninstall-gui.sh").is_file()
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
        str(project_root / "fcitx5/scripts/install-gui.sh"),
        *_common_flags(opts),
        "--slm-provider",
        "preserve" if opts.preserve_config else "disabled",
    ]


def ibus_installer_command(project_root: Path, options: InstallOptions | None = None) -> list[str]:
    opts = options or InstallOptions()
    return [
        "bash",
        str(project_root / "ibus/scripts/install-gui.sh"),
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


def _uninstall_flags(options: UninstallOptions) -> list[str]:
    flags: list[str] = []
    if options.purge_runtime:
        flags.append("--purge-runtime")
    if options.remove_user_data:
        flags.append("--remove-user-data")
    return flags


def fcitx_uninstaller_command(
    project_root: Path,
    options: UninstallOptions | None = None,
) -> list[str]:
    opts = options or UninstallOptions()
    return [
        "bash",
        str(project_root / "fcitx5/scripts/uninstall-gui.sh"),
        *_uninstall_flags(opts),
    ]


def ibus_uninstaller_command(
    project_root: Path,
    options: UninstallOptions | None = None,
) -> list[str]:
    opts = options or UninstallOptions()
    command = [
        "bash",
        str(project_root / "ibus/scripts/uninstall-gui.sh"),
        *_uninstall_flags(opts),
    ]
    if opts.remove_system_component:
        command.append("--remove-system-component")
    return command


def installer_command(project_root: Path) -> list[str]:
    """Backward-compatible alias for the default Fcitx graphical installer."""

    return fcitx_installer_command(project_root)


def _run_lifecycle_command(
    command: list[str],
    *,
    root: Path,
    progress: ProgressCallback | None,
    start_message: str,
) -> tuple[bool, str]:
    callback = progress or (lambda _line: None)
    callback(start_message)
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
        output.append(line)
        callback(line.rstrip())
    return process.wait() == 0, "".join(output)


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

    return _run_lifecycle_command(
        command,
        root=root,
        progress=progress,
        start_message="开始图形安装；需要管理员权限时，桌面将弹出 Polkit 授权窗口。",
    )


def uninstall_framework(
    framework: Framework,
    *,
    options: UninstallOptions | None = None,
    project_root: Path | None = None,
    progress: ProgressCallback | None = None,
) -> tuple[bool, str]:
    root = project_root or find_project_root()
    if root is None:
        return False, "找不到包含卸载后端的 VoCoType 源码目录。"
    if framework == "fcitx5":
        command = fcitx_uninstaller_command(root, options)
    elif framework == "ibus":
        command = ibus_uninstaller_command(root, options)
    else:
        return False, f"未知卸载框架: {framework}"
    return _run_lifecycle_command(
        command,
        root=root,
        progress=progress,
        start_message="开始卸载 VoCoType 用户级集成；原生软件包文件仍由系统包管理器管理。",
    )


def native_package_present(project_root: Path | None = None) -> bool:
    root = project_root or find_project_root()
    markers = [Path("/usr/share/vocotype/.system-package")]
    if root is not None:
        markers.insert(0, root / ".system-package")
    return any(marker.is_file() for marker in markers)


def native_package_removal_command(project_root: Path | None = None) -> str | None:
    if not native_package_present(project_root):
        return None
    if shutil.which("pacman"):
        return "sudo pacman -Rns vocotype-linux"
    if shutil.which("dnf"):
        return "sudo dnf remove vocotype-linux"
    if shutil.which("apt-get"):
        return "sudo apt remove vocotype-linux"
    return "请使用系统包管理器卸载 vocotype-linux"


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

    # `fcitx5 -r` stays in the foreground. Running it with subprocess.run and
    # a timeout kills the replacement instance, which leaves the desktop with
    # no input method. Daemonize the replacement before waiting for it.
    environment = os.environ.copy()
    environment.pop("FCITX_ADDON_DIRS", None)
    result = subprocess.run(
        [executable, "-r", "-d"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
        env=environment,
    )
    message = (result.stdout + result.stderr).strip()
    if result.returncode != 0:
        return False, message or "Fcitx 5 重启失败"

    remote = shutil.which("fcitx5-remote")
    if remote is not None:
        time.sleep(0.35)
        probe = subprocess.run(
            [remote],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            env=environment,
        )
        if probe.returncode not in {0, 1, 2}:
            probe_message = (probe.stdout + probe.stderr).strip()
            return False, probe_message or "Fcitx 5 已启动，但无法连接到其 D-Bus 服务"

    return True, message or "Fcitx 5 已重新启动"


def restart_ibus() -> tuple[bool, str]:
    executable = shutil.which("ibus")
    if executable is None:
        return False, "未检测到 ibus"
    result = subprocess.run([executable, "restart"], capture_output=True, text=True, timeout=15, check=False)
    message = (result.stdout + result.stderr).strip()
    return result.returncode == 0, message or ("IBus 已重启" if result.returncode == 0 else "IBus 重启失败")
