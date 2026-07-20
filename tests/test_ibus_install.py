#!/usr/bin/env python3
"""VoCoType IBus 安装验证测试

验证安装是否正确完成，特别是 Rime 集成部分。
"""

import os
import sys
from pathlib import Path


def _skip_unless_install_validation_enabled():
    """这些用例用于安装后验证；默认在 pytest 中跳过。"""
    if "PYTEST_CURRENT_TEST" not in os.environ:
        return
    if os.environ.get("VOCOTYPE_VALIDATE_INSTALL") == "1":
        return
    try:
        import pytest
        pytest.skip("安装验证测试默认跳过，设置 VOCOTYPE_VALIDATE_INSTALL=1 启用")
    except ImportError:
        pass


def check_mark(ok: bool) -> str:
    return "✓" if ok else "✗"


def test_directory_structure():
    """测试目录结构"""
    _skip_unless_install_validation_enabled()
    print("\n[1] 检查目录结构...")

    home = Path.home()
    install_dir = home / ".local" / "share" / "vocotype"

    results = []

    # 检查安装目录
    ok = install_dir.exists()
    results.append(ok)
    print(f"  {check_mark(ok)} 安装目录: {install_dir}")

    # 检查子目录
    for subdir in ["app", "ibus"]:
        path = install_dir / subdir
        ok = path.exists()
        results.append(ok)
        print(f"  {check_mark(ok)} {subdir}/")

    # 检查启动脚本
    launcher = home / ".local" / "libexec" / "ibus-engine-vocotype"
    ok = launcher.exists() and os.access(launcher, os.X_OK)
    results.append(ok)
    print(f"  {check_mark(ok)} 启动脚本: {launcher}")

    assert all(results), "目录结构检查失败"


def test_python_deps():
    """测试 Python 依赖"""
    _skip_unless_install_validation_enabled()
    print("\n[2] 检查 Python 依赖...")

    results = []
    deps = [
        ("numpy", "NumPy"),
        ("sounddevice", "SoundDevice"),
        ("soundfile", "SoundFile"),
    ]

    for module, name in deps:
        try:
            __import__(module)
            ok = True
        except ImportError:
            ok = False
        results.append(ok)
        print(f"  {check_mark(ok)} {name}")

    assert all(results), "Python 依赖检查失败"


def test_rime_integration():
    """测试 Rime 集成"""
    _skip_unless_install_validation_enabled()
    print("\n[3] 检查 Rime 集成...")

    home = Path.home()
    vocotype_rime = home / ".config" / "vocotype" / "rime"
    ibus_rime = home / ".config" / "ibus" / "rime"
    shared_dirs = [Path("/usr/share/rime-data"), Path("/usr/local/share/rime-data")]
    shared_data_dir = next((d for d in shared_dirs if d.exists()), None)

    results = []

    # 检查 pyrime 是否可用
    try:
        import pyrime
        pyrime_ok = True
        pyrime_version = getattr(pyrime, "__version__", "unknown")
    except ImportError:
        pyrime_ok = False
        pyrime_version = "未安装"

    results.append(pyrime_ok)
    print(f"  {check_mark(pyrime_ok)} pyrime: {pyrime_version}")

    if not pyrime_ok:
        print("  (跳过 Rime 相关测试)")
        return  # 纯语音版，不算失败

    # 检查共享数据目录
    ok = shared_data_dir is not None
    results.append(ok)
    print(f"  {check_mark(ok)} rime-data 目录: {shared_data_dir}")

    if not ok:
        print("  ⚠ 请先安装 rime-data 共享数据")
        assert False, "缺少 rime-data 共享数据目录"

    # 优先使用 ibus-rime 配置目录
    if (ibus_rime / "default.yaml").exists():
        results.append(True)
        print(f"  {check_mark(True)} ibus-rime 配置: {ibus_rime}")
    else:
        # 回退到 vocotype 配置目录
        ok = vocotype_rime.exists()
        results.append(ok)
        print(f"  {check_mark(ok)} vocotype rime 目录: {vocotype_rime}")

        user_yaml = vocotype_rime / "user.yaml"
        ok = user_yaml.exists()
        results.append(ok)
        print(f"  {check_mark(ok)} user.yaml: {user_yaml}")

    assert all(results), "Rime 集成检查失败"


