from __future__ import annotations

from pathlib import Path

import pytest

from app.voice_edit import (
    KeyAction,
    SurroundingSnapshot,
    VoiceEditPlan,
    VoiceEditPlanError,
)


def test_snapshot_normalizes_null_and_derives_selection():
    snapshot = SurroundingSnapshot.from_mapping(
        {
            "text": "alpha beta",
            "cursor_pos": 5,
            "anchor_pos": 0,
            "selected_text": None,
        }
    )
    assert snapshot.text == "alpha beta"
    assert snapshot.selected_text == "alpha"


def test_replace_plan_accepts_complete_context_text():
    plan = VoiceEditPlan.from_model_output(
        {
            "mode": "replace",
            "new_text": "请使用 VoCoType。",
            "record_history": True,
            "hint": "已按上下文修正同音词",
        },
        original_text="请使用窝口太普。",
    )
    assert plan.mode == "replace"
    assert plan.new_text == "请使用 VoCoType。"
    assert plan.record_history is True
    assert plan.to_dict()["new_text"] == "请使用 VoCoType。"


def test_key_action_plan_normalizes_null_text_and_clamps_repeat():
    plan = VoiceEditPlan.from_model_output(
        {
            "mode": "key_actions",
            "new_text": None,
            "hint": None,
            "key_actions": [
                {"key": "left", "modifiers": ["ctrl"], "repeat": 999}
            ],
        },
        original_text="上下文",
    )
    assert plan.mode == "key_actions"
    assert plan.new_text == ""
    assert plan.hint == ""
    assert plan.key_actions == (KeyAction("left", ("ctrl",), 100),)
    payload = plan.to_dict()
    assert payload["new_text"] == ""
    assert payload["key_actions"][0]["repeat"] == 100


def test_undo_and_navigation_are_ordinary_model_key_plans():
    undo = VoiceEditPlan.from_model_output(
        {
            "mode": "key_actions",
            "key_actions": [{"key": "z", "modifiers": ["ctrl"], "repeat": 1}],
        }
    )
    previous_word = VoiceEditPlan.from_model_output(
        {
            "mode": "key_actions",
            "key_actions": [
                {"key": "left", "modifiers": ["ctrl"], "repeat": 1}
            ],
        }
    )
    sentence_start = VoiceEditPlan.from_model_output(
        {
            "mode": "key_actions",
            "key_actions": [{"key": "home", "modifiers": [], "repeat": 1}],
        }
    )
    assert undo.key_actions[0] == KeyAction("z", ("ctrl",), 1)
    assert previous_word.key_actions[0] == KeyAction("left", ("ctrl",), 1)
    assert sentence_start.key_actions[0] == KeyAction("home", (), 1)


def test_no_op_normalizes_all_optional_null_fields():
    plan = VoiceEditPlan.from_model_output(
        {"mode": "no_op", "new_text": None, "hint": None, "reason": None},
        original_text="保持原文",
    )
    assert plan.mode == "no_op"
    assert plan.new_text == "保持原文"
    assert plan.hint == ""
    assert plan.reason == ""
    assert plan.record_history is False


def test_fenced_or_prefixed_json_is_extracted():
    fenced = VoiceEditPlan.from_model_output(
        '```json\n{"mode":"replace","new_text":"结果"}\n```'
    )
    prefixed = VoiceEditPlan.from_model_output(
        '计划如下：{"mode":"no_op","hint":"无需修改"} trailing'
    )
    assert fenced.new_text == "结果"
    assert prefixed.mode == "no_op"
    assert prefixed.hint == "无需修改"


@pytest.mark.parametrize(
    "payload",
    [
        {"mode": "replace", "new_text": None},
        {"mode": "key_actions", "key_actions": []},
        {
            "mode": "key_actions",
            "key_actions": [{"key": "shell", "modifiers": []}],
        },
        {
            "mode": "key_actions",
            "key_actions": [{"key": "left", "modifiers": ["root"]}],
        },
        {"mode": "unknown"},
        {
            "mode": "key_actions",
            "key_actions": [
                {"key": "left", "modifiers": [], "repeat": 1}
                for _ in range(33)
            ],
        },
    ],
)
def test_invalid_or_unsafe_plans_are_rejected(payload):
    with pytest.raises(VoiceEditPlanError):
        VoiceEditPlan.from_model_output(payload)


def test_shared_module_contains_no_natural_language_command_parser():
    source = (Path(__file__).resolve().parents[1] / "app/voice_edit.py").read_text(
        encoding="utf-8"
    )
    assert "apply_direct_command" not in source
    assert "normalize_command" not in source
    assert "rewrite_insert_generation_instruction" not in source
    assert "re.match" not in source
    assert "Framework-neutral validation for SLM-generated" in source
