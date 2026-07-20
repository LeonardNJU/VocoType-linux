from __future__ import annotations

import io
import json
import os
import subprocess
import tarfile
import threading
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
    native_package_removal_command,
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


def test_fcitx_module_config_round_trip(isolated_home: Path):
    path = config_service.save_fcitx_module_config(
        {
            "PolishByDefault": True,
            "PolishMinChars": 12,
            "EnableThinking": False,
        }
    )
    text = path.read_text(encoding="utf-8")
    assert "PolishByDefault=True" in text
    assert "PolishMinChars=12" in text
    loaded = config_service.load_fcitx_module_config()
    assert loaded["polishbydefault"] == "True"
    assert loaded["polishminchars"] == "12"


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


def _run_uninstaller(script: str, home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    fake_bin = home / "bin"
    fake_bin.mkdir(parents=True, exist_ok=True)
    for command in ("systemctl", "fcitx5", "ibus"):
        _write_executable(fake_bin / command)
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "PATH": f"{fake_bin}:{env['PATH']}",
        }
    )
    return subprocess.run(
        ["bash", script, *args],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )


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
    assert "Fcitx 5 用户级集成已卸载" in result.stdout



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
    assert "安装 / 修复 Fcitx 5" in source
    assert "安装 / 修复 IBus" in source
    assert "卸载 Fcitx 5" in source
    assert "卸载 IBus" in source
    assert "UninstallOptions" in source
    assert "uninstall_framework" in source
    assert "launch_ibus_installer" not in source
    assert "Polkit" in source
    assert "InstallOptions" in source
    assert "录音 2 秒测试" in source
    assert "save_audio_config" in source


def test_installers_have_gui_noninteractive_paths_without_terminal_password_prompts():
    script = Path("fcitx5/scripts/install.sh").read_text(encoding="utf-8")
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
    ):
        assert fragment in script


def test_shared_uninstaller_preserves_user_configuration_by_default():
    script = Path("installers/uninstall-integration.sh").read_text(encoding="utf-8")
    assert "REMOVE_USER_DATA=false" in script
    assert 'rm -rf "$VOCOTYPE_CONFIG_DIR"' in script
    assert '[[ "$REMOVE_USER_DATA" == true ]]' in script
    assert "用户配置已保留" in script
    assert "fcitx_user_present" in script
    assert "ibus_user_present" in script


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
