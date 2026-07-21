"""Framework-neutral voice-edit command semantics.

IBus and Fcitx adapters are responsible only for capturing surrounding text and
executing the returned text/key actions. This module owns command parsing,
clipboard/history state, and generation-instruction rewriting.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Mapping


@dataclass(frozen=True)
class SurroundingSnapshot:
    text: str
    cursor_pos: int
    anchor_pos: int
    selected_text: str = ""

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "SurroundingSnapshot":
        text = str(value.get("text", ""))
        cursor = max(0, min(int(value.get("cursor_pos", 0)), len(text)))
        anchor = max(0, min(int(value.get("anchor_pos", cursor)), len(text)))
        selected = str(value.get("selected_text", ""))
        if not selected and cursor != anchor:
            start, end = sorted((cursor, anchor))
            selected = text[start:end]
        return cls(text=text, cursor_pos=cursor, anchor_pos=anchor, selected_text=selected)


@dataclass(frozen=True)
class KeyAction:
    key: str
    modifiers: tuple[str, ...] = ()
    repeat: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "modifiers": list(self.modifiers),
            "repeat": max(1, min(20, int(self.repeat))),
        }


@dataclass
class DirectEditResult:
    handled: bool
    new_text: str | None = None
    record_history: bool = True
    hint: str = ""
    mode: str = "replace"  # replace / key_actions / no_replace / commit_only
    key_actions: tuple[KeyAction, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "handled": self.handled,
            "new_text": self.new_text,
            "record_history": self.record_history,
            "hint": self.hint,
            "mode": self.mode,
            "key_actions": [item.to_dict() for item in self.key_actions],
        }


@dataclass(frozen=True)
class EditEnvironment:
    supports_surrounding: bool = True
    active: bool = True
    replace_state: str = "unknown"


@dataclass
class VoiceEditCore:
    history_limit: int = 20
    undo_stack: list[str] = field(default_factory=list)
    redo_stack: list[str] = field(default_factory=list)
    voice_clipboard: str = ""
    last_text_change_source: str = "none"
    last_internal_edit_text: str | None = None

    _PUNCTUATION_MAP = {
        "句号": "。",
        "逗号": "，",
        "问号": "？",
        "感叹号": "！",
        "冒号": "：",
        "分号": "；",
        "引号": "“”",
    }

    @staticmethod
    def normalize_command(command: str) -> str:
        cmd = " ".join((command or "").strip().split())
        if not cmd:
            return ""
        cmd = re.sub(r"^(?:请|麻烦|帮我|帮忙)\s*", "", cmd)
        cmd = re.sub(r"(一下子?|吧)$", "", cmd)
        cmd = re.sub(r"[。！？!?，,；;：:]+$", "", cmd)
        return cmd.strip()

    @staticmethod
    def strip_command_quotes(text: str) -> str:
        return str(text or "").strip().strip("“”\"'")

    @staticmethod
    def parse_count(command: str) -> int:
        digit_match = re.search(r"(\d+)", command)
        if digit_match:
            return max(1, min(20, int(digit_match.group(1))))
        cn_map = {
            "一": 1,
            "二": 2,
            "两": 2,
            "三": 3,
            "四": 4,
            "五": 5,
            "六": 6,
            "七": 7,
            "八": 8,
            "九": 9,
            "十": 10,
        }
        for char, value in cn_map.items():
            if char in command:
                return value
        return 1

    @staticmethod
    def rewrite_insert_generation_instruction(command: str) -> str:
        cmd = VoiceEditCore.normalize_command(command)
        if not cmd:
            return ""
        match = re.match(r"^(?:输入|写|写一段|生成|生成一段|来一段)\s*(.+)\s*$", cmd)
        if not match:
            return ""
        request = VoiceEditCore.strip_command_quotes(match.group(1))
        if not request:
            return ""
        return (
            "请按以下要求生成并插入文本："
            f"{request}。"
            "将生成结果插入到当前光标位置；如果当前有选中文本，则替换选中内容。"
            "除插入/替换位置外，不要改动任何其他文本。"
            "只输出编辑后的完整输入框文本。"
        )

    @staticmethod
    def predict_commit_result(snapshot: SurroundingSnapshot, payload: str) -> str:
        text = snapshot.text or ""
        cursor = max(0, min(int(snapshot.cursor_pos), len(text)))
        anchor = max(0, min(int(snapshot.anchor_pos), len(text)))
        start, end = sorted((cursor, anchor))
        if end > start:
            return text[:start] + payload + text[end:]
        return text[:cursor] + payload + text[cursor:]

    @staticmethod
    def _sentence_spans(text: str) -> list[tuple[int, int]]:
        if not text:
            return []
        delimiters = set("。！？!?；;.\n")
        spans: list[tuple[int, int]] = []
        start = 0
        for index, char in enumerate(text):
            if char in delimiters:
                end = index + 1
                if end > start:
                    spans.append((start, end))
                start = end
        if start < len(text):
            spans.append((start, len(text)))
        return spans

    @staticmethod
    def _locate_sentence_index(spans: list[tuple[int, int]], cursor_pos: int) -> int:
        if not spans:
            return -1
        cursor = max(0, cursor_pos)
        for index, (start, end) in enumerate(spans):
            if start <= cursor <= end:
                return index
        return len(spans) - 1

    @staticmethod
    def _clip(text: str, limit: int = 48) -> str:
        compact = str(text or "").replace("\n", "\\n")
        if len(compact) <= limit:
            return compact
        return compact[: max(0, limit - 1)] + "…"

    @classmethod
    def _sentence_window(cls, text: str, cursor_pos: int) -> tuple[str, str]:
        spans = cls._sentence_spans(text)
        index = cls._locate_sentence_index(spans, cursor_pos)
        if index < 0:
            return "", ""
        start, end = spans[index]
        current = text[start:end]
        previous = ""
        if index > 0:
            p_start, p_end = spans[index - 1]
            previous = text[p_start:p_end]
        return current, previous

    @staticmethod
    def _key(key: str, *modifiers: str, repeat: int = 1) -> tuple[KeyAction, ...]:
        return (KeyAction(key=key, modifiers=tuple(modifiers), repeat=repeat),)

    def push_undo_state(self, text: str) -> None:
        if self.undo_stack and self.undo_stack[-1] == text:
            return
        self.undo_stack.append(text)
        if len(self.undo_stack) > self.history_limit:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    def mark_voice_edit_applied(
        self,
        original_text: str,
        new_text: str,
        *,
        record_history: bool = True,
    ) -> None:
        if record_history:
            self.push_undo_state(original_text)
        self.last_text_change_source = "voice_edit"
        self.last_internal_edit_text = new_text

    def mark_external_commit(self) -> None:
        self.last_text_change_source = "app_commit"
        self.last_internal_edit_text = None

    def apply_direct_command(
        self,
        snapshot: SurroundingSnapshot,
        instruction: str,
        environment: EditEnvironment | None = None,
    ) -> DirectEditResult:
        cmd = self.normalize_command(instruction)
        if not cmd:
            return DirectEditResult(False)

        environment = environment or EditEnvironment()
        text = snapshot.text
        cursor = max(0, min(snapshot.cursor_pos, len(text)))
        anchor = max(0, min(snapshot.anchor_pos, len(text)))
        lower_cmd = cmd.lower()
        start, end = sorted((anchor, cursor))
        selected_text = text[start:end] if end > start else ""

        if lower_cmd in {
            "显示上下文",
            "显示上下文信息",
            "输出上下文",
            "输出上下文信息",
            "显示surrounding信息",
            "输出surrounding信息",
            "surrounding info",
            "context info",
        }:
            current, previous = self._sentence_window(text, cursor)
            report = (
                "[VT-SURR "
                f"cap={int(environment.supports_surrounding)} "
                f"active={int(environment.active)} "
                f"del={environment.replace_state} "
                f"len={len(text)} cursor={cursor} anchor={anchor} "
                f"prev='{self._clip(previous)}' "
                f"cur='{self._clip(current)}' "
                f"sel='{self._clip(selected_text)}' "
                f"all='{self._clip(text, 120)}']"
            )
            return DirectEditResult(True, report, True, "已输出上下文信息", "commit_only")

        if lower_cmd in {"撤销", "撤回", "撤销修改", "撤销上一步", "undo"}:
            can_internal = (
                bool(self.undo_stack)
                and self.last_text_change_source == "voice_edit"
                and self.last_internal_edit_text == text
            )
            if can_internal:
                previous = self.undo_stack.pop()
                self.redo_stack.append(text)
                if len(self.redo_stack) > self.history_limit:
                    self.redo_stack.pop(0)
                return DirectEditResult(True, previous, False, "已撤销语音编辑")
            self.mark_external_commit()
            return DirectEditResult(
                True,
                record_history=False,
                hint="已发送应用撤销",
                mode="key_actions",
                key_actions=self._key("z", "ctrl"),
            )

        if lower_cmd in {"重做", "恢复", "redo"}:
            can_internal = (
                bool(self.redo_stack)
                and self.last_text_change_source == "voice_edit"
                and self.last_internal_edit_text == text
            )
            if can_internal:
                recovered = self.redo_stack.pop()
                self.undo_stack.append(text)
                if len(self.undo_stack) > self.history_limit:
                    self.undo_stack.pop(0)
                return DirectEditResult(True, recovered, False, "已重做语音编辑")
            self.mark_external_commit()
            return DirectEditResult(
                True,
                record_history=False,
                hint="已发送应用重做",
                mode="key_actions",
                key_actions=self._key("z", "ctrl", "shift"),
            )

        if lower_cmd in {"复制全部", "复制全文", "copy all"}:
            self.voice_clipboard = text
            return DirectEditResult(True, text, False, "已复制全文", "no_replace")
        if lower_cmd in {"复制选中", "复制选中内容", "copy that"}:
            if not selected_text:
                return DirectEditResult(True, text, False, "当前没有选中内容", "no_replace")
            self.voice_clipboard = selected_text
            return DirectEditResult(True, text, False, "已复制选中内容", "no_replace")
        if lower_cmd in {"剪切全部", "剪切全文", "cut all"}:
            self.voice_clipboard = text
            return DirectEditResult(True, "", True, "已剪切全文")
        if lower_cmd in {"剪切选中", "剪切选中内容", "cut that"}:
            if not selected_text:
                return DirectEditResult(True, text, False, "当前没有选中内容", "no_replace")
            self.voice_clipboard = selected_text
            return DirectEditResult(True, text[:start] + text[end:], True, "已剪切选中内容")
        if lower_cmd in {"粘贴", "贴上", "paste"}:
            if not self.voice_clipboard:
                return DirectEditResult(True, text, False, "剪贴板为空", "no_replace")
            merged = (
                text[:start] + self.voice_clipboard + text[end:]
                if end > start
                else text[:cursor] + self.voice_clipboard + text[cursor:]
            )
            return DirectEditResult(True, merged, True, "已粘贴")

        if lower_cmd in {"清空", "清空输入框", "删除全部", "删掉全部", "全选删除"}:
            return DirectEditResult(True, "", True, "已清空")
        if lower_cmd in {"删除选中", "删除选中内容"}:
            if not selected_text:
                return DirectEditResult(True, text, False, "当前没有选中内容", "no_replace")
            return DirectEditResult(True, text[:start] + text[end:], True, "已删除选中内容")
        if lower_cmd in {"删除当前句", "删掉当前句"}:
            spans = self._sentence_spans(text)
            index = self._locate_sentence_index(spans, cursor)
            if index < 0:
                return DirectEditResult(True, text, False, "未找到当前句", "no_replace")
            seg_start, seg_end = spans[index]
            return DirectEditResult(True, text[:seg_start] + text[seg_end:], True, "已删除当前句")
        if lower_cmd in {"删除上一句", "删掉上一句"}:
            spans = self._sentence_spans(text)
            index = self._locate_sentence_index(spans, cursor)
            if index <= 0:
                return DirectEditResult(True, text, False, "没有上一句可删除", "no_replace")
            seg_start, seg_end = spans[index - 1]
            return DirectEditResult(True, text[:seg_start] + text[seg_end:], True, "已删除上一句")

        match = re.match(r"^(?:把|将)\s*(.+?)\s*(?:改成|改为|替换成|替换为)\s*(.+)\s*$", cmd)
        if match:
            old = self.strip_command_quotes(match.group(1))
            new = self.strip_command_quotes(match.group(2))
            if not old:
                return DirectEditResult(True, text, False, "替换目标为空", "no_replace")
            if old not in text:
                return DirectEditResult(True, text, False, f"未找到“{old}”", "no_replace")
            return DirectEditResult(True, text.replace(old, new, 1), True, "已替换")

        match = re.match(r"^在\s*(.+?)\s*(?:前面|前)\s*插入\s*(.+)\s*$", cmd)
        if match:
            marker = self.strip_command_quotes(match.group(1))
            payload = self.strip_command_quotes(match.group(2))
            index = text.find(marker)
            if index < 0:
                return DirectEditResult(True, text, False, f"未找到“{marker}”", "no_replace")
            return DirectEditResult(True, text[:index] + payload + text[index:], True, "已插入")

        match = re.match(r"^在\s*(.+?)\s*(?:后面|后)\s*插入\s*(.+)\s*$", cmd)
        if match:
            marker = self.strip_command_quotes(match.group(1))
            payload = self.strip_command_quotes(match.group(2))
            index = text.find(marker)
            if index < 0:
                return DirectEditResult(True, text, False, f"未找到“{marker}”", "no_replace")
            insertion = index + len(marker)
            return DirectEditResult(True, text[:insertion] + payload + text[insertion:], True, "已插入")

        match = re.match(r"^(?:在)?(?:开头|最前面)(?:插入|添加|加上)\s*(.+)\s*$", cmd)
        if match:
            return DirectEditResult(True, self.strip_command_quotes(match.group(1)) + text, True, "已在开头插入")
        match = re.match(r"^(?:在)?(?:结尾|末尾|最后)(?:插入|添加|加上|追加)\s*(.+)\s*$", cmd)
        if match:
            return DirectEditResult(True, text + self.strip_command_quotes(match.group(1)), True, "已在结尾插入")
        match = re.match(r"^(?:追加|添加|加上)\s*(.+)\s*$", cmd)
        if match:
            return DirectEditResult(True, text + self.strip_command_quotes(match.group(1)), True, "已追加")
        match = re.match(r"^(?:加|插入)\s*(句号|逗号|问号|感叹号|冒号|分号|引号)\s*$", cmd)
        if match:
            punctuation = self._PUNCTUATION_MAP.get(match.group(1), "")
            if punctuation:
                return DirectEditResult(True, text + punctuation, True, "已添加标点")

        if lower_cmd in {"全部大写", "全大写", "uppercase"}:
            return DirectEditResult(True, text.upper(), True, "已转为大写")
        if lower_cmd in {"全部小写", "全小写", "lowercase"}:
            return DirectEditResult(True, text.lower(), True, "已转为小写")
        if lower_cmd in {"首字母大写", "标题格式", "title case"}:
            return DirectEditResult(True, text.title(), True, "已转为首字母大写")
        if lower_cmd in {"加粗", "加粗选中", "bold", "bold that"}:
            styled = (
                text[:start] + f"**{selected_text}**" + text[end:]
                if end > start
                else f"**{text}**"
            )
            return DirectEditResult(True, styled, True, "已加粗")
        if lower_cmd in {"斜体", "斜体选中", "italic", "italicize"}:
            styled = (
                text[:start] + f"*{selected_text}*" + text[end:]
                if end > start
                else f"*{text}*"
            )
            return DirectEditResult(True, styled, True, "已设为斜体")

        match = re.match(r"^(?:删除|删掉|去掉)\s*(.+)\s*$", cmd)
        if match:
            target = self.strip_command_quotes(match.group(1))
            if target in {"当前句", "上一句", "全部", "选中内容", "选中"}:
                return DirectEditResult(False)
            if target and target in text:
                return DirectEditResult(True, text.replace(target, "", 1), True, "已删除")
            return DirectEditResult(True, text, False, f"未找到“{target}”", "no_replace")

        count = self.parse_count(cmd)
        if lower_cmd in {"全选", "选中全部", "select all"}:
            return DirectEditResult(True, record_history=False, hint="已全选", mode="key_actions", key_actions=self._key("a", "ctrl"))
        if lower_cmd in {"移动到开头", "跳到开头", "到开头", "行首", "到行首", "移动到行首"}:
            return DirectEditResult(True, record_history=False, hint="已移动到开头", mode="key_actions", key_actions=self._key("home"))
        if lower_cmd in {"移动到结尾", "跳到结尾", "到结尾", "行尾", "到行尾", "移动到行尾"}:
            return DirectEditResult(True, record_history=False, hint="已移动到结尾", mode="key_actions", key_actions=self._key("end"))
        if lower_cmd in {"段首", "到段首", "移动到段首"}:
            return DirectEditResult(True, record_history=False, hint="已尝试移动到段首", mode="key_actions", key_actions=self._key("up", "ctrl"))
        if lower_cmd in {"段尾", "到段尾", "移动到段尾"}:
            return DirectEditResult(True, record_history=False, hint="已尝试移动到段尾", mode="key_actions", key_actions=self._key("down", "ctrl"))
        if re.match(r"^(?:向|往)?左(?:移|移动)?(?:\s*\d+|\s*[一二两三四五六七八九十])?(?:次|个字|个字符)?$", cmd) or lower_cmd in {"左移", "向左"}:
            return DirectEditResult(True, record_history=False, hint=f"已左移{count}次", mode="key_actions", key_actions=self._key("left", repeat=count))
        if re.match(r"^(?:向|往)?右(?:移|移动)?(?:\s*\d+|\s*[一二两三四五六七八九十])?(?:次|个字|个字符)?$", cmd) or lower_cmd in {"右移", "向右"}:
            return DirectEditResult(True, record_history=False, hint=f"已右移{count}次", mode="key_actions", key_actions=self._key("right", repeat=count))
        if lower_cmd in {"下一个词", "到下一个词", "移动到下一个词", "next word"}:
            return DirectEditResult(True, record_history=False, hint="已移动到下一个词", mode="key_actions", key_actions=self._key("right", "ctrl", repeat=count))
        if lower_cmd in {"上一个词", "到上一个词", "移动到上一个词", "previous word"}:
            return DirectEditResult(True, record_history=False, hint="已移动到上一个词", mode="key_actions", key_actions=self._key("left", "ctrl", repeat=count))
        if lower_cmd in {"选中下一个词", "选择下一个词"}:
            return DirectEditResult(True, record_history=False, hint="已尝试选中下一个词", mode="key_actions", key_actions=self._key("right", "ctrl", "shift", repeat=count))
        if lower_cmd in {"选中上一个词", "选择上一个词"}:
            return DirectEditResult(True, record_history=False, hint="已尝试选中上一个词", mode="key_actions", key_actions=self._key("left", "ctrl", "shift", repeat=count))

        return DirectEditResult(False)
