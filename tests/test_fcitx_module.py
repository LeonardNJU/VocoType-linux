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


def test_ptt_release_filters_x11_autorepeat_without_cutting_recording():
    source = (ROOT / "fcitx5/module/vocotype_module.cpp").read_text(encoding="utf-8")
    header = (ROOT / "fcitx5/module/vocotype_module.h").read_text(encoding="utf-8")
    assert "PTT_AUTOREPEAT_RELEASE_GRACE_US = 30000" in source
    assert "event.rawKey().states().test(fcitx::KeyState::Repeat)" in source
    assert "if (ptt_release_timer_)" in source
    assert "cancelPendingPttRelease();" in source
    assert "armPendingPttRelease(ic);" in source
    assert "ptt_release_timer_" in header
    stop_body = source.split("void VoCoTypeModule::stopRecording(bool transcribe)", 1)[1]
    assert stop_body.index("stopPanelAnimation();") < stop_body.index("is_recording_ = false;")


def test_voice_edit_module_contains_no_clipboard_context_capture():
    source = (ROOT / "fcitx5/module/vocotype_module.cpp").read_text(encoding="utf-8")
    header = (ROOT / "fcitx5/module/vocotype_module.h").read_text(encoding="utf-8")
    assert "ClipboardCapturePurpose" not in source
    assert "ClipboardCapturePurpose" not in header
    assert "canUseClipboardFallback" not in source
    assert "beginClipboardCapture" not in source


def test_voice_edit_status_uses_panel_preedit_not_candidate_rows():
    source = (ROOT / "fcitx5/module/vocotype_module.cpp").read_text(encoding="utf-8")
    status = source.split(
        "void VoCoTypeModule::showVoiceEditStatusBar", 1
    )[1].split("void VoCoTypeModule::showVoiceEditProgress", 1)[0]
    assert "panel.setPreedit(preedit)" in status
    assert "panel.setAuxDown(auxiliary)" in status
    assert "setCandidateList" not in status
