#!/usr/bin/env python3
"""调试 VoCoType Rime 集成"""

import sys
from pathlib import Path

# 添加项目目录
sys.path.insert(0, str(Path(__file__).parent))

def test_rime():
    print("=== VoCoType Rime 调试测试 ===\n")

    # 1. 检查 pyrime 是否可用
    print("[1] 检查 pyrime...")
    try:
        import pyrime
        print(f"    ✓ pyrime 版本: {pyrime.__version__}")
    except ImportError as e:
        print(f"    ✗ pyrime 不可用: {e}")
        return False

    # 2. 检查目录
    print("\n[2] 检查目录...")

    # 优先使用 ibus-rime 配置目录
    vocotype_dir = Path.home() / ".config" / "vocotype" / "rime"
    ibus_rime_dir = Path.home() / ".config" / "ibus" / "rime"
    if (ibus_rime_dir / "default.yaml").exists():
        user_data_dir = ibus_rime_dir
    else:
        user_data_dir = vocotype_dir
    print(f"    VoCoType 配置目录: {user_data_dir}")
    print(f"    存在: {user_data_dir.exists()}")

    # 检查 ibus-rime 用户目录（用于参考）
    print(f"    ibus-rime 目录: {ibus_rime_dir} (存在: {ibus_rime_dir.exists()})")

    shared_dirs = [
        Path("/usr/share/rime-data"),
        Path("/usr/local/share/rime-data"),
    ]
    shared_data_dir = next((d for d in shared_dirs if d.exists()), None)
    print(f"    共享数据目录: {shared_data_dir}")

    log_dir = Path.home() / ".local" / "share" / "vocotype" / "rime"
    print(f"    日志目录: {log_dir}")
    log_dir.mkdir(parents=True, exist_ok=True)
    user_data_dir.mkdir(parents=True, exist_ok=True)

    if shared_data_dir is None:
        print("    ✗ 找不到共享数据目录")
        return False

    # 3. 初始化 Rime Session
    print("\n[3] 初始化 Rime Session...")
    try:
        from pyrime.api import Traits, API
        from pyrime.session import Session

        traits = Traits(
            shared_data_dir=str(shared_data_dir),
            user_data_dir=str(user_data_dir),
            log_dir=str(log_dir),
            distribution_name="VoCoType",
            distribution_code_name="vocotype",
            distribution_version="1.0",
            app_name="rime.vocotype",
        )

        api = API()
        session = Session(traits=traits, api=api)
        schema = session.get_current_schema()
        print(f"    ✓ Session 创建成功")
        print(f"    当前方案: {schema}")
    except Exception as e:
        print(f"    ✗ 初始化失败: {e}")
        import traceback
        traceback.print_exc()
        return False

    # 4. 测试按键处理
    print("\n[4] 测试按键处理...")
    test_keys = [
        ('n', ord('n'), "输入 'n'"),
        ('i', ord('i'), "输入 'i'"),
        ('h', ord('h'), "输入 'h'"),
        ('a', ord('a'), "输入 'a'"),
        ('o', ord('o'), "输入 'o'"),
    ]

    for name, keyval, desc in test_keys:
        try:
            handled = session.process_key(keyval, 0)
            commit = session.get_commit()
            context = session.get_context()

            preedit = ""
            if context and context.composition:
                preedit = context.composition.preedit or ""

            commit_text = ""
            if commit and commit.text:
                commit_text = commit.text

            print(f"    {desc}: handled={handled}, preedit='{preedit}', commit='{commit_text}'")
        except Exception as e:
            print(f"    {desc}: 失败 - {e}")

    # 5. 测试空格选词
    print("\n[5] 测试空格选词...")
    try:
        # 按空格
        handled = session.process_key(0x20, 0)  # space
        commit = session.get_commit()
        context = session.get_context()

        commit_text = commit.text if commit else ""
        print(f"    空格: handled={handled}, commit='{commit_text}'")

    except Exception as e:
        print(f"    空格: 失败 - {e}")

    print("\n=== 测试完成 ===")
    return True

if __name__ == "__main__":
    test_rime()
