from app.ibus_compat import build_capability_flags


def test_build_capability_flags_supports_ibus_1_5_26_subset():
    class OldCapabilite:
        PREEDIT_TEXT = 1
        AUXILIARY_TEXT = 2
        LOOKUP_TABLE = 4
        FOCUS = 8
        PROPERTY = 16
        SURROUNDING_TEXT = 32

    assert build_capability_flags(OldCapabilite) == (
        (1, "preedit"),
        (2, "aux"),
        (4, "lookup"),
        (8, "focus"),
        (16, "property"),
        (32, "surrounding"),
    )


def test_build_capability_flags_includes_newer_optional_flags():
    class NewCapabilite:
        PREEDIT_TEXT = 1
        OSK = 64
        SYNC_PROCESS_KEY = 128

    assert build_capability_flags(NewCapabilite) == (
        (1, "preedit"),
        (64, "osk"),
        (128, "sync_key"),
    )


def test_ibus_online_preview_is_preedit_only_and_offline_asr_remains_final():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1] / "ibus" / "engine.py"
    ).read_text(encoding="utf-8")
    preview_body = source.split("    def _render_streaming_preview", 1)[1].split(
        "    def _reload_runtime_config", 1
    )[0]
    final_body = source.split("    def _stop_and_transcribe", 1)[1].split(
        "    def _update_preedit", 1
    )[0]
    assert "self._streaming_preview_text = text" in preview_body
    assert "self._render_recording_status()" in preview_body
    status_body = source.split("    def _render_recording_status", 1)[1].split(
        "    def _advance_recording_animation", 1
    )[0]
    assert "self._update_preedit(self._recording_status_text())" in status_body
    assert "self._update_auxiliary_status(self._streaming_preview_text)" in status_body
    assert "commit_text" not in preview_body
    assert "self._run_native_core_pipeline(" in final_body
    assert "client.transcribe(temp_path" in source
    assert "client.start_transcription(" in source
    assert "client.start_edit(" in source
    assert "asr_server.transcribe_audio(" in final_body  # explicit Python fallback
    assert "audio_data = np.concatenate(self._audio_frames)" in final_body


def test_ibus_release_does_not_wait_for_online_tail_flush():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1] / "ibus" / "engine.py"
    ).read_text(encoding="utf-8")
    stop_body = source.split("    def _stop_streaming_preview", 1)[1].split(
        "    def _render_streaming_preview", 1
    )[0]
    assert "thread.join(timeout=0.1)" in stop_body
    assert "flush=True" not in stop_body


def test_ibus_rejects_short_recordings_and_hides_early_streaming_partials():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "ibus/engine.py").read_text(
        encoding="utf-8"
    )
    assert "self._min_recording_ms = 1000" in source
    assert 'audio.get("min_recording_ms", 1000)' in source
    assert 'self._asr_options["min_audio_seconds"]' in source
    final_body = source.split("    def _stop_and_transcribe", 1)[1].split(
        "    def _update_preedit", 1
    )[0]
    assert "duration * 1000.0 < self._min_recording_ms" in final_body
    assert "录音过短（至少 {self._min_recording_ms} ms）" in final_body
    preview_body = source.split("    def _render_streaming_preview", 1)[1].split(
        "    def _reload_runtime_config", 1
    )[0]
    assert "eligible = (" in preview_body
    assert "time.monotonic() - self._recording_started_at" in preview_body
    assert "and eligible" in preview_body


def test_ibus_slm_key_plans_cover_navigation_and_recheck_snapshot():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "ibus" / "engine.py").read_text(
        encoding="utf-8"
    )
    assert '"pageup": IBus.KEY_Page_Up' in source
    assert '"backspace": IBus.KEY_BackSpace' in source
    assert '"c": IBus.KEY_c' in source
    assert '"v": IBus.KEY_v' in source
    assert 'snapshot: Optional[SurroundingSnapshot] = None' in source
    assert 'live_text != snapshot.text' in source
    assert 'int(live_cursor) != int(snapshot.cursor_pos)' in source
    assert 'int(live_anchor) != int(snapshot.anchor_pos)' in source
    plan_call = source.split("if plan.mode == \"key_actions\":", 1)[1].split("return", 1)[0]
    assert "plan.key_actions" in plan_call
    assert "plan.hint" in plan_call
    assert "edit_snapshot" in plan_call


def test_ibus_prefers_native_core_and_retains_explicit_python_fallback():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "ibus/engine.py").read_text(
        encoding="utf-8"
    )
    client = (
        Path(__file__).resolve().parents[1] / "app/native_core_client.py"
    ).read_text(encoding="utf-8")
    assert "NativeCoreClient.should_use_native" in source
    assert "VOCOTYPE_BACKEND" in client
    assert 'return "python"' in client
    assert 'return "auto"' in client
    assert "client.ensure_running()" in source
    assert "model = self._native_core" in source
    assert "NativeCoreClient.close_all()" in source
    assert "StreamingASRProcess(cfg)" in source  # legacy fallback remains available
