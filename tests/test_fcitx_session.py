from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app import fcitx_session


def test_session_environment_removes_legacy_addon_override(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FCITX_ADDON_DIRS", "/broken/override")
    environment = fcitx_session.session_environment()
    assert "FCITX_ADDON_DIRS" not in environment


def test_parse_fcitx_addon_states_distinguishes_discovered_from_enabled():
    payload = {
        "type": "a(sssibb)",
        "data": [
            [
                ["vocotype", "VoCoType", "", 3, True, False],
                ["rime", "Rime", "", 2, True, True],
            ]
        ],
    }
    assert fcitx_session.parse_fcitx_addon_states(payload) == {
        "vocotype": False,
        "rime": True,
    }
    assert fcitx_session.parse_fcitx_addon_states({"data": [[]]}) == {}
    assert fcitx_session.parse_fcitx_addon_states({"data": "broken"}) is None


def test_set_fcitx_addon_enabled_uses_controller_state_signature(
    monkeypatch: pytest.MonkeyPatch,
):
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(fcitx_session.shutil, "which", lambda _name: "/usr/bin/busctl")
    monkeypatch.setattr(
        fcitx_session,
        "session_environment",
        lambda _base=None: {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus"},
    )
    monkeypatch.setattr(fcitx_session.subprocess, "run", fake_run)

    assert fcitx_session.set_fcitx_addon_enabled("vocotype", True)
    command, kwargs = calls[0]
    assert command == [
        "/usr/bin/busctl",
        "--user",
        "call",
        "org.fcitx.Fcitx5",
        "/controller",
        "org.fcitx.Fcitx.Controller1",
        "SetAddonsState",
        "a(sb)",
        "1",
        "vocotype",
        "true",
    ]
    assert kwargs["env"]["DBUS_SESSION_BUS_ADDRESS"].endswith("/bus")


def test_restart_fcitx_session_does_not_pipe_or_wait_for_persistent_daemon(
    monkeypatch: pytest.MonkeyPatch,
):
    owners = iter([":1.10", ":1.10", ":1.11"])
    addon_states = iter([{}, {"vocotype": True}])
    popen_calls: list[tuple[list[str], dict[str, object]]] = []

    class FakeProcess:
        def poll(self):
            return None

    def fake_popen(command, **kwargs):
        popen_calls.append((command, kwargs))
        return FakeProcess()

    monkeypatch.setattr(fcitx_session.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(fcitx_session, "query_fcitx_bus_owner", lambda: next(owners))
    monkeypatch.setattr(
        fcitx_session,
        "query_fcitx_addon_states",
        lambda: next(addon_states),
    )
    monkeypatch.setattr(fcitx_session.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(fcitx_session.time, "sleep", lambda _seconds: None)

    result = fcitx_session.restart_fcitx_session(
        timeout=1.0,
        required_addon="vocotype",
        poll_interval=0.01,
    )

    assert result.success, result.message
    assert result.owner == ":1.11"
    assert len(popen_calls) == 1
    command, kwargs = popen_calls[0]
    assert command == ["/usr/bin/fcitx5", "-r", "-d"]
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["stdout"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.DEVNULL
    assert kwargs["start_new_session"] is True
    assert "FCITX_ADDON_DIRS" not in kwargs["env"]


def test_restart_fcitx_session_enables_disabled_required_addon(
    monkeypatch: pytest.MonkeyPatch,
):
    owners = iter([":1.10", ":1.11", ":1.12"])
    addon_states = iter([{"vocotype": False}, {"vocotype": True}])
    enable_calls: list[tuple[str, bool]] = []
    popen_calls = 0

    class FakeProcess:
        def poll(self):
            return None

    def fake_popen(*_args, **_kwargs):
        nonlocal popen_calls
        popen_calls += 1
        return FakeProcess()

    monkeypatch.setattr(fcitx_session.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(fcitx_session, "query_fcitx_bus_owner", lambda: next(owners))
    monkeypatch.setattr(
        fcitx_session,
        "query_fcitx_addon_states",
        lambda: next(addon_states),
    )
    monkeypatch.setattr(
        fcitx_session,
        "set_fcitx_addon_enabled",
        lambda name, enabled=True: enable_calls.append((name, enabled)) or True,
    )
    monkeypatch.setattr(fcitx_session.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(fcitx_session.time, "sleep", lambda _seconds: None)

    result = fcitx_session.restart_fcitx_session(
        timeout=1.0,
        required_addon="vocotype",
        poll_interval=0.01,
    )

    assert result.success, result.message
    assert result.owner == ":1.12"
    assert enable_calls == [("vocotype", True)]
    assert popen_calls == 2


def test_restart_fcitx_session_reports_missing_required_addon(
    monkeypatch: pytest.MonkeyPatch,
):
    class FakeProcess:
        def poll(self):
            return None

    owners = iter([":1.10", ":1.11", ":1.11", ":1.11"])
    monkeypatch.setattr(fcitx_session.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(fcitx_session, "query_fcitx_bus_owner", lambda: next(owners, ":1.11"))
    monkeypatch.setattr(
        fcitx_session,
        "query_fcitx_addon_states",
        lambda: {"keyboard": True},
    )
    monkeypatch.setattr(fcitx_session.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess())

    now = iter([0.0, 0.0, 1.0])
    monkeypatch.setattr(fcitx_session.time, "monotonic", lambda: next(now, 1.0))
    monkeypatch.setattr(fcitx_session.time, "sleep", lambda _seconds: None)

    result = fcitx_session.restart_fcitx_session(
        timeout=0.5,
        required_addon="vocotype",
    )
    assert not result.success
    assert "未发现所需 addon=vocotype" in result.message
    assert "keyboard" in result.message


def test_migrate_legacy_fcitx_profile_restores_rime_and_keeps_backup(
    tmp_path: Path,
):
    profile = tmp_path / "profile"
    original = """[Groups/0]
# Group Name
Name=默认
# Default Input Method
DefaultIM=vocotype

[Groups/0/Items/0]
# Name
Name=keyboard-us
# Layout
Layout=

[Groups/0/Items/1]
# Name
Name=vocotype
# Layout
Layout=

[Groups/0/Items/2]
# Name
Name=rime
# Layout
Layout=

[GroupOrder]
0=默认
"""
    profile.write_text(original, encoding="utf-8")

    references = fcitx_session.legacy_fcitx_profile_references(profile)
    assert references == ("Groups/0:DefaultIM", "Groups/0/Items/1")

    result = fcitx_session.migrate_legacy_fcitx_profile(profile)

    assert result.changed
    assert result.removed_entries == 1
    assert result.restored_defaults == (("0", "rime"),)
    assert result.backup == tmp_path / "profile.vocotype-backup"
    assert result.backup.read_text(encoding="utf-8") == original

    migrated = profile.read_text(encoding="utf-8")
    assert "DefaultIM=rime" in migrated
    assert "Name=vocotype" not in migrated
    assert "[Groups/0/Items/0]" in migrated
    assert "Name=keyboard-us" in migrated
    assert "[Groups/0/Items/1]" in migrated
    assert "Name=rime" in migrated
    assert "[Groups/0/Items/2]" not in migrated
    assert "[GroupOrder]" in migrated
    assert fcitx_session.legacy_fcitx_profile_references(profile) == ()

    second = fcitx_session.migrate_legacy_fcitx_profile(profile)
    assert not second.changed
    assert result.backup.read_text(encoding="utf-8") == original
