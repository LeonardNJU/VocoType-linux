"""GUI installation, repair, and framework restart helpers."""

from __future__ import annotations

import os
import signal
import shutil
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Literal

from app.download_models import inspect_required_models
from app.fcitx_session import query_fcitx_addon_names, restart_fcitx_session
from vocotype_package import read_system_package_marker

ProgressCallback = Callable[[str], None]
Framework = Literal["fcitx5", "ibus"]
INSTALL_PROGRESS_PREFIX = "VOCOTYPE_PROGRESS:"
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
    remove_system_component: bool = True
    remove_system_integration: bool = True


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


def native_package_metadata(project_root: Path | None = None) -> dict[str, str]:
    candidates: list[Path] = []
    if project_root is not None:
        candidates.append(Path(project_root) / ".system-package")
    runtime_root = Path(__file__).resolve().parents[1]
    candidates.extend(
        [
            runtime_root / ".system-package",
            Path("/usr/share/vocotype/.system-package"),
        ]
    )
    for marker in dict.fromkeys(candidates):
        metadata = read_system_package_marker(marker)
        if metadata:
            metadata["marker"] = str(marker)
            return metadata
    return {}


def native_package_flavor(project_root: Path | None = None) -> str | None:
    metadata = native_package_metadata(project_root)
    return metadata.get("flavor") if metadata else None


def native_package_name(project_root: Path | None = None) -> str | None:
    metadata = native_package_metadata(project_root)
    return metadata.get("package") if metadata else None


def package_supports_framework(
    framework: Framework,
    project_root: Path | None = None,
) -> bool:
    flavor = native_package_flavor(project_root)
    if flavor in {None, "universal"}:
        return True
    return flavor == framework


def preferred_installed_framework(
    ibus_status: IntegrationStatus,
    fcitx_status: IntegrationStatus,
    selected: Framework,
) -> Framework | None:
    """Resolve which installed framework should drive framework-specific UI."""

    ibus_installed = ibus_status.state != "absent"
    fcitx_installed = fcitx_status.state != "absent"
    if ibus_installed and fcitx_installed:
        return selected if selected in {"ibus", "fcitx5"} else "fcitx5"
    if ibus_installed:
        return "ibus"
    if fcitx_installed:
        return "fcitx5"
    return None


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


def fcitx_panel_style_support(
    *,
    home: Path | None = None,
    system_prefix: Path = Path("/usr"),
) -> tuple[bool, Path | None]:
    """Return whether an installed Fcitx module understands PanelStyle."""

    paths = installation_paths(home=home, system_prefix=system_prefix).fcitx_modules
    first_module: Path | None = None
    for module in paths:
        if not module.is_file():
            continue
        if first_module is None:
            first_module = module
        try:
            if b"PanelStyle" in module.read_bytes():
                return True, module
        except OSError:
            continue
    return False, first_module


def _models_verified(home: Path) -> bool:
    return all(
        bool(item.get("complete"))
        for item in inspect_required_models(home=home).values()
    )


def parse_install_progress(line: str) -> tuple[float, str] | None:
    """Parse a structured installer stage into a GTK fraction and label."""

    if not line.startswith(INSTALL_PROGRESS_PREFIX):
        return None
    payload = line[len(INSTALL_PROGRESS_PREFIX) :]
    try:
        percent_text, message = payload.split(":", 1)
        percent = int(percent_text)
    except (ValueError, TypeError):
        return None
    message = message.strip()
    if not 0 <= percent <= 100 or not message:
        return None
    return percent / 100.0, message


def _query_fcitx_addon_loaded() -> bool:
    names = query_fcitx_addon_names()
    return names is not None and "vocotype" in names


