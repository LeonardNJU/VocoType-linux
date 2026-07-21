from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import threading
import time

from fcitx5.backend.fcitx5_server import EditTask, Fcitx5Backend


ROOT = Path(__file__).resolve().parents[1]


class FakeAsr:
    def __init__(self, text: str):
        self.text = text

    def transcribe_audio(self, audio_path, *, options):
        assert Path(audio_path).exists()
        return {"success": True, "text": self.text}


class FakePolisher:
    def __init__(self, *, enabled: bool, edited_text: str = ""):
        self.enabled = enabled
        self.edit_enabled = enabled
        self.edited_text = edited_text
        self.received = None

    def edit_with_instruction(self, **kwargs):
        self.received = kwargs
        return self.edited_text, SimpleNamespace(reason="ok")

    @staticmethod
    def is_failure_reason(reason):
        return False

    @staticmethod
    def format_failure_message(reason):
        return f"failed:{reason}"


def backend(instruction: str, polisher: FakePolisher) -> Fcitx5Backend:
    value = Fcitx5Backend.__new__(Fcitx5Backend)
    value.asr_server = FakeAsr(instruction)
    value._asr_options = {}
    value._asr_lock = threading.Lock()
    value._slm_polisher = polisher
    value._voice_edit_cores = {}
    value._voice_edit_cores_lock = threading.Lock()
    value._voice_edit_run_lock = threading.Lock()
    value._edit_tasks = {}
    value._edit_tasks_lock = threading.Lock()
    return value


def request(audio: Path, text: str, cursor: int | None = None):
    position = len(text) if cursor is None else cursor
    return {
        "audio_path": str(audio),
        "context_id": "context-1",
        "replace_state": "unknown",
        "snapshot": {
            "text": text,
            "cursor_pos": position,
            "anchor_pos": position,
            "selected_text": "",
        },
    }


def test_fcitx_direct_edit_works_without_ai(tmp_path):
    audio = tmp_path / "edit.wav"
    audio.write_bytes(b"audio")
    value = backend("把旧名字改成新名字", FakePolisher(enabled=False))

    result = value._edit_audio(request(audio, "这里是旧名字。"))

    assert result["success"] is True
    assert result["mode"] == "replace"
    assert result["new_text"] == "这里是新名字。"
    assert result["expected_text"] == "这里是新名字。"


def test_fcitx_free_form_edit_uses_shared_slm_path(tmp_path):
    audio = tmp_path / "edit.wav"
    audio.write_bytes(b"audio")
    polisher = FakePolisher(enabled=True, edited_text="更正式的文本")
    value = backend("改得更正式一点", polisher)

    result = value._edit_audio(request(audio, "原始文本"))

    assert result["success"] is True
    assert result["mode"] == "replace"
    assert result["new_text"] == "更正式的文本"
    assert polisher.received["context_text"] == "原始文本"
    assert polisher.received["instruction"] == "改得更正式一点"


def test_fcitx_confirmed_edit_enables_shared_internal_undo(tmp_path):
    audio = tmp_path / "edit.wav"
    audio.write_bytes(b"audio")
    value = backend("把旧改成新", FakePolisher(enabled=False))

    first = value._edit_audio(request(audio, "旧"))
    assert first["new_text"] == "新"
    value._confirm_edit_applied(
        {
            "context_id": "context-1",
            "original_text": "旧",
            "new_text": "新",
            "record_history": True,
        }
    )

    value.asr_server.text = "撤销"
    undone = value._edit_audio(request(audio, "新"))
    assert undone["mode"] == "replace"
    assert undone["new_text"] == "旧"
    assert undone["record_history"] is False


def test_cpp_module_uses_surrounding_text_and_ctrl_f9_adapter():
    header = (ROOT / "fcitx5/module/vocotype_module.h").read_text(encoding="utf-8")
    source = (ROOT / "fcitx5/module/vocotype_module.cpp").read_text(encoding="utf-8")
    backend_source = (ROOT / "fcitx5/backend/fcitx5_server.py").read_text(
        encoding="utf-8"
    )

    assert "CapabilityFlag::SurroundingText" in source
    assert "surroundingText()" in source
    assert "deleteSurroundingText" in source
    assert "editModeForStates" in source
    assert "KeyState::Ctrl" in source
    assert "startVoiceEdit" in source
    assert "pollVoiceEditTask" in source
    assert "showVoiceEditProgress" in source
    assert '"指令：" + instruction' in source
    assert "VoiceEditSnapshot" in header
    assert "req_type == 'edit_start'" in backend_source
    assert "req_type == 'edit_poll'" in backend_source
    assert "req_type == 'edit_audio'" in backend_source
    assert "VoiceEditCore" in backend_source


