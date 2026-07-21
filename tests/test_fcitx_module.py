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


def test_panel_animation_scheduler_has_single_owner_and_generation_guard():
    source = (ROOT / "fcitx5/module/vocotype_module.cpp").read_text(encoding="utf-8")
    header = (ROOT / "fcitx5/module/vocotype_module.h").read_text(encoding="utf-8")

    assert "schedulePanelAnimationFrame" in source
    assert "panel_animation_generation_" in source
    assert "panel_animation_generation_" in header
    assert "std::make_shared<std::function<void()>>" not in source
    assert "schedule_next" not in source
    assert "generation != panel_animation_generation_" in source
    assert "++panel_animation_generation_;" in source


def test_panel_style_defaults_to_minimal_and_release_switches_immediately():
    header = (ROOT / "fcitx5/module/vocotype_module.h").read_text(encoding="utf-8")
    source = (ROOT / "fcitx5/module/vocotype_module.cpp").read_text(encoding="utf-8")
    assert '"PanelStyle"' in header
    assert '"minimal"' in header
    assert 'animate_panel_ = false' in header
    assert 'animate_panel_ = toLower(config_.panelStyle.value()) == "animated"' in source
    assert 'long_mode ? "🎤 录音中(长句)..." : "🎤 录音中..."' in source
    assert "renderRecordingPanel(ic, recording_status_text_)" in source
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
    for status in (
        "🎤 录音中...",
        "🟢 正在听 ●     ",
        "⚫ 正在听     ● ",
        "⏳ 识别中",
    ):
        assert status in source
        assert status in ibus
    assert "panel.setAuxDown(preview);" in source
    assert "self._update_auxiliary_status(self._streaming_preview_text)" in ibus


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


def test_live_asr_partials_replace_panel_preedit_but_never_commit():
    source = (ROOT / "fcitx5" / "module" / "vocotype_module.cpp").read_text(
        encoding="utf-8"
    )
    render_body = source.split(
        "void VoCoTypeModule::renderRecordingPanel", 1
    )[1].split("void VoCoTypeModule::showStreamingPreview", 1)[0]
    preview_body = source.split(
        "void VoCoTypeModule::showStreamingPreview", 1
    )[1].split("void VoCoTypeModule::showAnimationFrame", 1)[0]
    assert 'type == "partial"' in source
    assert "panel.setPreedit(status_text);" in render_body
    assert "panel.setAuxDown(preview);" in render_body
    assert "streaming_preview_text_ = text;" in preview_body
    assert "stopPanelAnimation();" not in preview_body
    assert "ic->updatePreedit();" not in render_body
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
    release = source.split("void VoCoTypeModule::armPendingPttRelease", 1)[1].split(
        "void VoCoTypeModule::cancelPendingPttRelease", 1
    )[0]
    assert "else if (ptt_suppressed_)" in release
    finish = source.split("std::string finishRecorderProcess", 1)[1].split(
        "bool copyTextToWaylandClipboard", 1
    )[0]
    assert "close(lock_fd);" in finish


def test_fcitx_rejects_short_recordings_before_preview_or_final_asr():
    header = (ROOT / "fcitx5/module/vocotype_module.h").read_text(encoding="utf-8")
    source = (ROOT / "fcitx5/module/vocotype_module.cpp").read_text(encoding="utf-8")
    assert '"MinRecordingMs"' in header
    assert '"最短有效录音时长（毫秒）"' in header
    assert "int min_recording_ms_ = 1000;" in header
    stop_body = source.split("void VoCoTypeModule::stopRecording(bool transcribe)", 1)[1]
    stop_prefix = stop_body.split("std::thread", 1)[0]
    assert "const bool recording_too_short" in stop_prefix
    assert "transcribe = false;" in stop_prefix
    assert "showTemporaryMessage(" in stop_prefix
    assert "录音过短（至少 " in stop_prefix
    assert stop_prefix.index("recording_too_short") < stop_prefix.index('showPanelMessage(ic, "⏳ 识别中")')
    preview_body = source.split("void VoCoTypeModule::showStreamingPreview", 1)[1].split(
        "void VoCoTypeModule::showAnimationFrame", 1
    )[0]
    assert "min_recording_ms_ > 0" in preview_body
    assert "recording_started_us_" in preview_body
    assert "return;" in preview_body
