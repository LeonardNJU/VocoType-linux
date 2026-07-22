#!/usr/bin/env python3
"""Validate the current user's installed VoCoType IBus integration."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ibus.rime_runtime import librime_available, probe_runtime


def check_mark(ok: bool) -> str:
    return "✓" if ok else "✗"


def test_directory_structure() -> None:
    print("\n[1] 检查目录结构...")
    home = Path.home()
    install_dir = home / ".local/share/vocotype"
    checks = [
        ("安装目录", install_dir, install_dir.is_dir()),
        ("app/", install_dir / "app", (install_dir / "app").is_dir()),
        ("ibus/", install_dir / "ibus", (install_dir / "ibus").is_dir()),
        (
            "Rime ctypes 适配层",
            install_dir / "ibus/rime_runtime.py",
            (install_dir / "ibus/rime_runtime.py").is_file(),
        ),
    ]
    launcher = home / ".local/libexec/ibus-engine-vocotype"
    checks.append(("启动脚本", launcher, launcher.is_file() and os.access(launcher, os.X_OK)))
    for label, path, ok in checks:
        print(f"  {check_mark(ok)} {label}: {path}")
    assert all(ok for _, _, ok in checks), "目录结构检查失败"


def test_python_deps() -> None:
    print("\n[2] 检查 Python 依赖...")
    results = []
    for module, name in (
        ("numpy", "NumPy"),
        ("sounddevice", "SoundDevice"),
        ("soundfile", "SoundFile"),
        ("gi", "PyGObject"),
    ):
        try:
            __import__(module)
            ok = True
        except ImportError:
            ok = False
        results.append(ok)
        print(f"  {check_mark(ok)} {name}")
    assert all(results), "Python 依赖检查失败"


def _rime_paths() -> tuple[Path, Path, Path]:
    home = Path.home()
    user = home / ".config/vocotype/rime"
    log = home / ".local/share/vocotype/rime"
    shared = next(
        (
            path
            for path in (Path("/usr/share/rime-data"), Path("/usr/local/share/rime-data"))
            if (path / "default.yaml").is_file()
        ),
        None,
    )
    assert shared is not None, "缺少 Rime 共享数据目录"
    return shared, user, log


def test_rime_integration() -> None:
    print("\n[3] 检查 Rime 集成...")
    shared, user, log = _rime_paths()
    checks = [
        ("librime C API", librime_available()),
        ("共享数据", (shared / "default.yaml").is_file()),
        ("用户部署", (user / "build/default.yaml").is_file()),
        ("schema 配置", (user / "user.yaml").is_file()),
    ]
    log.mkdir(parents=True, exist_ok=True)
    for label, ok in checks:
        print(f"  {check_mark(ok)} {label}")
    assert all(ok for _, ok in checks), "Rime 集成检查失败"


def test_rime_functionality() -> None:
    print("\n[4] 测试 Rime 普通键盘输入...")
    shared, user, log = _rime_paths()
    context = probe_runtime(
        shared_data_dir=shared,
        user_data_dir=user,
        log_dir=log,
        schema="luna_pinyin",
        key="n",
    )
    print(f"  ✓ preedit: {context.composition.preedit!r}")
    print("  ✓ 候选: " + ", ".join(item.text for item in context.menu.candidates[:5]))
    assert context.composition.preedit == "n"
    assert context.menu.candidates


def test_ibus_component() -> None:
    print("\n[5] 检查 IBus 组件...")
    home = Path.home()
    paths = (
        home / ".local/share/ibus/component/vocotype.xml",
        Path("/usr/share/ibus/component/vocotype.xml"),
    )
    found = next((path for path in paths if path.is_file()), None)
    print(f"  {check_mark(found is not None)} 组件文件: {found or paths}")
    assert found is not None, "IBus 组件检查失败"


def main() -> int:
    print("=" * 50)
    print("VoCoType IBus 安装验证")
    print("=" * 50)
    tests = [
        ("目录结构", test_directory_structure),
        ("Python 依赖", test_python_deps),
        ("Rime 集成", test_rime_integration),
        ("Rime 功能", test_rime_functionality),
        ("IBus 组件", test_ibus_component),
    ]
    results: list[tuple[str, bool]] = []
    for name, test in tests:
        try:
            test()
            ok = True
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ {type(exc).__name__}: {exc}")
            ok = False
        results.append((name, ok))
    print("\n" + "=" * 50)
    for name, ok in results:
        print(f"  {check_mark(ok)} {name}")
    return 0 if all(ok for _, ok in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
