from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app import download_models
from app.funasr_config import get_models_for_download

ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "installers/validate-installed-integration.py"


def _write_required_models(home: Path) -> None:
    for model in get_models_for_download():
        model_path = download_models.configured_model_cache_path(model["name"], home=home)
        model_path.mkdir(parents=True, exist_ok=True)
        required, required_any = download_models.model_requirements(model)
        for name in required:
            (model_path / name).write_bytes(b"fixture")
        for group in required_any:
            (model_path / group[0]).write_bytes(b"fixture")


def test_modelscope_download_retries_without_proxy_and_restores_environment(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("https_proxy", "http://proxy.invalid:7890")
    calls: list[str | None] = []

    def fake_snapshot(_name: str, **_kwargs):
        calls.append(os.environ.get("https_proxy"))
        if len(calls) == 1:
            raise RuntimeError("proxy handshake failed")
        return "/tmp/downloaded"

    result = download_models._snapshot_download_with_direct_retry(
        fake_snapshot,
        "iic/example",
        revision="v1",
    )
    assert result == "/tmp/downloaded"
    assert calls == ["http://proxy.invalid:7890", None]
    assert os.environ["https_proxy"] == "http://proxy.invalid:7890"


def test_modelscope_download_reports_proxy_and_direct_errors(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("http_proxy", "http://proxy.invalid:7890")
    calls = 0

    def fake_snapshot(_name: str, **_kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeError("proxy failed" if calls == 1 else "direct failed")

    with pytest.raises(RuntimeError) as exc_info:
        download_models._snapshot_download_with_direct_retry(
            fake_snapshot,
            "iic/example",
            revision="v1",
        )
    message = str(exc_info.value)
    assert "当前代理尝试错误" in message
    assert "proxy failed" in message
    assert "无代理直连尝试错误" in message
    assert "direct failed" in message


def test_required_model_inspection_rejects_placeholder_cache(tmp_path: Path):
    home = tmp_path / "home"
    models = get_models_for_download()
    punc = next(item for item in models if item["type"] == "punc")
    punc_path = download_models.configured_model_cache_path(punc["name"], home=home)
    punc_path.mkdir(parents=True)
    (punc_path / ".mdl").write_bytes(b"placeholder")

    status = download_models.inspect_required_models(home=home)
    assert status["punc"]["complete"] is False
    assert "config.yaml" in status["punc"]["missing"]
    assert "tokens.json" in status["punc"]["missing"]
    assert "model_quant.onnx/model.onnx" in status["punc"]["missing"]

    _write_required_models(home)
    status = download_models.inspect_required_models(home=home)
    assert all(item["complete"] for item in status.values())


def test_download_cli_returns_nonzero_when_any_required_model_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        download_models,
        "download_all_models",
        lambda _callback=None: {
            "success": False,
            "failed_models": ["vad"],
            "results": {},
        },
    )
    assert download_models.main() == 1


def test_ibus_post_install_validator_runs_in_isolated_home(tmp_path: Path):
    home = tmp_path / "home"
    runtime = home / ".local/share/vocotype"
    (runtime / "ibus").mkdir(parents=True)
    (runtime / "ibus/main.py").write_text("main", encoding="utf-8")
    launcher = home / ".local/libexec/ibus-engine-vocotype"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    launcher.chmod(0o755)
    component = home / ".local/share/ibus/component/vocotype.xml"
    component.parent.mkdir(parents=True)
    component.write_text("<component/>", encoding="utf-8")
    _write_required_models(home)

    result = subprocess.run(
        [
            sys.executable,
            str(VALIDATOR),
            "--framework",
            "ibus",
            "--runtime-root",
            str(runtime),
        ],
        cwd=ROOT,
        env={**os.environ, "HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config")},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "安装后验收全部通过" in result.stdout


def test_installers_require_models_and_post_install_validation():
    fcitx = (ROOT / "fcitx5/scripts/install.sh").read_text(encoding="utf-8")
    ibus_gui = (ROOT / "ibus/scripts/install-gui.sh").read_text(encoding="utf-8")
    ibus_cli = (ROOT / "ibus/scripts/install.sh").read_text(encoding="utf-8")
    for source in (fcitx, ibus_gui, ibus_cli):
        assert "download_and_verify_asr_models" in source
        assert "validate-installed-integration.py" in source
    validator = (ROOT / "installers/validate-installed-integration.py").read_text(
        encoding="utf-8"
    )
    assert 'required_addon="vocotype"' in validator
    assert 'run([fcitx, "-r", "-d"]' not in validator
    assert fcitx.index("validate-installed-integration.py") < fcitx.index(
        "安装与运行验收完成"
    )
    assert ibus_gui.index("validate-installed-integration.py") < ibus_gui.index(
        "安装/修复与结构验收完成"
    )


def test_audio_installer_cannot_skip_success_and_records_verification():
    source = (ROOT / "installers/setup-audio.py").read_text(encoding="utf-8")
    assert "跳过音频配置" not in source
    assert "tested_at=datetime.now(timezone.utc).isoformat()" in source
    assert "tested_device_id=device_id" in source
    assert "save_audio_config(" in source


def test_ibus_cli_only_claims_full_success_after_audio_verification():
    source = (ROOT / "ibus/scripts/install.sh").read_text(encoding="utf-8")
    assert "AUDIO_VERIFIED=false" in source
    assert "AUDIO_VERIFIED=true" in source
    assert "程序与结构已就绪；麦克风验收尚未完成" in source
    assert "安装与麦克风验收完成" in source


def test_make_clean_removes_python_build_metadata():
    source = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "*.egg-info" in source
    assert "-name __pycache__" in source
    assert "-name '*.pyc'" in source


def test_gui_installers_emit_determinate_progress_and_share_runtime_helpers():
    fcitx = (ROOT / "fcitx5/scripts/install.sh").read_text(encoding="utf-8")
    ibus = (ROOT / "ibus/scripts/install-gui.sh").read_text(encoding="utf-8")
    common = (ROOT / "installers/runtime-common.sh").read_text(encoding="utf-8")
    application = (ROOT / "settings_center/application.py").read_text(encoding="utf-8")

    assert "emit_install_progress()" in common
    assert 'source "$PROJECT_DIR/installers/runtime-common.sh"' in ibus
    for source in (fcitx, ibus):
        stages = [
            int(line.split()[1])
            for line in source.splitlines()
            if line.strip().startswith("emit_install_progress ")
        ]
        assert stages == sorted(stages)
        assert stages[0] <= 2
        assert stages[-1] == 96
        assert len(stages) >= 8

    assert "progress_bar = Gtk.ProgressBar()" in application
    assert "parse_install_progress(line.strip())" in application
    assert 'progress_bar.set_text("❌ 安装失败")' in application
    assert 'progress_bar.set_text("⚠️ 96%")' in application
    assert 'progress_bar.set_text("✅ 100%")' in application
