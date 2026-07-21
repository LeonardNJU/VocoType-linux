from __future__ import annotations

import subprocess

import pytest

from app import fcitx_session


def test_session_environment_removes_legacy_addon_override(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FCITX_ADDON_DIRS", "/broken/override")
    environment = fcitx_session.session_environment()
    assert "FCITX_ADDON_DIRS" not in environment


def test_restart_fcitx_session_does_not_pipe_or_wait_for_persistent_daemon(
    monkeypatch: pytest.MonkeyPatch,
):
    owners = iter([":1.10", ":1.10", ":1.11"])
    addons = iter([set(), {"vocotype"}])
    popen_calls: list[tuple[list[str], dict[str, object]]] = []

    class FakeProcess:
        def poll(self):
            return None

    def fake_popen(command, **kwargs):
        popen_calls.append((command, kwargs))
        return FakeProcess()

    monkeypatch.setattr(fcitx_session.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(fcitx_session, "query_fcitx_bus_owner", lambda: next(owners))
    monkeypatch.setattr(fcitx_session, "query_fcitx_addon_names", lambda: next(addons))
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


def test_restart_fcitx_session_reports_missing_required_addon(
    monkeypatch: pytest.MonkeyPatch,
):
    class FakeProcess:
        def poll(self):
            return None

    owners = iter([":1.10", ":1.11", ":1.11", ":1.11"])
    monkeypatch.setattr(fcitx_session.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(fcitx_session, "query_fcitx_bus_owner", lambda: next(owners, ":1.11"))
    monkeypatch.setattr(fcitx_session, "query_fcitx_addon_names", lambda: {"keyboard"})
    monkeypatch.setattr(fcitx_session.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess())

    now = iter([0.0, 0.0, 1.0])
    monkeypatch.setattr(fcitx_session.time, "monotonic", lambda: next(now, 1.0))
    monkeypatch.setattr(fcitx_session.time, "sleep", lambda _seconds: None)

    result = fcitx_session.restart_fcitx_session(
        timeout=0.5,
        required_addon="vocotype",
    )
    assert not result.success
    assert "未加载所需 addon=vocotype" in result.message
    assert "keyboard" in result.message
