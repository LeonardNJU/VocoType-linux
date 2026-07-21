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
    assert "EventWatcherPhase::PostInputMethod" in source
    assert "EventWatcherPhase::ReservedFirst" not in source
    assert "EventWatcherPhase::ReservedLast" not in source
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
        ROOT / "fcitx5" / "scripts" / "install.sh"
    ).read_text(encoding="utf-8")
    assert '"$PROJECT_DIR/fcitx5/module/build"' in installer
    assert 'rm -f "$HOME/.local/share/fcitx5/inputmethod/vocotype.conf"' in installer
    assert "uv pip install pyrime" not in installer
    assert '"$PYTHON" -m pip install pyrime' not in installer

def test_dead_fcitx_rime_handler_is_removed():
    source = (ROOT / "fcitx5" / "backend" / "fcitx5_server.py").read_text(encoding="utf-8")
    assert "rime_handler" not in source
    assert not (ROOT / "fcitx5" / "backend" / "rime_handler.py").exists()


def test_ptt_release_stops_listening_immediately_without_debounce():
    source = (ROOT / "fcitx5/module/vocotype_module.cpp").read_text(encoding="utf-8")
    header = (ROOT / "fcitx5/module/vocotype_module.h").read_text(encoding="utf-8")
    assert "PTT_RELEASE_DEBOUNCE_US" not in source
    assert "armPendingRecordingStop" not in source
    assert "ptt_release_timer_" not in header
    assert "} else if (is_recording_) {\n        stopAndTranscribe();" in source
    stop_body = source.split("void VoCoTypeModule::stopRecording(bool transcribe)", 1)[1]
    assert stop_body.index("stopPanelAnimation();") < stop_body.index("is_recording_ = false;")


def test_panel_style_defaults_to_minimal_and_release_switches_immediately():
    header = (ROOT / "fcitx5/module/vocotype_module.h").read_text(encoding="utf-8")
    source = (ROOT / "fcitx5/module/vocotype_module.cpp").read_text(encoding="utf-8")
    assert '"PanelStyle"' in header
    assert '"minimal"' in header
    assert 'animate_panel_ = false' in header
    assert 'animate_panel_ = toLower(config_.panelStyle.value()) == "animated"' in source
    assert 'showPanelMessage(ic, "🎤 录音中...")' in source
    stop_body = source.split("void VoCoTypeModule::stopRecording(bool transcribe)", 1)[1]
    assert 'showPanelMessage(ic, "⏳ 识别中")' in stop_body
    assert stop_body.index("stopPanelAnimation();") < stop_body.index('showPanelMessage(ic, "⏳ 识别中")')
    release_prefix = stop_body.split("std::thread", 1)[0]
    assert "PanelAnimationKind::Polishing" not in release_prefix


def test_f9_and_shift_f9_contract_matches_ibus():
    header = (ROOT / "fcitx5/module/vocotype_module.h").read_text(encoding="utf-8")
    source = (ROOT / "fcitx5/module/vocotype_module.cpp").read_text(encoding="utf-8")
    ibus = (ROOT / "ibus/engine.py").read_text(encoding="utf-8")

    assert "PolishByDefault" not in header
    assert "polish_by_default_" not in source
    assert "return static_cast<bool>(states & long_mode_modifier_);" in source
    assert "long_mode = bool(state & IBus.ModifierType.SHIFT_MASK)" in ibus
