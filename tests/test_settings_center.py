from __future__ import annotations

import io
import json
import os
import socket
import subprocess
import tarfile
import threading
from types import SimpleNamespace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from app.slm_polisher import SLMPolisher
from app.text_normalizer import normalize_text
from settings_center import config_service
from settings_center.doctor import run_doctor
from settings_center.feedback import build_issue_url, submit_feedback
from settings_center.setup_manager import (
    InstallOptions,
    UninstallOptions,
    fcitx_installer_command,
    fcitx_uninstaller_command,
    ibus_installer_command,
    ibus_uninstaller_command,
    installer_command,
    integration_status,
    native_package_removal_command,
    parse_install_progress,
    restart_fcitx,
    restart_ibus_backend,
)
from settings_center.support_bundle import create_support_bundle


@pytest.fixture
def isolated_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / "home"
    config = home / ".config"
    home.mkdir()
    config.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    return home


def test_settings_css_uses_gtk_theme_colors():
    from settings_center.application import CSS, Gtk

    css = CSS.decode("utf-8")
    for semantic_color in (
        "@theme_bg_color",
        "@theme_fg_color",
        "@theme_base_color",
        "@theme_text_color",
        "@theme_selected_bg_color",
        "@theme_selected_fg_color",
    ):
        assert semantic_color in css

    for hardcoded_surface in ("#f6f7f9", "#ffffff", "#eef0f3", "#f2f5f8"):
        assert hardcoded_surface not in css.lower()

    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)


def test_normalization_master_switch_and_independent_styles():
    source = "二零二六年五月十一号下午三点二十分跑了三百二十米花了一百二十八元"
    assert normalize_text(source) == "2026/05/11 15:20跑了320m花了¥128"
    assert normalize_text(source, config={"enabled": False}) == source
    assert normalize_text(
        source,
        config={
            "compact_dates": False,
            "compact_times": False,
            "compact_distances": False,
            "currency_symbols": False,
        },
    ) == "2026年5月11号下午3点20分跑了320米花了128元"


def test_compact_styles_avoid_bare_point_false_positive_and_format_negative_money():
    assert normalize_text("给我三点建议") == "给我三点建议"
    assert normalize_text("退款金额是负三十二块五") == "退款金额是-¥32.5"
    assert normalize_text("晚上十二点半") == "00:30"


