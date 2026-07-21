from __future__ import annotations

from app.voice_edit import EditEnvironment, SurroundingSnapshot, VoiceEditCore


def snapshot(text: str, cursor: int | None = None, anchor: int | None = None):
    resolved_cursor = len(text) if cursor is None else cursor
    resolved_anchor = resolved_cursor if anchor is None else anchor
    start, end = sorted((resolved_cursor, resolved_anchor))
    return SurroundingSnapshot(
        text=text,
        cursor_pos=resolved_cursor,
        anchor_pos=resolved_anchor,
        selected_text=text[start:end],
    )


def test_direct_replace_is_framework_neutral():
    core = VoiceEditCore()
    result = core.apply_direct_command(
        snapshot("今天使用旧名字。"),
        "把旧名字改成 VoCoType",
    )

    assert result.handled is True
    assert result.mode == "replace"
    assert result.new_text == "今天使用VoCoType。"
    assert result.record_history is True


def test_navigation_returns_abstract_key_actions():
    core = VoiceEditCore()
    result = core.apply_direct_command(snapshot("abcdef"), "选中下一个词")

    assert result.mode == "key_actions"
    assert len(result.key_actions) == 1
    action = result.key_actions[0]
    assert action.key == "right"
    assert action.modifiers == ("ctrl", "shift")
    assert action.repeat == 1


def test_copy_and_paste_state_is_shared_semantics():
    core = VoiceEditCore()
    selected = snapshot("alpha beta", cursor=5, anchor=0)
    copied = core.apply_direct_command(selected, "复制选中")
    assert copied.mode == "no_replace"
    assert core.voice_clipboard == "alpha"

    target = snapshot(" beta", cursor=0)
    pasted = core.apply_direct_command(target, "粘贴")
    assert pasted.new_text == "alpha beta"


def test_internal_undo_and_redo_require_matching_applied_text():
    core = VoiceEditCore()
    original = "旧文本"
    edited = "新文本"
    core.mark_voice_edit_applied(original, edited, record_history=True)

    undo = core.apply_direct_command(snapshot(edited), "撤销")
    assert undo.mode == "replace"
    assert undo.new_text == original
    assert undo.record_history is False
    core.mark_voice_edit_applied(edited, original, record_history=False)

    redo = core.apply_direct_command(snapshot(original), "重做")
    assert redo.new_text == edited
    assert redo.record_history is False


def test_undo_falls_back_to_application_key_when_state_changed():
    core = VoiceEditCore()
    core.mark_voice_edit_applied("旧", "新", record_history=True)

    result = core.apply_direct_command(snapshot("用户又改了"), "撤销")
    assert result.mode == "key_actions"
    assert result.key_actions[0].key == "z"
    assert result.key_actions[0].modifiers == ("ctrl",)


def test_generation_request_is_rewritten_for_context_editing():
    rewritten = VoiceEditCore.rewrite_insert_generation_instruction("写一段项目介绍")
    assert "生成并插入文本" in rewritten
    assert "项目介绍" in rewritten
    assert "完整输入框文本" in rewritten


def test_context_report_uses_adapter_environment():
    core = VoiceEditCore()
    result = core.apply_direct_command(
        snapshot("第一句。第二句。"),
        "显示上下文信息",
        EditEnvironment(
            supports_surrounding=True,
            active=True,
            replace_state="supported",
        ),
    )
    assert result.mode == "commit_only"
    assert "cap=1" in (result.new_text or "")
    assert "del=supported" in (result.new_text or "")
