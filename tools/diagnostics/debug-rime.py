#!/usr/bin/env python3
"""Probe VoCoType's direct system-librime integration."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ibus.rime_runtime import librime_available, probe_runtime


def main() -> int:
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
    print("=== VoCoType Rime 调试 ===")
    print(f"librime C API: {librime_available()}")
    print(f"共享数据: {shared}")
    print(f"用户数据: {user}")
    print(f"已部署: {(user / 'build/default.yaml').is_file()}")
    if shared is None or not librime_available():
        return 1
    log.mkdir(parents=True, exist_ok=True)
    context = probe_runtime(
        shared_data_dir=shared,
        user_data_dir=user,
        log_dir=log,
        schema="luna_pinyin",
        key="n",
    )
    print(f"preedit: {context.composition.preedit!r}")
    print("候选: " + ", ".join(item.text for item in context.menu.candidates[:10]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
