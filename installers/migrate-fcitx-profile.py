#!/usr/bin/env python3
"""Migrate stale standalone VoCoType entries from the Fcitx profile."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.fcitx_session import migrate_legacy_fcitx_profile  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=Path)
    args = parser.parse_args()
    try:
        result = migrate_legacy_fcitx_profile(args.profile)
    except Exception as exc:  # noqa: BLE001 - installer must report concise failure.
        print(f"❌ 无法迁移旧版 Fcitx profile：{exc}", flush=True)
        return 1

    if not result.changed:
        print("✓ Fcitx profile 无旧版 VoCoType 输入法残留", flush=True)
        return 0

    defaults = ", ".join(
        f"group {group} → {fallback}"
        for group, fallback in result.restored_defaults
    )
    detail = f"；恢复默认输入法：{defaults}" if defaults else ""
    print(
        f"✓ 已从 Fcitx profile 移除 {result.removed_entries} 个旧版 VoCoType 条目"
        f"{detail}",
        flush=True,
    )
    if result.backup:
        print(f"  原 profile 已备份到：{result.backup}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
