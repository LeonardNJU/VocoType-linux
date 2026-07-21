"""Framework-neutral validation for SLM-generated voice-edit plans.

The language model owns intent understanding. IBus and Fcitx adapters only
capture surrounding text and execute a small, validated plan. There are no
hard-coded natural-language command patterns in this module.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping


ALLOWED_PLAN_MODES = frozenset({"replace", "key_actions", "no_op"})
ALLOWED_KEYS = frozenset(
    {
        "left",
        "right",
        "up",
        "down",
        "home",
        "end",
        "pageup",
        "pagedown",
        "backspace",
        "delete",
        "enter",
        "tab",
        "escape",
        "space",
        "a",
        "c",
        "v",
        "x",
        "z",
    }
)
ALLOWED_MODIFIERS = frozenset({"ctrl", "shift", "alt", "super"})
MAX_KEY_REPEAT = 100


def _safe_string(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


@dataclass(frozen=True)
class SurroundingSnapshot:
    text: str
    cursor_pos: int
    anchor_pos: int
    selected_text: str = ""

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | Any) -> "SurroundingSnapshot":
        mapping = value if isinstance(value, Mapping) else {}
        text = _safe_string(mapping.get("text"))
        cursor = max(0, min(_safe_int(mapping.get("cursor_pos")), len(text)))
        anchor = max(
            0,
            min(_safe_int(mapping.get("anchor_pos"), cursor), len(text)),
        )
        selected = _safe_string(mapping.get("selected_text"))
        if not selected and cursor != anchor:
            start, end = sorted((cursor, anchor))
            selected = text[start:end]
        return cls(
            text=text,
            cursor_pos=cursor,
            anchor_pos=anchor,
            selected_text=selected,
        )


@dataclass(frozen=True)
class KeyAction:
    key: str
    modifiers: tuple[str, ...] = ()
    repeat: int = 1

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | Any) -> "KeyAction":
        if not isinstance(value, Mapping):
            raise VoiceEditPlanError("key_actions 中的每一项必须是对象")
        key = _safe_string(value.get("key")).strip().lower().replace("_", "")
        aliases = {
            "return": "enter",
            "pgup": "pageup",
            "pgdn": "pagedown",
            "page-up": "pageup",
            "page-down": "pagedown",
        }
        key = aliases.get(key, key)
        if key not in ALLOWED_KEYS:
            raise VoiceEditPlanError(f"不允许的按键动作：{key or '(空)'}")

        raw_modifiers = value.get("modifiers", ())
        if raw_modifiers is None:
            raw_modifiers = ()
        if not isinstance(raw_modifiers, (list, tuple)):
            raise VoiceEditPlanError("modifiers 必须是字符串数组")
        modifiers: list[str] = []
        for item in raw_modifiers:
            modifier = _safe_string(item).strip().lower()
            if modifier not in ALLOWED_MODIFIERS:
                raise VoiceEditPlanError(
                    f"不允许的修饰键：{modifier or '(空)'}"
                )
            if modifier not in modifiers:
                modifiers.append(modifier)

        repeat = max(1, min(MAX_KEY_REPEAT, _safe_int(value.get("repeat"), 1)))
        return cls(key=key, modifiers=tuple(modifiers), repeat=repeat)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "modifiers": list(self.modifiers),
            "repeat": max(1, min(MAX_KEY_REPEAT, int(self.repeat))),
        }


class VoiceEditPlanError(ValueError):
    """The model returned a malformed or unsafe edit plan."""


@dataclass(frozen=True)
class VoiceEditPlan:
    mode: str
    new_text: str = ""
    record_history: bool = True
    hint: str = ""
    key_actions: tuple[KeyAction, ...] = ()
    reason: str = ""

    @classmethod
    def from_model_output(
        cls,
        output: str | Mapping[str, Any],
        *,
        original_text: str = "",
    ) -> "VoiceEditPlan":
        value = _decode_plan_object(output)
        raw_mode = _safe_string(value.get("mode") or value.get("action"))
        mode = raw_mode.strip().lower().replace("-", "_")
        aliases = {
            "replace_text": "replace",
            "text": "replace",
            "keys": "key_actions",
            "navigate": "key_actions",
            "navigation": "key_actions",
            "noop": "no_op",
            "none": "no_op",
        }
        mode = aliases.get(mode, mode)
        if mode not in ALLOWED_PLAN_MODES:
            raise VoiceEditPlanError(
                f"未知编辑计划模式：{raw_mode or '(空)'}"
            )

        hint = _safe_string(value.get("hint"))
        reason = _safe_string(value.get("reason"))
        record_history = _safe_bool(
            value.get("record_history"),
            default=(mode == "replace"),
        )

        if mode == "replace":
            raw_text = value.get("new_text", value.get("text"))
            if not isinstance(raw_text, str):
                raise VoiceEditPlanError("replace 计划必须返回字符串 new_text")
            return cls(
                mode="replace",
                new_text=raw_text,
                record_history=record_history,
                hint=hint,
                reason=reason,
            )

        if mode == "key_actions":
            raw_actions = value.get("key_actions", value.get("actions"))
            if not isinstance(raw_actions, list) or not raw_actions:
                raise VoiceEditPlanError(
                    "key_actions 计划必须包含非空动作数组"
                )
            if len(raw_actions) > 32:
                raise VoiceEditPlanError("key_actions 动作数量超过安全上限 32")
            actions = tuple(KeyAction.from_mapping(item) for item in raw_actions)
            return cls(
                mode="key_actions",
                new_text="",
                record_history=False,
                hint=hint,
                key_actions=actions,
                reason=reason,
            )

        # no_op never changes text and never records history. Null fields from
        # model providers are deliberately normalized instead of crossing IPC.
        return cls(
            mode="no_op",
            new_text=original_text,
            record_history=False,
            hint=hint,
            reason=reason,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "handled": True,
            "mode": self.mode,
            "new_text": self.new_text,
            "record_history": bool(self.record_history),
            "hint": self.hint,
            "key_actions": [action.to_dict() for action in self.key_actions],
            "reason": self.reason,
        }


def _decode_plan_object(output: str | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(output, Mapping):
        return output
    if not isinstance(output, str):
        raise VoiceEditPlanError("模型编辑计划必须是 JSON 对象")

    text = output.strip()
    if not text:
        raise VoiceEditPlanError("模型返回了空编辑计划")
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].lstrip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        if start < 0:
            raise VoiceEditPlanError("模型未返回 JSON 对象") from None
        try:
            parsed, _ = json.JSONDecoder().raw_decode(text[start:])
        except json.JSONDecodeError as exc:
            raise VoiceEditPlanError(f"编辑计划 JSON 无效：{exc.msg}") from None

    if not isinstance(parsed, Mapping):
        raise VoiceEditPlanError("模型编辑计划顶层必须是对象")
    return parsed