def test_fcitx_ai_edit_uses_30_second_timeout():
    source = (ROOT / "fcitx5" / "common" / "ipc_client.cpp").read_text(encoding="utf-8")
    module_source = (ROOT / "fcitx5" / "module" / "vocotype_module.cpp").read_text(encoding="utf-8")
    assert "sendRequest(request.dump(), 30000)" in source
    assert "EDIT_TASK_TIMEOUT_S = 30.0" in (
        ROOT / "fcitx5" / "backend" / "fcitx5_server.py"
    ).read_text(encoding="utf-8")
    assert "std::thread([this, pid, stdin_fd, stdout_file" in module_source


def test_async_edit_exposes_instruction_before_slm_finishes(tmp_path):
    class BlockingPolisher(FakePolisher):
        def __init__(self):
            super().__init__(enabled=True, edited_text="完成结果")
            self.started = threading.Event()
            self.release = threading.Event()

        def edit_with_instruction(self, **kwargs):
            self.received = kwargs
            self.started.set()
            assert self.release.wait(timeout=2)
            return self.edited_text, SimpleNamespace(reason="ok")

    audio = tmp_path / "edit.wav"
    audio.write_bytes(b"audio")
    polisher = BlockingPolisher()
    value = backend("将定理翻译成英文", polisher)
    task = value._start_edit_task(request(audio, "勾股定理是伟大的发现。"))

    assert polisher.started.wait(timeout=2)
    running = task.snapshot()
    assert running["status"] == "running"
    assert running["phase"] == "editing"
    assert running["instruction"] == "将定理翻译成英文"

    polisher.release.set()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        final = task.snapshot()
        if final["status"] == "final":
            break
        time.sleep(0.01)
    assert final["status"] == "final"
    assert final["result"]["new_text"] == "完成结果"


def test_empty_asr_and_empty_slm_are_distinguished(tmp_path):
    audio = tmp_path / "edit.wav"
    audio.write_bytes(b"audio")

    empty_asr = backend("", FakePolisher(enabled=True, edited_text="unused"))
    asr_result = empty_asr._edit_audio(request(audio, "原文"))
    assert asr_result["success"] is False
    assert asr_result["reason"] == "empty_instruction"
    assert "未识别到编辑指令" in asr_result["error"]

    empty_slm = backend("翻译成英文", FakePolisher(enabled=True, edited_text=""))
    slm_result = empty_slm._edit_audio(request(audio, "原文"))
    assert slm_result["success"] is False
    assert slm_result["reason"] == "blank_content"
    assert slm_result["instruction"] == "翻译成英文"
    assert "没有返回编辑结果" in slm_result["error"]


def test_edit_task_times_out_after_30_seconds():
    task = EditTask(task_id="timeout")
    task.instruction = "写好评"
    task.phase = "editing"
    task.created_at -= 31
    result = task.snapshot()
    assert result["status"] == "error"
    assert result["reason"] == "timeout"
    assert result["instruction"] == "写好评"
    assert "30 秒" in result["error"]


def test_fcitx_voice_edit_uses_only_official_surrounding_text_api():
    source = (ROOT / "fcitx5/module/vocotype_module.cpp").read_text(
        encoding="utf-8"
    )
    header = (ROOT / "fcitx5/module/vocotype_module.h").read_text(
        encoding="utf-8"
    )
    assert "CapabilityFlag::SurroundingText" in source
    assert "surroundingText()" in source
    assert "deleteSurroundingText" in source
    assert "当前输入框未通过输入法接口提供上下文" in source
    assert "ClipboardCapturePurpose" not in source
    assert "ClipboardCapturePurpose" not in header
    assert "readX11ClipboardText" not in source
    assert "setSessionClipboardText" not in source
    assert "xclip" not in source
    assert "qdbus" not in source
    assert "FcitxKey_a, fcitx::KeyState::Ctrl" not in source
    assert "FcitxKey_c, fcitx::KeyState::Ctrl" not in source


def test_voice_edit_status_uses_fcitx_panel_preedit_not_candidates():
    source = (ROOT / "fcitx5/module/vocotype_module.cpp").read_text(
        encoding="utf-8"
    )
    body = source.split("void VoCoTypeModule::showVoiceEditStatusBar", 1)[1].split(
        "void VoCoTypeModule::showVoiceEditProgress", 1
    )[0]
    assert "panel.setPreedit(preedit)" in body
    assert "panel.setAuxDown(auxiliary)" in body
    assert "setCandidateList" not in body
    assert "panel.setClientPreedit" not in body


def test_voice_edit_async_callbacks_are_guarded_by_session_id():
    source = (ROOT / "fcitx5/module/vocotype_module.cpp").read_text(
        encoding="utf-8"
    )
    header = (ROOT / "fcitx5/module/vocotype_module.h").read_text(
        encoding="utf-8"
    )
    assert "active_voice_session_id_ = ++voice_session_counter_" in source
    assert "session_id != active_voice_session_id_" in source
    assert "session_id != active_voice_edit_session_id_" in source
    assert "voice_session_counter_" in header

