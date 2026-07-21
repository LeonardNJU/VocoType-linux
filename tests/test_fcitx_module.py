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


def test_ptt_release_ignores_repeat_release_press_pairs():
    source = (ROOT / "fcitx5/module/vocotype_module.cpp").read_text(encoding="utf-8")
    header = (ROOT / "fcitx5/module/vocotype_module.h").read_text(encoding="utf-8")
    assert "PTT_RELEASE_SETTLE_US" in source
    assert "armPendingRecordingStop();" in source
    assert "cancelPendingRecordingStop();" in source
    assert "KeyState::Repeat" in source
    assert "ptt_release_timer_" in header
    key_body = source.split("void VoCoTypeModule::handleKeyEvent", 1)[1].split(
        "void VoCoTypeModule::handleFocusOut", 1
    )[0]
    assert key_body.index("cancelPendingRecordingStop();") < key_body.index(
        "armPendingRecordingStop();"
    )


def test_live_asr_partials_replace_panel_preedit_but_never_commit():
    source = (ROOT / "fcitx5" / "module" / "vocotype_module.cpp").read_text(
        encoding="utf-8"
    )
    preview_body = source.split(
        "void VoCoTypeModule::showStreamingPreview", 1
    )[1].split("void VoCoTypeModule::showAnimationFrame", 1)[0]
    assert 'type == "partial"' in source
    assert "panel.setPreedit(preview);" in preview_body
    assert "streaming_preview_visible_" in preview_body
    assert preview_body.count("panel.reset();") == 1
    assert "ic->updatePreedit();" not in preview_body
    assert "commitString" not in preview_body
    assert 'type == "audio"' in source
    assert "transcribeAudio(audio_path, false)" in source


def test_fcitx_recording_is_process_singleton_and_duplicate_release_is_consumed():
    source = (ROOT / "fcitx5/module/vocotype_module.cpp").read_text(encoding="utf-8")
    header = (ROOT / "fcitx5/module/vocotype_module.h").read_text(encoding="utf-8")
    assert "flock(fd, LOCK_EX | LOCK_NB)" in source
    assert "recorder_lock_fd_" in header
    assert "ptt_suppressed_" in header
    assert "Suppressed duplicate VoCoType recording start" in source
    assert "} else if (ptt_suppressed_) {\n        cancelPendingRecordingStart();" in source
    finish = source.split("std::string finishRecorderProcess", 1)[1].split("bool copyTextToWaylandClipboard", 1)[0]
    assert "close(lock_fd);" in finish