def integration_status(
    framework: Framework,
    *,
    home: Path | None = None,
    system_prefix: Path = Path("/usr"),
    project_root: Path | None = None,
    fcitx_socket_path: Path = Path("/tmp/vocotype-fcitx5.sock"),
    fcitx_addon_loaded: bool | None = None,
    fcitx_panel_style_supported: bool | None = None,
) -> IntegrationStatus:
    """Classify a framework integration as complete, partial, or absent."""

    user_home = home or Path.home()
    paths = installation_paths(home=user_home, system_prefix=system_prefix)
    if framework == "fcitx5":
        groups = {
            "module": paths.fcitx_modules,
            "addon": paths.fcitx_addons,
            "后台服务": paths.fcitx_services,
            "后端启动器": paths.fcitx_backend_launchers,
            "后端代码": paths.fcitx_runtime_entries,
        }
        structural_groups = groups
        user_launcher = user_home / ".local/bin/vocotype-fcitx5-backend"
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

    if _models_verified(user_home):
        present.append("必需模型")
    else:
        missing.append("必需模型")

    if framework == "fcitx5":
        panel_style_supported = (
            fcitx_panel_style_support(
                home=user_home, system_prefix=system_prefix
            )[0]
            if fcitx_panel_style_supported is None
            else fcitx_panel_style_supported
        )
        if panel_style_supported:
            present.append("F9 状态样式支持")
        else:
            missing.append("F9 状态样式支持（module 需要更新）")
        if fcitx_socket_path.is_socket():
            present.append("后端 IPC")
        else:
            missing.append("后端 IPC")
        loaded = (
            _query_fcitx_addon_loaded()
            if fcitx_addon_loaded is None
            else fcitx_addon_loaded
        )
        if loaded:
            present.append("Fcitx addon 已加载")
        else:
            missing.append("Fcitx addon 未加载")

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
            marker = read_system_package_marker(path / ".system-package")
            if marker:
                flavor = marker["flavor"]
                common = (
                    (path / "pyproject.toml").is_file()
                    and (path / "settings_center/application.py").is_file()
                )
                ibus_ready = (
                    (path / "ibus/scripts/install-gui.sh").is_file()
                    and (path / "ibus/scripts/uninstall-gui.sh").is_file()
                )
                fcitx_ready = (
                    (path / "fcitx5/scripts/install-gui.sh").is_file()
                    and (path / "fcitx5/scripts/uninstall-gui.sh").is_file()
                )
                if common and (
                    (flavor == "universal" and ibus_ready and fcitx_ready)
                    or (flavor == "ibus" and ibus_ready)
                    or (flavor == "fcitx5" and fcitx_ready)
                ):
                    return path
            elif (
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



def _effective_install_options(
    project_root: Path,
    options: InstallOptions | None,
) -> InstallOptions:
    resolved = options or InstallOptions()
    if native_package_present(project_root):
        return replace(resolved, python_choice="user", bootstrap_uv=True)
    return resolved


def fcitx_installer_command(project_root: Path, options: InstallOptions | None = None) -> list[str]:
    opts = _effective_install_options(project_root, options)
    return [
        "bash",
        str(project_root / "fcitx5/scripts/install-gui.sh"),
        *_common_flags(opts),
    ]


def ibus_installer_command(project_root: Path, options: InstallOptions | None = None) -> list[str]:
    opts = _effective_install_options(project_root, options)
    return [
        "bash",
        str(project_root / "ibus/scripts/install-gui.sh"),
        *_common_flags(opts),
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
    command = [
        "bash",
        str(project_root / "fcitx5/scripts/uninstall-gui.sh"),
        *_uninstall_flags(opts),
    ]
    command.append(
        "--remove-system-integration"
        if opts.remove_system_integration
        else "--keep-system-integration"
    )
    return command


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
    command.append(
        "--remove-system-component"
        if opts.remove_system_component
        else "--keep-system-component"
    )
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
    if not package_supports_framework(framework, root):
        flavor = native_package_flavor(root) or "universal"
        return False, f"当前 {flavor} 软件包不包含 {framework} integration。"
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
    if not package_supports_framework(framework, root):
        flavor = native_package_flavor(root) or "universal"
        return False, f"当前 {flavor} 软件包不包含 {framework} integration。"
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
    return bool(native_package_metadata(project_root))


def native_package_removal_command(project_root: Path | None = None) -> str | None:
    package = native_package_name(project_root)
    if not package:
        return None
    if shutil.which("pacman"):
        return f"sudo pacman -Rns {package}"
    if shutil.which("dnf"):
        return f"sudo dnf remove {package}"
    if shutil.which("apt-get"):
        return f"sudo apt remove {package}"
    return f"请使用系统包管理器卸载 {package}"


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


def restart_ibus_backend(*, proc_root: Path = Path("/proc")) -> tuple[bool, str]:
    """Stop the VoCoType IBus engine so IBus relaunches it on next activation."""

    stopped: list[int] = []
    entries = proc_root.iterdir() if proc_root.is_dir() else ()
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(
                "utf-8", errors="replace"
            )
        except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
            continue
        if "ibus/main.py" not in cmdline or "--ibus" not in cmdline:
            continue
        pid = int(entry.name)
        if pid == os.getpid():
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            stopped.append(pid)
        except ProcessLookupError:
            continue
        except PermissionError as exc:
            return False, f"无法停止 VoCoType（IBus）后台 PID {pid}: {exc}"
    if stopped:
        return True, "VoCoType（IBus）后台已停止；下次切换到 VoCoType 时会自动重新启动"
    return True, "VoCoType（IBus）后台当前未运行；下次切换到 VoCoType 时会自动启动"


def restart_fcitx() -> tuple[bool, str]:
    result = restart_fcitx_session(timeout=10.0)
    detail = result.startup_log.strip()[-4000:]
    if result.success:
        return True, result.message
    message = result.message
    if detail:
        message += "\n" + detail
    return False, message


def restart_ibus() -> tuple[bool, str]:
    executable = shutil.which("ibus")
    if executable is None:
        return False, "未检测到 ibus"
    result = subprocess.run([executable, "restart"], capture_output=True, text=True, timeout=15, check=False)
    message = (result.stdout + result.stderr).strip()
    return result.returncode == 0, message or ("IBus 已重启" if result.returncode == 0 else "IBus 重启失败")
