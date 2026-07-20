"""User-level install/repair orchestration for the graphical settings center."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Callable

ProgressCallback = Callable[[str], None]


def find_project_root(start: str | os.PathLike[str] | None = None) -> Path | None:
    candidates = []
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
            if (path / "fcitx5/scripts/install-fcitx5.sh").is_file() and (path / "pyproject.toml").is_file():
                return path
    return None


def installer_command(project_root: Path) -> list[str]:
    return [
        "bash",
        str(project_root / "fcitx5/scripts/install-fcitx5.sh"),
        "--non-interactive",
        "--preserve-config",
        "--skip-audio",
        "--python-choice",
        "user",
        "--slm-provider",
        "preserve",
    ]


def install_or_repair(
    *,
    project_root: Path | None = None,
    progress: ProgressCallback | None = None,
) -> tuple[bool, str]:
    root = project_root or find_project_root()
    if root is None:
        return False, "当前应用不是从源码目录运行，找不到安装器。请从项目目录启动设置中心后重试。"
    command = installer_command(root)
    callback = progress or (lambda _line: None)
    callback("$ " + " ".join(command))
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
    return_code = process.wait()
    text = "".join(output)
    return return_code == 0, text


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


def launch_ibus_installer(project_root: Path | None = None) -> tuple[bool, str]:
    """Open the IBus installer in a terminal for prompts and possible sudo.

    IBus registration can require a system component under GNOME/Debian, so the
    graphical settings center deliberately uses a visible terminal rather than
    hiding password prompts or failing silently.
    """

    root = project_root or find_project_root()
    if root is None:
        return False, "找不到源码目录，无法启动 IBus 安装器。"
    script = root / "scripts/install-ibus.sh"
    shell_command = (
        f"cd {shlex.quote(str(root))}; "
        f"bash {shlex.quote(str(script))}; status=$?; "
        'echo; read -r -p "安装器已结束，按 Enter 关闭终端..."; exit $status'
    )
    candidates = [
        (["xdg-terminal-exec", "bash", "-lc", shell_command], "xdg-terminal-exec"),
        (["kgx", "--", "bash", "-lc", shell_command], "kgx"),
        (["gnome-terminal", "--", "bash", "-lc", shell_command], "gnome-terminal"),
        (["konsole", "-e", "bash", "-lc", shell_command], "konsole"),
        (["kitty", "bash", "-lc", shell_command], "kitty"),
        (["xterm", "-e", "bash", "-lc", shell_command], "xterm"),
    ]
    for argv, executable in candidates:
        if shutil.which(executable):
            subprocess.Popen(argv, cwd=root, env=os.environ.copy())
            return True, "已在终端中启动 IBus 安装器"
    return False, "未找到可用终端模拟器；请运行 bash scripts/install-ibus.sh"
