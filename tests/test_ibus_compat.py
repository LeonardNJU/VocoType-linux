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
    assert "asr_server.transcribe_audio(" in final_body
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