def test_compact_style_respects_protected_terms(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    terms = tmp_path / "terms.yaml"
    terms.write_text(
        """
terms:
  - canonical: 100米计划
    aliases: [hundred-meter]
    protect: true
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("VOCOTYPE_TERMS_FILE", str(terms))
    from app import term_lexicon

    term_lexicon._reset_term_lexicon_cache()
    try:
        assert normalize_text("hundred-meter和一百米") == "100米计划和100m"
    finally:
        term_lexicon._reset_term_lexicon_cache()


def test_config_service_synchronizes_runtimes_and_redacts_secrets(isolated_home: Path):
    payload = config_service.load_runtime_config()
    payload["normalization"]["enabled"] = False
    payload["slm"].update(
        {
            "enabled": True,
            "endpoint": "https://example.test/v1",
            "api_key": "sk-secret-value",
            "api_key_env": "VOCOTYPE_TEST_KEY",
        }
    )
    ibus, fcitx = config_service.save_runtime_config(payload)

    assert json.loads(ibus.read_text(encoding="utf-8")) == json.loads(
        fcitx.read_text(encoding="utf-8")
    )
    assert (ibus.stat().st_mode & 0o777) == 0o600
    assert (fcitx.stat().st_mode & 0o777) == 0o600
    redacted = config_service.sanitize_config(payload)
    assert redacted["slm"]["api_key"] == "<redacted>"
    assert redacted["slm"]["api_key_env"] == "<redacted>"



def test_runtime_config_remembers_last_lifecycle_framework(isolated_home: Path):
    config_service.update_runtime_sections(
        {"ui": {"lifecycle_framework": "fcitx5"}}
    )

    loaded = config_service.load_runtime_config()
    assert loaded["ui"]["lifecycle_framework"] == "fcitx5"
    assert json.loads(config_service.ibus_config_path().read_text(encoding="utf-8"))[
        "ui"
    ]["lifecycle_framework"] == "fcitx5"
    assert json.loads(config_service.fcitx_backend_path().read_text(encoding="utf-8"))[
        "ui"
    ]["lifecycle_framework"] == "fcitx5"

def test_audio_config_round_trip(isolated_home: Path):
    path = config_service.save_audio_config(
        device_name="USB Microphone",
        device_id=7,
        sample_rate=48000,
    )
    assert path == isolated_home / ".config/vocotype/audio.conf"
    assert (path.stat().st_mode & 0o777) == 0o600
    assert config_service.load_audio_config() == {
        "device_name": "USB Microphone",
        "device_id": 7,
        "sample_rate": 48000,
    }


def test_audio_config_records_and_clears_verification(isolated_home: Path):
    config_service.save_audio_config(
        device_name="USB Microphone",
        device_id=7,
        sample_rate=48000,
        tested_at="2026-07-21T00:00:00+00:00",
        tested_device_id=7,
        test_peak=0.5,
        test_rms=0.1,
        preserve_test=False,
    )
    loaded = config_service.load_audio_config()
    assert loaded["tested_device_id"] == 7
    assert loaded["test_peak"] == pytest.approx(0.5)
    assert loaded["test_rms"] == pytest.approx(0.1)

    config_service.save_audio_config(
        device_name="Quiet Microphone",
        device_id=8,
        sample_rate=44100,
        preserve_test=False,
    )
    loaded = config_service.load_audio_config()
    assert loaded["device_id"] == 8
    assert "tested_at" not in loaded
    assert "tested_device_id" not in loaded


def test_fcitx_module_config_round_trip_removes_legacy_polish_inversion(
    isolated_home: Path,
):
    path = config_service.fcitx_module_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "PolishByDefault=True\nPolishMinChars=8\n",
        encoding="utf-8",
    )

    saved = config_service.save_fcitx_module_config(
        {
            "PolishMinChars": 12,
            "EnableThinking": False,
            "PanelStyle": "minimal",
        }
    )
    text = saved.read_text(encoding="utf-8")
    assert "PolishByDefault" not in text
    assert "PolishMinChars=12" in text
    assert "PanelStyle=minimal" in text
    loaded = config_service.load_fcitx_module_config()
    assert "polishbydefault" not in loaded
    assert loaded["polishminchars"] == "12"
    assert loaded["panelstyle"] == "minimal"


def test_slm_api_key_can_come_from_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VOCOTYPE_TEST_API_KEY", "env-secret")
    polisher = SLMPolisher(
        {
            "enabled": True,
            "provider": "remote",
            "api_key": "",
            "api_key_env": "VOCOTYPE_TEST_API_KEY",
        }
    )
    assert polisher.api_key == "env-secret"
    assert polisher._request_headers()["Authorization"] == "Bearer env-secret"


def test_slm_api_key_misfiled_as_environment_name_is_migrated():
    secret = "sk-12345678901234567890123456789012"
    polisher = SLMPolisher(
        {
            "enabled": True,
            "provider": "remote",
            "api_key": "",
            "api_key_env": secret,
        }
    )
    assert polisher.api_key == secret
    assert polisher.api_key_env == ""
    assert "误填" in polisher.credential_warning
    assert polisher._request_headers()["Authorization"] == f"Bearer {secret}"


def test_issue_url_is_prefilled_without_transmitting():
    url = build_issue_url("F9 不工作", doctor_text="[fail] service")
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    assert parsed.netloc == "github.com"
    assert "F9 不工作" in query["body"][0]
    assert "VoCoType Doctor" in query["body"][0]


def test_feedback_endpoint_receives_json(tmp_path: Path):
    captured: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            captured.update(json.loads(self.rfile.read(length).decode("utf-8")))
            body = b'{"ok":true,"ticket":"T-1"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    try:
        result = submit_feedback(
            f"http://127.0.0.1:{server.server_port}/feedback",
            "测试反馈",
            doctor_payload=[{"status": "pass"}],
        )
    finally:
        thread.join(timeout=2)
        server.server_close()
    assert result["ok"] is True
    assert captured["message"] == "测试反馈"
    assert captured["doctor"] == [{"status": "pass"}]


def test_feedback_endpoint_rejects_insecure_nonlocal_http():
    with pytest.raises(ValueError, match="HTTPS"):
        submit_feedback("http://example.test/feedback", "unsafe")


def test_support_bundle_redacts_config_and_omits_dictionary_contents(
    isolated_home: Path,
    tmp_path: Path,
):
    payload = config_service.load_runtime_config()
    payload["slm"]["api_key"] = "super-secret"
    config_service.save_runtime_config(payload)
    dictionary = config_service.terms_path()
    dictionary.parent.mkdir(parents=True, exist_ok=True)
    dictionary.write_text("terms:\n  - canonical: PrivateProjectName\n", encoding="utf-8")
    log_path = isolated_home / ".local/share/vocotype/ibus.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        "Authorization: Bearer top-secret-token\n"
        "api_key=sk-supersecret123456\n"
        "转录完成，最终文本: 这是绝对不能泄露的口述内容\n",
        encoding="utf-8",
    )

    output = tmp_path / "support.tar.gz"
    create_support_bundle(output)
    assert output.is_file()
    assert (output.stat().st_mode & 0o777) == 0o600
    with tarfile.open(output, "r:gz") as archive:
        names = archive.getnames()
        assert any(name.endswith("config/fcitx5-backend.json") for name in names)
        assert any(name.endswith("config/terms-metadata.json") for name in names)
        assert any(name.endswith("config/audio.json") for name in names)
        assert not any(name.endswith("terms.yaml") for name in names)
        member = next(name for name in names if name.endswith("config/fcitx5-backend.json"))
        content = archive.extractfile(member).read().decode("utf-8")
        assert "super-secret" not in content
        assert "<redacted>" in content
        all_text = b"".join(
            archive.extractfile(name).read()
            for name in names
            if archive.getmember(name).isfile() and archive.getmember(name).size < 2_000_000
        ).decode("utf-8", errors="ignore")
        assert "PrivateProjectName" not in all_text
        assert "top-secret-token" not in all_text
        assert "sk-supersecret123456" not in all_text
        assert "这是绝对不能泄露的口述内容" not in all_text
        assert "[VoCoType user text redacted]" in all_text
        assert any(name.endswith("/PRIVACY.txt") for name in names)
        assert "<redacted>" in all_text


def test_gui_installers_use_noninteractive_polkit_ready_mode():
    root = Path("/tmp/VocoType-linux")
    fcitx = fcitx_installer_command(root)
    ibus = ibus_installer_command(
        root,
        InstallOptions(rime_enabled=True, rime_schema="rime_ice", component_mode="system"),
    )
    assert installer_command(root) == fcitx
    for command in (fcitx, ibus):
        assert "--non-interactive" in command
        assert "--preserve-config" in command
        assert "--install-system-deps" in command
        assert "--bootstrap-uv" in command
        assert command[command.index("--python-choice") + 1] == "user"
        assert command[command.index("--slm-provider") + 1] == "preserve"
    assert ibus[1].endswith("ibus/scripts/install-gui.sh")
    assert ibus[ibus.index("--rime") + 1] == "enabled"
    assert ibus[ibus.index("--rime-schema") + 1] == "rime_ice"
    assert ibus[ibus.index("--component-mode") + 1] == "system"



def test_gui_uninstallers_use_symmetric_noninteractive_entrypoints():
    root = Path("/tmp/VocoType-linux")
    options = UninstallOptions(
        purge_runtime=True,
        remove_user_data=True,
        remove_system_component=True,
    )
    fcitx = fcitx_uninstaller_command(root, options)
    ibus = ibus_uninstaller_command(root, options)
    assert fcitx[1].endswith("fcitx5/scripts/uninstall-gui.sh")
    assert ibus[1].endswith("ibus/scripts/uninstall-gui.sh")
    for command in (fcitx, ibus):
        assert "--purge-runtime" in command
        assert "--remove-user-data" in command
    assert "--remove-system-component" not in fcitx
    assert "--remove-system-component" in ibus


def _write_executable(path: Path, content: str = "#!/bin/sh\nexit 0\n") -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _run_uninstaller(
    script: str,
    home: Path,
    *args: str,
    command_overrides: dict[str, str] | None = None,
    env_overrides: dict[str, str] | None = None,
    timeout: float = 15,
) -> subprocess.CompletedProcess[str]:
    fake_bin = home / "bin"
    fake_bin.mkdir(parents=True, exist_ok=True)
    overrides = command_overrides or {}
    for command in ("systemctl", "fcitx5", "ibus"):
        _write_executable(fake_bin / command, overrides.get(command, "#!/bin/sh\nexit 0\n"))
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "VOCOTYPE_SYSTEM_PREFIX": str(home / "system"),
            "PATH": f"{fake_bin}:{env['PATH']}",
        }
    )
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        ["bash", script, *args],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )



def test_ibus_restart_timeout_is_reported_in_desktop_sessions(tmp_path: Path):
    home = tmp_path / "home"
    (home / ".local/share/vocotype/ibus").mkdir(parents=True, exist_ok=True)
    (home / ".local/share/vocotype/ibus/main.py").write_text("main", encoding="utf-8")

    result = _run_uninstaller(
        "ibus/scripts/uninstall-gui.sh",
        home,
        command_overrides={"ibus": "#!/bin/sh\nsleep 30\n"},
        env_overrides={
            "VOCOTYPE_RESTART_TIMEOUT_SECONDS": "1",
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/fake-session-bus",
            "DISPLAY": ":99",
        },
        timeout=5,
    )
    assert result.returncode != 0
    assert "RESTART_FAILED: VoCoType 文件已清理，但 IBus 重启失败" in result.stderr
    assert not (home / ".local/share/vocotype/ibus").exists()

def test_ibus_gui_uninstall_preserves_runtime_cache_config_and_shared_launcher(tmp_path: Path):
    home = tmp_path / "home"
    runtime = home / ".local/share/vocotype"
    for directory in ("app", "ibus", "settings_center", ".venv"):
        (runtime / directory).mkdir(parents=True, exist_ok=True)
    (runtime / "vocotype_version.py").write_text("version", encoding="utf-8")
    (runtime / ".venv/keep").write_text("cached", encoding="utf-8")
    component = home / ".local/share/ibus/component/vocotype.xml"
    component.parent.mkdir(parents=True, exist_ok=True)
    component.write_text("component", encoding="utf-8")
    launcher = home / ".local/libexec/ibus-engine-vocotype"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text("launcher", encoding="utf-8")
    shared_launcher = home / ".local/bin/vocotype-settings"
    shared_launcher.parent.mkdir(parents=True, exist_ok=True)
    shared_launcher.write_text("settings", encoding="utf-8")
    (home / ".local/share/vocotype-fcitx5/backend").mkdir(parents=True, exist_ok=True)
    (home / ".local/share/vocotype-fcitx5/backend/fcitx5_server.py").write_text("server", encoding="utf-8")
    user_config = home / ".config/vocotype/terms.yaml"
    user_config.parent.mkdir(parents=True, exist_ok=True)
    user_config.write_text("terms: []", encoding="utf-8")

    result = _run_uninstaller("ibus/scripts/uninstall-gui.sh", home)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (runtime / "app").exists()
    assert not (runtime / "ibus").exists()
    assert not (runtime / "settings_center").exists()
    assert not (runtime / "vocotype_version.py").exists()
    assert (runtime / ".venv/keep").is_file()
    assert not component.exists()
    assert not launcher.exists()
    assert shared_launcher.exists()
    assert user_config.exists()
    assert "用户配置已保留" in result.stdout


def test_fcitx_gui_uninstall_purges_runtime_and_user_integration_only(tmp_path: Path):
    home = tmp_path / "home"
    runtime = home / ".local/share/vocotype-fcitx5"
    (runtime / ".venv").mkdir(parents=True, exist_ok=True)
    (runtime / ".venv/keep").write_text("cached", encoding="utf-8")
    artifacts = [
        home / ".local/lib/fcitx5/vocotype.so",
        home / ".local/lib64/fcitx5/libvocotype.so",
        home / ".local/share/fcitx5/addon/vocotype.conf",
        home / ".config/environment.d/fcitx5-vocotype.conf",
        home / ".config/systemd/user/vocotype-fcitx5-backend.service",
        home / ".local/bin/vocotype-fcitx5-backend",
        home / ".local/bin/vocotype-fcitx5-recorder",
    ]
    for artifact in artifacts:
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("artifact", encoding="utf-8")
    shared_launcher = home / ".local/bin/vocotype-settings"
    shared_launcher.write_text("settings", encoding="utf-8")
    (home / ".local/share/vocotype/ibus").mkdir(parents=True, exist_ok=True)
    (home / ".local/share/vocotype/ibus/main.py").write_text("main", encoding="utf-8")
    user_config = home / ".config/vocotype/audio.conf"
    user_config.parent.mkdir(parents=True, exist_ok=True)
    user_config.write_text("[audio]", encoding="utf-8")

    result = _run_uninstaller(
        "fcitx5/scripts/uninstall-gui.sh",
        home,
        "--purge-runtime",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not runtime.exists()
    assert all(not artifact.exists() for artifact in artifacts)
    assert shared_launcher.exists()
    assert user_config.exists()
    assert "VoCoType（Fcitx 5）integration 已卸载" in result.stdout




def _create_source_fcitx_system_fixture(home: Path) -> tuple[Path, Path, Path]:
    prefix = home / 'system'
    module = prefix / 'lib/fcitx5/vocotype.so'
    addon = prefix / 'share/fcitx5/addon/vocotype.conf'
    marker = prefix / 'share/vocotype/.source-fcitx-integration'
    module.parent.mkdir(parents=True, exist_ok=True)
    addon.parent.mkdir(parents=True, exist_ok=True)
    marker.parent.mkdir(parents=True, exist_ok=True)
    module.write_bytes(b'module')
    addon.write_text('[Addon]\nLibrary=vocotype\n', encoding='utf-8')
    marker.write_text(
        'managed-by=source-installer\nversion=2.2.3\n'
        f'module={module}\naddon={addon}\n',
        encoding='utf-8',
    )
    return module, addon, marker


def test_fcitx_gui_uninstall_removes_source_system_addon_by_default(tmp_path: Path):
    home = tmp_path / 'home'
    module, addon, marker = _create_source_fcitx_system_fixture(home)
    result = _run_uninstaller('fcitx5/scripts/uninstall-gui.sh', home)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not module.exists()
    assert not addon.exists()
    assert not marker.exists()
    assert '系统 VoCoType（Fcitx 5）addon 已移除' in result.stdout


def test_fcitx_gui_uninstall_can_explicitly_keep_source_system_addon(tmp_path: Path):
    home = tmp_path / 'home'
    module, addon, marker = _create_source_fcitx_system_fixture(home)
    result = _run_uninstaller(
        'fcitx5/scripts/uninstall-gui.sh',
        home,
        '--keep-system-integration',
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert module.exists()
    assert addon.exists()
    assert marker.exists()
    assert '保留源码安装器管理的系统 Fcitx addon' in result.stdout


def test_last_user_integration_removes_broken_shared_launcher(tmp_path: Path):
    home = tmp_path / "home"
    runtime = home / ".local/share/vocotype"
    (runtime / "ibus").mkdir(parents=True, exist_ok=True)
    (runtime / "ibus/main.py").write_text("main", encoding="utf-8")
    shared_launcher = home / ".local/bin/vocotype-settings"
    shared_launcher.parent.mkdir(parents=True, exist_ok=True)
    shared_launcher.write_text("settings", encoding="utf-8")

    result = _run_uninstaller("ibus/scripts/uninstall-gui.sh", home)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not shared_launcher.exists()

def test_gui_uninstall_can_explicitly_remove_shared_user_data(tmp_path: Path):
    home = tmp_path / "home"
    (home / ".local/share/vocotype").mkdir(parents=True, exist_ok=True)
    user_config = home / ".config/vocotype/terms.yaml"
    user_config.parent.mkdir(parents=True, exist_ok=True)
    user_config.write_text("terms: []", encoding="utf-8")

    result = _run_uninstaller(
        "ibus/scripts/uninstall-gui.sh",
        home,
        "--purge-runtime",
        "--remove-user-data",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not user_config.parent.exists()



def _touch(path: Path, *, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("fixture", encoding="utf-8")
    if executable:
        path.chmod(0o755)


def test_integration_status_distinguishes_absent_partial_and_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    home = tmp_path / "home"
    prefix = tmp_path / "usr"
    monkeypatch.setattr(
        "settings_center.setup_manager.inspect_required_models",
        lambda **_kwargs: {
            name: {"complete": True}
            for name in ("asr", "vad", "punc")
        },
    )

    absent = integration_status(
        "fcitx5",
        home=home,
        system_prefix=prefix,
        fcitx_socket_path=tmp_path / "missing.sock",
        fcitx_addon_loaded=False,
    )
    assert absent.state == "absent"
    assert "module" in absent.missing

    module = home / ".local/lib/fcitx5/vocotype.so"
    _touch(module)
    _touch(home / ".local/share/fcitx5/addon/vocotype.conf")
    partial = integration_status(
        "fcitx5",
        home=home,
        system_prefix=prefix,
        fcitx_socket_path=tmp_path / "missing.sock",
        fcitx_addon_loaded=False,
    )
    assert partial.state == "partial"
    assert "module" in partial.present
    assert "后端代码" in partial.missing
    assert "Python 运行环境" in partial.missing
    assert "麦克风验收" not in partial.missing
    assert "后端 IPC" in partial.missing

    _touch(home / ".config/systemd/user/vocotype-fcitx5-backend.service")
    _touch(home / ".local/bin/vocotype-fcitx5-backend", executable=True)
    _touch(home / ".local/share/vocotype-fcitx5/backend/fcitx5_server.py")
    _touch(home / ".local/share/vocotype-fcitx5/.venv/bin/python", executable=True)
    module.write_bytes(b"fixture\0PanelStyle\0minimal")
    socket_path = tmp_path / "vocotype.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server.bind(str(socket_path))
        complete = integration_status(
            "fcitx5",
            home=home,
            system_prefix=prefix,
            fcitx_socket_path=socket_path,
            fcitx_addon_loaded=True,
        )
    finally:
        server.close()
    assert complete.state == "complete"
    assert complete.missing == ()



def test_old_fcitx_module_is_reported_as_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    home = tmp_path / "home"
    prefix = tmp_path / "usr"
    monkeypatch.setattr(
        "settings_center.setup_manager.inspect_required_models",
        lambda **_kwargs: {
            name: {"complete": True}
            for name in ("asr", "vad", "punc")
        },
    )
    module = home / ".local/lib/fcitx5/vocotype.so"
    _touch(module)
    _touch(home / ".local/share/fcitx5/addon/vocotype.conf")
    _touch(home / ".config/systemd/user/vocotype-fcitx5-backend.service")
    _touch(home / ".local/bin/vocotype-fcitx5-backend", executable=True)
    _touch(home / ".local/share/vocotype-fcitx5/backend/fcitx5_server.py")
    _touch(home / ".local/share/vocotype-fcitx5/.venv/bin/python", executable=True)

    status = integration_status(
        "fcitx5",
        home=home,
        system_prefix=prefix,
        fcitx_socket_path=tmp_path / "missing.sock",
        fcitx_addon_loaded=True,
    )

    assert status.state == "partial"
    assert "F9 状态样式支持（module 需要更新）" in status.missing

def test_ibus_status_requires_runtime_and_python_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    home = tmp_path / "home"
    prefix = tmp_path / "usr"
    monkeypatch.setattr(
        "settings_center.setup_manager.inspect_required_models",
        lambda **_kwargs: {
            name: {"complete": True}
            for name in ("asr", "vad", "punc")
        },
    )
    _touch(home / ".local/libexec/ibus-engine-vocotype", executable=True)
    _touch(home / ".local/share/ibus/component/vocotype.xml")

    partial = integration_status("ibus", home=home, system_prefix=prefix)
    assert partial.state == "partial"
    assert partial.missing == ("引擎代码", "Python 运行环境")

    _touch(home / ".local/share/vocotype/ibus/main.py")
    _touch(home / ".local/share/vocotype/.venv/bin/python", executable=True)
    complete = integration_status("ibus", home=home, system_prefix=prefix)
    assert complete.state == "complete"


def test_restart_fcitx_uses_nonblocking_session_helper(monkeypatch: pytest.MonkeyPatch):
    result = SimpleNamespace(
        success=True,
        message="Fcitx 5 已重新启动",
        startup_log="",
    )
    calls: list[dict[str, object]] = []

    def fake_restart(**kwargs):
        calls.append(kwargs)
        return result

    monkeypatch.setattr(
        "settings_center.setup_manager.restart_fcitx_session",
        fake_restart,
    )

    ok, message = restart_fcitx()
    assert ok, message
    assert message == "Fcitx 5 已重新启动"
    assert calls == [{"timeout": 10.0}]


def test_restart_ibus_backend_stops_only_vocotype_engine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    proc_root = tmp_path / "proc"
    for pid, command in {
        101: "python /home/user/.local/share/vocotype/ibus/main.py --ibus",
        102: "ibus-daemon --daemonize",
        103: "python unrelated.py --ibus",
    }.items():
        process_dir = proc_root / str(pid)
        process_dir.mkdir(parents=True)
        (process_dir / "cmdline").write_bytes(command.replace(" ", "\0").encode())
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(
        "settings_center.setup_manager.os.kill",
        lambda pid, sig: killed.append((pid, sig)),
    )

    ok, message = restart_ibus_backend(proc_root=proc_root)

    assert ok, message
    assert killed == [(101, 15)]
    assert "下次切换到 VoCoType" in message


def test_install_progress_parser_accepts_only_structured_stages():
    assert parse_install_progress("VOCOTYPE_PROGRESS:2:准备安装") == (0.02, "准备安装")
    assert parse_install_progress("VOCOTYPE_PROGRESS:100:完成") == (1.0, "完成")
    assert parse_install_progress("普通日志") is None
    assert parse_install_progress("VOCOTYPE_PROGRESS:101:错误") is None
    assert parse_install_progress("VOCOTYPE_PROGRESS:nope:错误") is None
    assert parse_install_progress("VOCOTYPE_PROGRESS:50:") is None


def test_native_package_removal_command_uses_available_package_manager(
    monkeypatch: pytest.MonkeyPatch,
):
    import settings_center.setup_manager as setup_manager

    monkeypatch.setattr(setup_manager, "native_package_present", lambda _root=None: True)
    monkeypatch.setattr(
        setup_manager.shutil,
        "which",
        lambda command: "/usr/bin/pacman" if command == "pacman" else None,
    )
    assert native_package_removal_command() == "sudo pacman -Rns vocotype-linux"

def test_settings_desktop_entry_and_console_scripts_exist():
    desktop = Path("data/applications/io.github.LeonardNJU.VoCoType.Settings.desktop")
    assert desktop.is_file()
    assert "Exec=vocotype-settings" in desktop.read_text(encoding="utf-8")
    project = Path("pyproject.toml").read_text(encoding="utf-8")
    assert 'vocotype-settings = "settings_center.application:main"' in project
    assert 'vocotype-doctor = "settings_center.doctor:main"' in project
    assert '[tool.setuptools.data-files]' in project
    assert 'io.github.LeonardNJU.VoCoType.Settings.desktop' in project


def test_settings_application_exposes_both_install_paths():
    source = Path("settings_center/application.py").read_text(encoding="utf-8")
    assert "安装 / 修复 VoCoType（Fcitx 5）" in source
    assert "安装 / 修复 VoCoType（IBus）" in source
    assert 'uninstall_button = Gtk.Button(label=f"卸载 VoCoType（{title}）")' in source
    assert "UninstallOptions" in source
    assert "uninstall_framework" in source
    assert "launch_ibus_installer" not in source
    assert "lifecycle_stack = Gtk.Stack()" in source
    assert "lifecycle_switcher = Gtk.StackSwitcher()" in source
    assert 'lifecycle_stack.add_titled(ibus_panel, "ibus", "IBus")' in source
    assert 'lifecycle_stack.add_titled(fcitx_panel, "fcitx5", "Fcitx 5")' in source
    assert "lifecycle_switcher.set_homogeneous(True)" in source
    assert "lifecycle_switcher.set_hexpand(True)" in source
    assert "lifecycle_switcher.set_halign(Gtk.Align.FILL)" in source
    assert 'ui_config = self.runtime_config.get("ui")' in source
    assert '"lifecycle_framework"' in source
    assert 'self._on_lifecycle_framework_changed' in source
    assert 'update_runtime_sections(' in source
    assert 'lifecycle_stack.connect(' in source
    assert "button.set_hexpand(True)" in source
    assert "button.set_halign(Gtk.Align.FILL)" in source
    assert 'backend_button = Gtk.Button(label="重启 VoCoType 后台")' in source
    assert 'framework_button = Gtk.Button(label=f"重启 {title}")' in source
    assert "restart_ibus_backend" in source
    assert "Gtk.ProgressBar()" in source
    assert "progress_bar.pulse()" in source
    assert "正在准备卸载 VoCoType" in source
    assert "Gtk.MessageDialog" not in source
    assert "scroller.set_min_content_height(220)" in source
    assert "output.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)" in source
    assert "❌ 最近一次安装 / 修复 VoCoType" in source
    assert "⚠️ VoCoType（{name}）：安装不完整" in source
    assert "安装 / 修复 Fcitx 5" not in source
    assert "安装 / 修复 IBus" not in source
    assert "Polkit" in source
    assert "InstallOptions" in source
    assert 'self.stack.add_titled(playground_page, "playground", "Playground")' in source
    assert "录音 {int(RECORDING_DURATION_SECONDS)} 秒" in source
    assert "回放上次录音" in source
    assert "转录上次录音" in source
    assert "测试 AI 润色" in source
    assert "测试 AI 编辑" in source
    assert "self.playground_ai_controls.set_sensitive(False)" in source
    assert "请先在“AI 润色”页面" in (
        Path("settings_center/playground_service.py")
    ).read_text(encoding="utf-8")
    assert "save_audio_config" in source
    assert "Gtk.Expander()" in source
    assert "overview_doctor_scroll" not in source
    assert 'Gtk.Button(label="查看详情")' in source
    assert "快速检查后仅显示摘要" in source
    assert "继续配置麦克风" not in source
    assert "麦克风尚未通过 2 秒录音验收" not in source
    assert "程序安装与运行验收完成" in source
    assert "API Key 环境变量名（高级）" in source
    assert "直接 API Key" in source
    assert "remove_system_integration" in source
    assert 'self.stack.add_titled(recognition_page, "recognition", "逆文本标准化")' in source
    assert 'self.panel_style.append("minimal", "极简：🎤 录音中 / ⏳ 识别中")' in source
    assert 'self.panel_style.append("animated", "动画：绿黑状态动画")' in source
    assert '"PanelStyle": self.panel_style.get_active_id() or "minimal"' in source
    assert "Gtk.DrawingArea()" in source
    assert "self.playground_waveform.set_hexpand(True)" in source
    assert "visible_peak = max(" in source
    assert "display_peak = max(0.01" in source
    assert "waveform_callback=lambda envelope" in source
    assert "自动增益 +{result.gain_db:.1f} dB" in source
    assert "list_output_devices" in source
    assert "回放输出已切换到" in source
    assert "Fcitx：F9 默认润色" not in source
    assert "PolishByDefault" not in source
    assert "F9 始终直接输出；Shift+F9" in source


def test_installers_have_gui_noninteractive_paths_without_terminal_password_prompts():
    script = Path("fcitx5/scripts/install.sh").read_text(encoding="utf-8")
    assert 'mkdir -p "$INSTALL_DIR/scripts" "$INSTALL_DIR/installers"' in script
    assert 'rm -f "$HOME/.config/environment.d/fcitx5-vocotype.conf"' in script
    assert "FCITX_ADDON_DIRS=$HOME" not in script
    assert "manage-fcitx-system-integration.sh" in script
    assert "AUTH_REQUIRED: 即将弹出管理员授权窗口以安装 VoCoType（Fcitx 5）系统 addon" in script
    assert '"$HOME/.local/share/fcitx5/addon/vocotype.conf"' in script
    assert script.index('mkdir -p "$INSTALL_DIR/scripts" "$INSTALL_DIR/installers"') < script.index(
        'cp "$INSTALLER_DIR/setup-audio.py" "$INSTALLED_SETUP_AUDIO_SCRIPT"'
    )
    for fragment in (
        "--non-interactive",
        "--preserve-config",
        'cp -r "$PROJECT_DIR/settings_center"',
        'vocotype-settings',
        'io.github.LeonardNJU.VoCoType.Settings.desktop',
        '检测到已有本地 SLM 配置',
        'VOCOTYPE_PROJECT_DIR',
        '--install-system-deps',
        'pkexec',
        'pkexec --disable-internal-agent',
    ):
        assert fragment in script

    ibus_gui = Path("ibus/scripts/install-gui.sh").read_text(encoding="utf-8")
    assert ibus_gui.count("pkexec --disable-internal-agent") == 2

    uninstaller = Path("installers/uninstall-integration.sh").read_text(encoding="utf-8")
    assert 'if [[ "$NON_INTERACTIVE" == true ]]; then' in uninstaller
    assert 'pkexec --disable-internal-agent "$@"' in uninstaller


def test_shared_uninstaller_preserves_user_configuration_by_default():
    script = Path("installers/uninstall-integration.sh").read_text(encoding="utf-8")
    assert "正在停止 VoCoType（Fcitx 5）后台服务" in script
    assert "正在清理 VoCoType（IBus）用户级运行代码" in script
    assert "正在重启 Fcitx 5 以加载 VoCoType 变更" in script
    assert "REMOVE_USER_DATA=false" in script
    assert 'rm -rf "$VOCOTYPE_CONFIG_DIR"' in script
    assert '[[ "$REMOVE_USER_DATA" == true ]]' in script
    assert "用户配置已保留" in script
    assert "fcitx_user_present" in script
    assert "ibus_user_present" in script
    assert "REMOVE_SYSTEM_INTEGRATION=true" in script
    assert "--remove-system-integration" in script
    assert ".source-fcitx-integration" in script


def test_source_fcitx_helper_only_queries_package_database_for_real_usr():
    source = Path("installers/manage-fcitx-system-integration.sh").read_text(
        encoding="utf-8"
    )
    assert '[[ "$PREFIX" == /usr ]] || return 0' in source
    assert 'if output=$(dpkg-query -S -- "$path" 2>/dev/null); then' in source
    assert 'if output=$(rpm -qf --qf' in source
    assert 'if output=$(pacman -Qo -- "$path" 2>/dev/null); then' in source
    assert "*[!A-Za-z0-9+_.:@-]*" in source


def test_source_fcitx_system_helper_tracks_and_removes_owned_files(tmp_path: Path):
    helper = Path("installers/manage-fcitx-system-integration.sh").resolve()
    prefix = tmp_path / "usr"
    module = tmp_path / "vocotype.so"
    addon = tmp_path / "vocotype.conf"
    module.write_bytes(b"module")
    addon.write_text("[Addon]\nLibrary=vocotype\n", encoding="utf-8")
    env = {
        **os.environ,
        "VOCOTYPE_SYSTEM_PREFIX": str(prefix),
        "VOCOTYPE_SYSTEM_LIBDIR": str(prefix / "lib"),
    }
    result = subprocess.run(
        ["bash", str(helper), "install", str(module), str(addon), "2.2.3"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (prefix / "lib/fcitx5/vocotype.so").is_file()
    assert (prefix / "share/fcitx5/addon/vocotype.conf").is_file()
    marker = prefix / "share/vocotype/.source-fcitx-integration"
    assert "managed-by=source-installer" in marker.read_text(encoding="utf-8")

    result = subprocess.run(
        ["bash", str(helper), "uninstall"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (prefix / "lib/fcitx5/vocotype.so").exists()
    assert not marker.exists()


def test_ibus_gui_installer_uses_pkexec_and_never_reads_from_a_terminal():
    script = Path("ibus/scripts/install-gui.sh").read_text(encoding="utf-8")
    assert "--non-interactive" in script
    assert "--component-mode" in script
    assert "pkexec" in script
    assert "AUTH_REQUIRED" in script
    assert "read -r -p" not in script
    assert "sudo " not in script
    assert "xdg-terminal-exec" not in script
    assert "gnome-terminal" not in script


def test_system_dependency_helper_has_fixed_actions_and_no_arbitrary_package_arguments():
    helper = Path("installers/install-system-dependencies.sh").read_text(encoding="utf-8")
    assert "fcitx5|ibus|ibus-rime" in helper
    assert "apt-get install" in helper
    assert "dnf install" in helper
    assert "pacman -S --needed" in helper
    assert 'PACKAGES=("$@")' not in helper


def test_setup_manager_does_not_launch_terminal_emulators():
    source = Path("settings_center/setup_manager.py").read_text(encoding="utf-8")
    for terminal in ("gnome-terminal", "xdg-terminal-exec", "konsole", "kitty", "xterm"):
        assert terminal not in source



@pytest.mark.parametrize(
    ("addon_rows", "expected_status"),
    [
        ([['vocotype', 'VoCoType Voice Input', '', 3, True, True]], 'pass'),
        ([['clipboard', 'Clipboard', '', 3, True, True]], 'fail'),
    ],
)
def test_doctor_uses_live_fcitx_getaddons_for_loaded_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    addon_rows: list[list[object]],
    expected_status: str,
):
    import settings_center.doctor as doctor_module

    module = tmp_path / 'usr/lib/fcitx5/vocotype.so'
    addon = tmp_path / 'usr/share/fcitx5/addon/vocotype.conf'
    module.parent.mkdir(parents=True)
    addon.parent.mkdir(parents=True)
    module.write_bytes(b'module')
    addon.write_text('[Addon]\nLibrary=vocotype\n', encoding='utf-8')
    paths = SimpleNamespace(
        fcitx_modules=(module,),
        fcitx_addons=(addon,),
        fcitx_services=(),
        ibus_launchers=(),
        ibus_components=(),
    )
    monkeypatch.setattr(doctor_module, 'installation_paths', lambda: paths)
    monkeypatch.setattr(
        doctor_module.shutil,
        'which',
        lambda command: f'/usr/bin/{command}'
        if command in {'busctl', 'fcitx5', 'systemctl', 'pkexec'}
        else None,
    )
    monkeypatch.setenv('XDG_RUNTIME_DIR', str(tmp_path / 'runtime'))

    def fake_run(argv: list[str], timeout: float = 5.0):
        if 'GetAddons' in argv:
            return subprocess.CompletedProcess(
                argv,
                0,
                json.dumps({'type': 'a(sssibb)', 'data': [addon_rows]}),
                '',
            )
        if argv and Path(argv[0]).name == 'systemctl':
            return subprocess.CompletedProcess(
                argv,
                0,
                'ActiveState=inactive\nSubState=dead\nNRestarts=0\n'
                'ExecMainStatus=0\nMainPID=0\n',
                '',
            )
        return subprocess.CompletedProcess(argv, 1, '', 'not mocked')

    monkeypatch.setattr(doctor_module, '_run', fake_run)
    check = next(item for item in run_doctor() if item.check_id == 'fcitx_loaded')
    assert check.status == expected_status
    if expected_status == 'fail':
        assert '没有创建 VoCoType addon' in check.summary


def test_doctor_reports_polkit_readiness(monkeypatch: pytest.MonkeyPatch):
    import settings_center.doctor as doctor_module

    original_which = doctor_module.shutil.which
    monkeypatch.setattr(
        doctor_module.shutil,
        "which",
        lambda command: "/usr/bin/pkexec" if command == "pkexec" else original_which(command),
    )
    check = next(item for item in run_doctor() if item.check_id == "polkit")
    assert check.status == "pass"
    assert "pkexec" in check.details
