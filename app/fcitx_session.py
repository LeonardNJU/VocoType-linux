"""Fcitx desktop-session queries, migration, and safe replacement startup."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

FCITX_SERVICE = "org.fcitx.Fcitx5"
FCITX_CONTROLLER_PATH = "/controller"
FCITX_CONTROLLER_INTERFACE = "org.fcitx.Fcitx.Controller1"
LEGACY_INPUT_METHOD = "vocotype"


@dataclass(frozen=True)
class FcitxRestartResult:
    success: bool
    message: str
    startup_log: str = ""
    owner: str | None = None


@dataclass(frozen=True)
class FcitxProfileMigrationResult:
    changed: bool
    profile: Path
    backup: Path | None = None
    removed_entries: int = 0
    restored_defaults: tuple[tuple[str, str], ...] = ()


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


def parse_fcitx_addon_states(payload: object) -> dict[str, bool] | None:
    """Parse GetAddons rows as ``uniqueName -> enabled``.

    GetAddons lists every discoverable addon. Merely seeing ``vocotype`` in the
    response does not mean it is enabled; the sixth field carries that state.
    """

    if not isinstance(payload, dict):
        return None
    try:
        rows = payload.get("data", [[]])[0]
    except (AttributeError, IndexError, TypeError):
        return None
    if not isinstance(rows, list):
        return None

    states: dict[str, bool] = {}
    for row in rows:
        if not isinstance(row, list) or len(row) < 6 or not row[0]:
            continue
        enabled = row[5]
        if not isinstance(enabled, bool):
            continue
        states[str(row[0])] = enabled
    return states


def query_fcitx_addon_states() -> dict[str, bool] | None:
    payload = _busctl_json_call(
        [
            FCITX_SERVICE,
            FCITX_CONTROLLER_PATH,
            FCITX_CONTROLLER_INTERFACE,
            "GetAddons",
        ]
    )
    return parse_fcitx_addon_states(payload)


def query_fcitx_addon_names() -> set[str] | None:
    """Return enabled addon names from the current Fcitx instance."""

    states = query_fcitx_addon_states()
    if states is None:
        return None
    return {name for name, enabled in states.items() if enabled}


def set_fcitx_addon_enabled(unique_name: str, enabled: bool = True) -> bool:
    """Persist one addon state through the Fcitx controller D-Bus API."""

    busctl = shutil.which("busctl")
    environment = session_environment()
    if busctl is None or not environment.get("DBUS_SESSION_BUS_ADDRESS"):
        return False
    try:
        result = subprocess.run(
            [
                busctl,
                "--user",
                "call",
                FCITX_SERVICE,
                FCITX_CONTROLLER_PATH,
                FCITX_CONTROLLER_INTERFACE,
                "SetAddonsState",
                "a(sb)",
                "1",
                unique_name,
                "true" if enabled else "false",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


_SECTION_RE = re.compile(r"^\s*\[([^\]]+)]\s*$")
_ITEM_SECTION_RE = re.compile(r"^Groups/([^/]+)/Items/\d+$")
_GROUP_SECTION_RE = re.compile(r"^Groups/([^/]+)$")


def _profile_blocks(text: str) -> list[tuple[str | None, list[str]]]:
    blocks: list[tuple[str | None, list[str]]] = []
    section: str | None = None
    lines: list[str] = []
    for line in text.splitlines(keepends=True):
        match = _SECTION_RE.match(line.rstrip("\r\n"))
        if match:
            if lines:
                blocks.append((section, lines))
            section = match.group(1)
            lines = [line]
        else:
            lines.append(line)
    if lines:
        blocks.append((section, lines))
    return blocks


def _profile_value(lines: list[str], key: str) -> str | None:
    for line in lines[1:]:
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", ";")) or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() == key:
            return value.strip()
    return None


def _replace_profile_value(lines: list[str], key: str, value: str) -> list[str]:
    result = list(lines)
    pattern = re.compile(rf"^(\s*{re.escape(key)}\s*=\s*).*(\r?\n)?$")
    for index, line in enumerate(result[1:], start=1):
        match = pattern.match(line)
        if match:
            newline = match.group(2) or ""
            result[index] = f"{match.group(1)}{value}{newline}"
            break
    return result


def legacy_fcitx_profile_references(profile: Path | None = None) -> tuple[str, ...]:
    path = profile or Path.home() / ".config/fcitx5/profile"
    try:
        blocks = _profile_blocks(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError):
        return ()

    references: list[str] = []
    for section, lines in blocks:
        if section is None:
            continue
        item_match = _ITEM_SECTION_RE.match(section)
        if item_match and _profile_value(lines, "Name") == LEGACY_INPUT_METHOD:
            references.append(section)
            continue
        group_match = _GROUP_SECTION_RE.match(section)
        if group_match and _profile_value(lines, "DefaultIM") == LEGACY_INPUT_METHOD:
            references.append(f"{section}:DefaultIM")
    return tuple(references)


def migrate_legacy_fcitx_profile(
    profile: Path | None = None,
) -> FcitxProfileMigrationResult:
    """Remove the obsolete standalone VoCoType IM while preserving user IMs.

    VoCoType is now a global Fcitx module. Older installations may still have
    ``vocotype`` selected in ``~/.config/fcitx5/profile``. Removing only its
    metadata leaves Fcitx pointing at a non-existent input method, which can
    replace the user's Rime UI with a fallback state. This migration removes
    those stale items and restores each affected group to Rime when available.
    """

    path = profile or Path.home() / ".config/fcitx5/profile"
    try:
        original = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return FcitxProfileMigrationResult(False, path)
    except (OSError, UnicodeError):
        raise

    blocks = _profile_blocks(original)
    removed_entries = 0
    retained: list[tuple[str | None, list[str]]] = []
    group_items: dict[str, list[str]] = {}

    for section, lines in blocks:
        item_match = _ITEM_SECTION_RE.match(section or "")
        if item_match:
            group = item_match.group(1)
            name = _profile_value(lines, "Name")
            if name == LEGACY_INPUT_METHOD:
                removed_entries += 1
                continue
            if name:
                group_items.setdefault(group, []).append(name)
        retained.append((section, lines))

    restored_defaults: list[tuple[str, str]] = []
    groups_needing_item: set[str] = set()
    rewritten: list[tuple[str | None, list[str]]] = []
    item_indexes: dict[str, int] = {}

    for section, lines in retained:
        group_match = _GROUP_SECTION_RE.match(section or "")
        if group_match and _profile_value(lines, "DefaultIM") == LEGACY_INPUT_METHOD:
            group = group_match.group(1)
            items = group_items.get(group, [])
            fallback = (
                "rime"
                if "rime" in items
                else next(
                    (name for name in items if not name.startswith("keyboard-")),
                    items[0] if items else "keyboard-us",
                )
            )
            lines = _replace_profile_value(lines, "DefaultIM", fallback)
            restored_defaults.append((group, fallback))
            if not items:
                groups_needing_item.add(group)

        item_match = _ITEM_SECTION_RE.match(section or "")
        if item_match:
            group = item_match.group(1)
            index = item_indexes.get(group, 0)
            item_indexes[group] = index + 1
            newline = "\n"
            if lines and lines[0].endswith("\r\n"):
                newline = "\r\n"
            lines = list(lines)
            lines[0] = f"[Groups/{group}/Items/{index}]{newline}"
            section = f"Groups/{group}/Items/{index}"

        rewritten.append((section, lines))
        if group_match and group_match.group(1) in groups_needing_item:
            group = group_match.group(1)
            newline = "\r\n" if lines and lines[0].endswith("\r\n") else "\n"
            rewritten.append(
                (
                    f"Groups/{group}/Items/0",
                    [
                        newline,
                        f"[Groups/{group}/Items/0]{newline}",
                        f"# Name{newline}",
                        f"Name=keyboard-us{newline}",
                        f"# Layout{newline}",
                        f"Layout={newline}",
                    ],
                )
            )
            group_items[group] = ["keyboard-us"]
            item_indexes[group] = 1
            groups_needing_item.remove(group)

    changed = removed_entries > 0 or bool(restored_defaults)
    if not changed:
        return FcitxProfileMigrationResult(False, path)

    migrated = "".join("".join(lines) for _section, lines in rewritten)
    backup = path.with_name(f"{path.name}.vocotype-backup")
    if not backup.exists():
        shutil.copy2(path, backup)

    temporary = path.with_name(f".{path.name}.vocotype-tmp-{os.getpid()}")
    temporary.write_text(migrated, encoding="utf-8")
    temporary.chmod(path.stat().st_mode)
    os.replace(temporary, path)
    return FcitxProfileMigrationResult(
        True,
        path,
        backup,
        removed_entries,
        tuple(restored_defaults),
    )


def _spawn_fcitx(executable: str) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [executable, "-r", "-d"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=session_environment(),
        start_new_session=True,
        close_fds=True,
    )


def restart_fcitx_session(
    *,
    timeout: float = 10.0,
    required_addon: str | None = None,
    poll_interval: float = 0.1,
) -> FcitxRestartResult:
    """Replace Fcitx, enabling the required addon when it was disabled."""

    executable = shutil.which("fcitx5")
    if executable is None:
        return FcitxRestartResult(False, "未检测到 fcitx5")

    previous_owner = query_fcitx_bus_owner()
    try:
        process = _spawn_fcitx(executable)
    except OSError as exc:
        return FcitxRestartResult(False, f"无法启动 Fcitx 5：{exc}")

    deadline = time.monotonic() + max(0.5, timeout)
    latest_owner: str | None = None
    latest_states: dict[str, bool] | None = None
    enable_attempted = False
    while time.monotonic() < deadline:
        latest_owner = query_fcitx_bus_owner()
        latest_states = query_fcitx_addon_states()
        owner_replaced = latest_owner is not None and (
            previous_owner is None or latest_owner != previous_owner
        )
        addon_ready = required_addon is None or (
            latest_states is not None and latest_states.get(required_addon) is True
        )
        if owner_replaced and addon_ready:
            message = "Fcitx 5 已重新启动"
            if required_addon:
                message += f"，并已启用 {required_addon} addon"
            return FcitxRestartResult(True, message, owner=latest_owner)

        addon_disabled = (
            owner_replaced
            and required_addon is not None
            and latest_states is not None
            and latest_states.get(required_addon) is False
        )
        if addon_disabled and not enable_attempted:
            enable_attempted = True
            if not set_fcitx_addon_enabled(required_addon, True):
                return FcitxRestartResult(
                    False,
                    f"已发现 addon={required_addon}，但自动启用失败",
                    owner=latest_owner,
                )
            previous_owner = latest_owner
            try:
                process = _spawn_fcitx(executable)
            except OSError as exc:
                return FcitxRestartResult(
                    False,
                    f"已启用 addon={required_addon}，但无法再次启动 Fcitx 5：{exc}",
                    owner=latest_owner,
                )
            time.sleep(max(0.01, poll_interval))
            continue

        return_code = process.poll()
        if return_code not in (None, 0):
            return FcitxRestartResult(
                False,
                f"Fcitx 5 重启进程异常退出（exit={return_code}）",
                owner=latest_owner,
            )
        time.sleep(max(0.01, poll_interval))

    enabled_text = (
        "无法查询"
        if latest_states is None
        else ", ".join(
            sorted(name for name, enabled in latest_states.items() if enabled)
        )
        or "空"
    )
    if required_addon and latest_states is not None and required_addon in latest_states:
        required_text = f"；addon={required_addon} 已发现但仍未启用"
    else:
        required_text = (
            f"；未发现所需 addon={required_addon}" if required_addon else ""
        )
    return FcitxRestartResult(
        False,
        "等待新 Fcitx 5 实例就绪超时；"
        f"owner={latest_owner or '无'}，enabled_addons={enabled_text}{required_text}",
        owner=latest_owner,
    )
