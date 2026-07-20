from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_fcitx_addon_is_global_module():
    metadata = (ROOT / "fcitx5" / "data" / "vocotype.conf").read_text(
        encoding="utf-8"
    )
    assert "Category=Module" in metadata
    assert "OnDemand=False" in metadata
    assert "Category=InputMethod" not in metadata


def test_module_intercepts_ptt_without_proxying_rime():
    source = (ROOT / "fcitx5" / "module" / "vocotype_module.cpp").read_text(
        encoding="utf-8"
    )
    assert "EventWatcherPhase::PreInputMethod" in source
    assert "InputContextKeyEvent" in source
    assert "commitString" in source
    assert "processKey" not in source
    assert "RimeUIState" not in source


def test_fcitx_backend_no_longer_embeds_rime():
    source = (ROOT / "fcitx5" / "backend" / "fcitx5_server.py").read_text(
        encoding="utf-8"
    )
    assert "RimeHandler" not in source
    assert "rime_handler" not in source
    assert "req_type == 'key_event'" not in source


def test_installer_builds_module_and_removes_legacy_input_method():
    installer = (
        ROOT / "fcitx5" / "scripts" / "install-fcitx5.sh"
    ).read_text(encoding="utf-8")
    assert '"$PROJECT_DIR/fcitx5/module/build"' in installer
    assert 'rm -f "$HOME/.local/share/fcitx5/inputmethod/vocotype.conf"' in installer
    assert "uv pip install pyrime" not in installer
    assert '"$PYTHON" -m pip install pyrime' not in installer