def test_rime_functionality():
    """测试 Rime 功能"""
    _skip_unless_install_validation_enabled()
    print("\n[4] 测试 Rime 功能...")

    try:
        from pyrime.api import Traits, API
        from pyrime.session import Session
    except ImportError:
        print("  (pyrime 未安装，跳过)")
        return

    home = Path.home()
    ibus_rime = home / ".config" / "ibus" / "rime"
    if (ibus_rime / "default.yaml").exists():
        user_data_dir = ibus_rime
    else:
        user_data_dir = home / ".config" / "vocotype" / "rime"
    log_dir = home / ".local" / "share" / "vocotype" / "rime"

    if not user_data_dir.exists():
        print("  ⚠ VoCoType Rime 配置不存在")
        assert False, "VoCoType Rime 配置不存在"

    if not log_dir.exists():
        log_dir.mkdir(parents=True, exist_ok=True)

    # 查找共享数据目录
    shared_dirs = [Path("/usr/share/rime-data"), Path("/usr/local/share/rime-data")]
    shared_data_dir = next((d for d in shared_dirs if d.exists()), None)

    if shared_data_dir is None:
        print("  ✗ 找不到 rime-data 目录")
        assert False, "找不到 rime-data 目录"

    results = []

    try:
        traits = Traits(
            shared_data_dir=str(shared_data_dir),
            user_data_dir=str(user_data_dir),
            log_dir=str(log_dir),
            distribution_name="VoCoType-Test",
            distribution_code_name="vocotype-test",
            distribution_version="1.0",
            app_name="rime.vocotype.test",
        )
        print("  ✓ Traits 创建成功")
        results.append(True)
    except Exception as e:
        print(f"  ✗ Traits 创建失败: {e}")
        assert False, f"Traits 创建失败: {e}"

    try:
        api = API()
        session = Session(traits=traits, api=api, id=api.create_session())
        print("  ✓ Session 创建成功")
        results.append(True)
    except Exception as e:
        print(f"  ✗ Session 创建失败: {e}")
        assert False, f"Session 创建失败: {e}"

    # 检查 schema
    schema = session.get_current_schema()
    schemas = session.get_schema_list()
    schema_ids = [s.schema_id for s in schemas]

    ok = len(schemas) > 0
    results.append(ok)
    print(f"  {check_mark(ok)} Schema 列表: {len(schemas)} 个方案")
    if schemas:
        print(f"      当前: {schema}")
        print(f"      可用: {', '.join(schema_ids[:5])}{'...' if len(schema_ids) > 5 else ''}")

    if not ok:
        print("  ⚠ Schema 列表为空，可能是符号链接配置问题")
        assert False, "Schema 列表为空"

    # 测试输入
    print("\n  测试输入 'ni'...")
    for c in "ni":
        session.process_key(ord(c), 0)

    ctx = session.get_context()
    if ctx:
        preedit = ctx.composition.preedit if ctx.composition else ""
        num_candidates = ctx.menu.num_candidates

        ok = preedit == "ni"
        results.append(ok)
        print(f"  {check_mark(ok)} preedit: '{preedit}'")

        ok = num_candidates > 0
        results.append(ok)
        print(f"  {check_mark(ok)} 候选词数量: {num_candidates}")

        if ctx.menu.candidates:
            print(f"      候选: {', '.join(c.text for c in ctx.menu.candidates[:5])}")
    else:
        print("  ✗ 无法获取上下文")
        results.append(False)

    assert all(results), "Rime 功能检查失败"


def test_ibus_component():
    """测试 IBus 组件"""
    _skip_unless_install_validation_enabled()
    print("\n[5] 检查 IBus 组件...")

    home = Path.home()
    results = []

    # 检查组件文件位置
    component_paths = [
        home / ".local" / "share" / "ibus" / "component" / "vocotype.xml",
        Path("/usr/share/ibus/component/vocotype.xml"),
    ]

    found = False
    for path in component_paths:
        if path.exists():
            print(f"  ✓ 组件文件: {path}")
            found = True
            break

    if not found:
        print("  ✗ 组件文件未找到")
        print(f"    检查位置: {component_paths}")

    results.append(found)
    assert all(results), "IBus 组件检查失败"


def main():
    print("=" * 50)
    print("VoCoType IBus 安装验证测试")
    print("=" * 50)

    tests = [
        ("目录结构", test_directory_structure),
        ("Python 依赖", test_python_deps),
        ("Rime 集成", test_rime_integration),
        ("Rime 功能", test_rime_functionality),
        ("IBus 组件", test_ibus_component),
    ]

    results = []
    for name, test_func in tests:
        try:
            test_func()
            ok = True
        except AssertionError as e:
            print(f"\n  ✗ 断言失败: {e}")
            ok = False
        except Exception as e:
            print(f"\n  ✗ 测试异常: {e}")
            ok = False
        results.append((name, ok))

    print("\n" + "=" * 50)
    print("测试结果汇总")
    print("=" * 50)

    all_passed = True
    for name, ok in results:
        print(f"  {check_mark(ok)} {name}")
        if not ok:
            all_passed = False

    print()
    if all_passed:
        print("✓ 所有测试通过！")
        return 0
    else:
        print("✗ 部分测试失败，请检查上述问题")
        return 1


if __name__ == "__main__":
    sys.exit(main())
