"""GTK 3 settings, setup, diagnostics, and feedback application."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from vocotype_version import __version__

from .config_service import (
    ensure_terms_template,
    load_audio_config,
    load_fcitx_module_config,
    load_runtime_config,
    save_audio_config,
    save_fcitx_module_config,
    save_runtime_config,
    terms_path,
)
from .doctor import DoctorCheck, doctor_summary, run_doctor
from .feedback import open_github_issue, submit_feedback
from .setup_manager import (
    InstallOptions,
    UninstallOptions,
    find_project_root,
    install_or_repair,
    installation_paths,
    native_package_removal_command,
    polkit_available,
    restart_backend,
    restart_fcitx,
    restart_ibus,
    uninstall_framework,
)
from .support_bundle import create_support_bundle

APP_ID = "io.github.LeonardNJU.VoCoType.Settings"
DEFAULT_TERMS_TEMPLATE = """# VoCoType 统一术语库
terms:
  - canonical: Ghostty
    aliases: [鬼斯提, 格斯提]
    hotword: true
    protect: true

protect:
  - 三体问题
  - 一加手机
"""

CSS = b"""
window { background: #f6f7f9; }
headerbar { background: #ffffff; border-bottom: 1px solid #dfe3e8; }
.sidebar { background: #eef0f3; border-right: 1px solid #d9dde3; padding: 12px; }
.page { padding: 28px 34px; }
.page-title { font-size: 24px; font-weight: 700; color: #20242a; }
.page-subtitle { font-size: 14px; color: #6f7782; margin-bottom: 14px; }
.card { background: #ffffff; border: 1px solid #dfe3e8; border-radius: 12px; padding: 4px; }
.card-row { padding: 12px 14px; border-bottom: 1px solid #eceff2; }
.card-row:last-child { border-bottom: 0; }
.row-title { font-size: 15px; font-weight: 600; color: #252a31; }
.row-subtitle { font-size: 12px; color: #7a828d; }
.status-pass { color: #168b46; font-weight: 600; }
.status-warn { color: #a66a00; font-weight: 600; }
.status-fail { color: #bf2c2c; font-weight: 600; }
.monospace { font-family: monospace; }
.preview { background: #f2f5f8; border-radius: 8px; padding: 12px; }
.accent { background: #2f7de1; color: white; border-radius: 8px; padding: 8px 15px; }
"""


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if value is None:
        return default
    return bool(value)


class SettingsWindow(Gtk.ApplicationWindow):
    def __init__(self, application: Gtk.Application):
        super().__init__(application=application, title="VoCoType 设置")
        self.set_default_size(1120, 760)
        self.set_size_request(900, 620)
        self.runtime_config = load_runtime_config()
        self.module_config = load_fcitx_module_config()
        self.last_doctor_checks: list[DoctorCheck] = []
        self.last_bundle_path: Path | None = None
        self._install_dialog: Gtk.Dialog | None = None
        self._uninstall_dialog: Gtk.Dialog | None = None
        self._build_header()
        self._build_layout()
        self._load_values()

    def _build_header(self) -> None:
        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        header.props.title = "VoCoType"
        header.props.subtitle = f"语音输入设置 · {__version__}"
        save_button = Gtk.Button(label="保存设置")
        save_button.get_style_context().add_class("suggested-action")
        save_button.connect("clicked", self._on_save)
        header.pack_end(save_button)
        self.set_titlebar(header)

    def _build_layout(self) -> None:
        root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.add(root)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=160)
        self.stack.set_hexpand(True)
        self.stack.set_vexpand(True)
        sidebar = Gtk.StackSidebar(stack=self.stack)
        sidebar.set_size_request(220, -1)
        sidebar.get_style_context().add_class("sidebar")
        root.pack_start(sidebar, False, False, 0)
        root.pack_start(self.stack, True, True, 0)

        self.stack.add_titled(self._overview_page(), "overview", "概览与安装")
        self.stack.add_titled(self._recognition_page(), "recognition", "语音识别与 ITN")
        self.stack.add_titled(self._terms_page(), "terms", "用户词典")
        self.stack.add_titled(self._slm_page(), "slm", "AI 润色")
        self.stack.add_titled(self._doctor_page(), "doctor", "诊断")
        self.stack.add_titled(self._tutorial_page(), "tutorial", "教程")
        self.stack.add_titled(self._feedback_page(), "feedback", "反馈")

    def _page(self, title: str, subtitle: str) -> tuple[Gtk.ScrolledWindow, Gtk.Box]:
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        content.get_style_context().add_class("page")
        title_label = Gtk.Label(label=title, xalign=0)
        title_label.get_style_context().add_class("page-title")
        subtitle_label = Gtk.Label(label=subtitle, xalign=0)
        subtitle_label.set_line_wrap(True)
        subtitle_label.get_style_context().add_class("page-subtitle")
        content.pack_start(title_label, False, False, 0)
        content.pack_start(subtitle_label, False, False, 0)
        scroller.add(content)
        return scroller, content

    def _card(self) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.get_style_context().add_class("card")
        return box

    def _row(
        self,
        title: str,
        subtitle: str = "",
        control: Gtk.Widget | None = None,
    ) -> Gtk.Box:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        row.get_style_context().add_class("card-row")
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        title_label = Gtk.Label(label=title, xalign=0)
        title_label.get_style_context().add_class("row-title")
        labels.pack_start(title_label, False, False, 0)
        if subtitle:
            subtitle_label = Gtk.Label(label=subtitle, xalign=0)
            subtitle_label.set_line_wrap(True)
            subtitle_label.get_style_context().add_class("row-subtitle")
            labels.pack_start(subtitle_label, False, False, 0)
        row.pack_start(labels, True, True, 0)
        if control is not None:
            control.set_valign(Gtk.Align.CENTER)
            row.pack_end(control, False, False, 0)
        return row

    def _switch(self) -> Gtk.Switch:
        widget = Gtk.Switch()
        widget.set_halign(Gtk.Align.END)
        return widget

    def _overview_page(self) -> Gtk.Widget:
        page, content = self._page(
            "概览与安装",
            "从这里完成首次安装、升级或修复。配置与术语文件会被保留。",
        )
        card = self._card()
        self.install_status = Gtk.Label(label="尚未检查安装状态", xalign=0)
        self.install_status.set_line_wrap(True)
        lifecycle_actions = Gtk.Box(spacing=8)
        install_button = Gtk.Button(label="安装 / 修复 Fcitx 5")
        install_button.get_style_context().add_class("suggested-action")
        install_button.connect("clicked", lambda _b: self._open_install_dialog("fcitx5"))
        ibus_install_button = Gtk.Button(label="安装 / 修复 IBus")
        ibus_install_button.connect("clicked", lambda _b: self._open_install_dialog("ibus"))
        uninstall_fcitx_button = Gtk.Button(label="卸载 Fcitx 5")
        uninstall_fcitx_button.connect("clicked", lambda _b: self._open_uninstall_dialog("fcitx5"))
        uninstall_ibus_button = Gtk.Button(label="卸载 IBus")
        uninstall_ibus_button.connect("clicked", lambda _b: self._open_uninstall_dialog("ibus"))
        lifecycle_actions.pack_start(install_button, False, False, 0)
        lifecycle_actions.pack_start(ibus_install_button, False, False, 0)
        lifecycle_actions.pack_start(uninstall_fcitx_button, False, False, 0)
        lifecycle_actions.pack_start(uninstall_ibus_button, False, False, 0)

        restart_actions = Gtk.Box(spacing=8)
        restart_service = Gtk.Button(label="重启后台服务")
        restart_service.connect("clicked", lambda _b: self._run_quick_action(restart_backend))
        restart_fcitx_button = Gtk.Button(label="重启 Fcitx 5")
        restart_fcitx_button.connect("clicked", lambda _b: self._run_quick_action(restart_fcitx))
        restart_ibus_button = Gtk.Button(label="重启 IBus")
        restart_ibus_button.connect("clicked", lambda _b: self._run_quick_action(restart_ibus))
        restart_actions.pack_start(restart_service, False, False, 0)
        restart_actions.pack_start(restart_fcitx_button, False, False, 0)
        restart_actions.pack_start(restart_ibus_button, False, False, 0)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.pack_start(self.install_status, False, False, 0)
        box.pack_start(lifecycle_actions, False, False, 0)
        box.pack_start(restart_actions, False, False, 0)
        card.pack_start(
            self._row(
                "VoCoType 安装",
                "Fcitx 5 与 IBus 均在本窗口完成。缺少系统依赖或需要注册系统 IBus component 时，Polkit 会弹出标准管理员授权窗口。",
                box,
            ),
            False,
            False,
            0,
        )

        self.overview_summary = Gtk.Label(xalign=0)
        self.overview_summary.set_line_wrap(True)
        doctor_button = Gtk.Button(label="运行快速检查")
        doctor_button.connect("clicked", self._on_run_doctor)
        card.pack_start(self._row("运行状态", "检查 module、后台服务、IPC、麦克风、配置与 ITN。", doctor_button), False, False, 0)
        card.pack_start(self._row("当前摘要", control=self.overview_summary), False, False, 0)
        content.pack_start(card, False, False, 0)
        GLib.idle_add(self._refresh_install_status)
        return page

    def _recognition_page(self) -> Gtk.Widget:
        page, content = self._page(
            "语音识别与 ITN",
            "术语标准化始终生效；数字、日期、时间、路程和金额格式可以分别控制并实时预览。",
        )
        audio_card = self._card()
        self.audio_device = Gtk.ComboBoxText()
        self.audio_device.set_hexpand(True)
        self.audio_sample_rate = Gtk.SpinButton.new_with_range(8000, 192000, 1000)
        self.audio_sample_rate.set_value(44100)
        self.audio_status = Gtk.Label(label="尚未枚举麦克风", xalign=0)
        self.audio_status.set_line_wrap(True)
        audio_actions = Gtk.Box(spacing=8)
        refresh_audio = Gtk.Button(label="刷新设备")
        refresh_audio.connect("clicked", self._on_refresh_audio)
        test_audio = Gtk.Button(label="录音 2 秒测试")
        test_audio.connect("clicked", self._on_test_audio)
        audio_actions.pack_start(refresh_audio, False, False, 0)
        audio_actions.pack_start(test_audio, False, False, 0)
        audio_actions.pack_start(self.audio_status, True, True, 0)
        audio_card.pack_start(
            self._row("输入设备", "选择用于 F9 录音的麦克风。", self.audio_device),
            False,
            False,
            0,
        )
        audio_card.pack_start(
            self._row("原生采样率", "默认采用设备报告的采样率，后端会重采样到 16 kHz。", self.audio_sample_rate),
            False,
            False,
            0,
        )
        audio_card.pack_start(self._row("设备测试", control=audio_actions), False, False, 0)
        content.pack_start(audio_card, False, False, 0)

        card = self._card()
        self.itn_enabled = self._switch()
        self.compact_dates = self._switch()
        self.compact_times = self._switch()
        self.compact_distances = self._switch()
        self.currency_symbols = self._switch()
        card.pack_start(self._row("启用数字与 ITN", "关闭后保留用户词典替换，但不改写中文数字。", self.itn_enabled), False, False, 0)
        card.pack_start(self._row("紧凑日期", "例如：二零二六年五月十一号 → 2026/05/11", self.compact_dates), False, False, 0)
        card.pack_start(self._row("24 小时时间", "例如：下午三点二十分 → 15:20", self.compact_times), False, False, 0)
        card.pack_start(self._row("路程单位缩写", "例如：三百二十米 → 320m", self.compact_distances), False, False, 0)
        card.pack_start(self._row("金额符号", "例如：一百二十八元 → ¥128", self.currency_symbols), False, False, 0)
        content.pack_start(card, False, False, 0)

        preview_card = self._card()
        self.preview_input = Gtk.Entry()
        self.preview_input.set_text("二零二六年五月十一号下午三点二十分跑了三百二十米，花了一百二十八元")
        self.preview_input.connect("activate", self._on_preview)
        preview_button = Gtk.Button(label="生成预览")
        preview_button.connect("clicked", self._on_preview)
        input_box = Gtk.Box(spacing=8)
        input_box.pack_start(self.preview_input, True, True, 0)
        input_box.pack_start(preview_button, False, False, 0)
        self.preview_output = Gtk.Label(label="点击“生成预览”查看结果", xalign=0)
        self.preview_output.set_line_wrap(True)
        self.preview_output.set_selectable(True)
        self.preview_output.get_style_context().add_class("preview")
        preview_card.pack_start(self._row("测试文本", control=input_box), False, False, 0)
        preview_card.pack_start(self._row("预览结果", control=self.preview_output), False, False, 0)
        content.pack_start(preview_card, False, False, 0)
        return page

    def _terms_page(self) -> Gtk.Widget:
        page, content = self._page(
            "用户词典",
            "同一份术语库同时用于 Contextual Paraformer 原生 hotword、识别后标准化和 ITN 保护。",
        )
        toolbar = Gtk.Box(spacing=8)
        reload_button = Gtk.Button(label="重新载入")
        reload_button.connect("clicked", lambda _b: self._load_terms())
        save_button = Gtk.Button(label="验证并保存")
        save_button.get_style_context().add_class("suggested-action")
        save_button.connect("clicked", self._on_save_terms)
        open_button = Gtk.Button(label="在文件管理器中显示")
        open_button.connect("clicked", self._on_open_terms)
        toolbar.pack_start(reload_button, False, False, 0)
        toolbar.pack_start(save_button, False, False, 0)
        toolbar.pack_start(open_button, False, False, 0)
        content.pack_start(toolbar, False, False, 0)
        self.terms_view = Gtk.TextView()
        self.terms_view.set_monospace(True)
        self.terms_view.set_wrap_mode(Gtk.WrapMode.NONE)
        editor_scroll = Gtk.ScrolledWindow()
        editor_scroll.set_min_content_height(440)
        editor_scroll.add(self.terms_view)
        content.pack_start(editor_scroll, True, True, 0)
        self.terms_status = Gtk.Label(xalign=0)
        content.pack_start(self.terms_status, False, False, 0)
        GLib.idle_add(self._load_terms)
        return page

    def _slm_page(self) -> Gtk.Widget:
        page, content = self._page(
            "AI 润色",
            "配置本地或 OpenAI-compatible 远程服务。API Key 可直接保存，也可以仅填写环境变量名。",
        )
        card = self._card()
        self.slm_enabled = self._switch()
        self.polish_by_default = self._switch()
        self.slm_remote_stream = self._switch()
        self.slm_thinking = self._switch()
        self.slm_provider = Gtk.ComboBoxText()
        self.slm_provider.append("remote", "远程 OpenAI-compatible")
        self.slm_provider.append("local_ephemeral", "本地按需模型")
        self.slm_endpoint = Gtk.Entry()
        self.slm_model = Gtk.Entry()
        self.slm_api_key_env = Gtk.Entry()
        self.slm_api_key = Gtk.Entry()
        self.slm_api_key.set_visibility(False)
        self.slm_api_key.set_placeholder_text("留空则保留现有凭据")
        self.slm_clear_api_key = Gtk.CheckButton(label="清除已保存的直接 API Key")
        self.slm_min_chars = Gtk.SpinButton.new_with_range(0, 2000, 1)
        self.slm_timeout = Gtk.SpinButton.new_with_range(1000, 120000, 1000)
        card.pack_start(self._row("启用 AI 润色", "Shift+F9 或默认润色模式才会调用。", self.slm_enabled), False, False, 0)
        card.pack_start(
            self._row(
                "Fcitx：F9 默认润色",
                "开启后 Shift+F9 临时跳过润色；IBus 仍保持 Shift+F9 才润色。",
                self.polish_by_default,
            ),
            False,
            False,
            0,
        )
        card.pack_start(self._row("Provider", control=self.slm_provider), False, False, 0)
        card.pack_start(self._row("API 地址", "可填写服务根地址或 /v1/chat/completions。", self.slm_endpoint), False, False, 0)
        card.pack_start(self._row("模型", control=self.slm_model), False, False, 0)
        card.pack_start(self._row("API Key 环境变量", "例如 DEEPSEEK_API_KEY；优先于空白直接凭据。", self.slm_api_key_env), False, False, 0)
        card.pack_start(self._row("更新 API Key", "配置文件权限固定为 0600；留空不修改。", self.slm_api_key), False, False, 0)
        card.pack_start(self._row("清除直接凭据", "切换到环境变量凭据时可清除旧值。", self.slm_clear_api_key), False, False, 0)
        card.pack_start(self._row("最少润色字符数", "0 表示不限制。", self.slm_min_chars), False, False, 0)
        card.pack_start(self._row("流式空闲超时（毫秒）", control=self.slm_timeout), False, False, 0)
        card.pack_start(self._row("远程流式输出", "Fcitx 候选框实时显示可见增量。", self.slm_remote_stream), False, False, 0)
        card.pack_start(self._row("允许 reasoning/thinking", "思考内容不会进入最终提交。", self.slm_thinking), False, False, 0)
        content.pack_start(card, False, False, 0)
        actions = Gtk.Box(spacing=8)
        test_button = Gtk.Button(label="测试 AI 连接")
        test_button.connect("clicked", self._on_test_slm)
        actions.pack_start(test_button, False, False, 0)
        self.slm_test_status = Gtk.Label(xalign=0)
        self.slm_test_status.set_line_wrap(True)
        actions.pack_start(self.slm_test_status, True, True, 0)
        content.pack_start(actions, False, False, 0)
        return page

    def _doctor_page(self) -> Gtk.Widget:
        page, content = self._page(
            "诊断",
            "自动检查常见安装与运行问题。仍无法解决时，可生成不含录音和凭据的支持包。",
        )
        actions = Gtk.Box(spacing=8)
        run_button = Gtk.Button(label="运行 Doctor")
        run_button.get_style_context().add_class("suggested-action")
        run_button.connect("clicked", self._on_run_doctor)
        export_button = Gtk.Button(label="导出支持包")
        export_button.connect("clicked", self._on_export_bundle)
        actions.pack_start(run_button, False, False, 0)
        actions.pack_start(export_button, False, False, 0)
        self.doctor_summary_label = Gtk.Label(xalign=0)
        actions.pack_start(self.doctor_summary_label, True, True, 0)
        content.pack_start(actions, False, False, 0)
        self.doctor_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        content.pack_start(self.doctor_list, False, False, 0)
        return page

    def _tutorial_page(self) -> Gtk.Widget:
        page, content = self._page("教程", "完成安装后，无需把 VoCoType 添加为输入法。它作为 Fcitx 全局 module 增强现有输入法。")
        card = self._card()
        steps = [
            ("1. 安装或修复", "在“概览与安装”点击安装/修复，然后注销并重新登录一次，让桌面会话读取用户 addon 路径。"),
            ("2. 保留原输入法", "继续使用雾凇拼音、Rime、Mozc 或任意 Fcitx 5 输入法，不再切换到 VoCoType。"),
            ("3. 语音输入", "按住 F9 说话，松开识别；Shift+F9 使用 AI 润色。"),
            ("4. 添加术语", "在用户词典中加入项目名、人名和专业术语。hotword 提高识别概率，aliases 保证标准拼写。"),
            ("5. 排障", "F9 无响应时先运行 Doctor；支持包可直接附到 GitHub issue。"),
        ]
        for title, description in steps:
            card.pack_start(self._row(title, description), False, False, 0)
        content.pack_start(card, False, False, 0)
        return page

    def _feedback_page(self) -> Gtk.Widget:
        page, content = self._page(
            "反馈",
            "反馈端点留空时会打开预填好的 GitHub issue；配置项目反馈端点后可直接发送。",
        )
        self.feedback_endpoint = Gtk.Entry()
        self.feedback_endpoint.set_placeholder_text("可选：https://.../feedback")
        content.pack_start(self._row("反馈端点", "仅在你信任该端点时发送诊断信息。", self.feedback_endpoint), False, False, 0)
        self.feedback_view = Gtk.TextView()
        self.feedback_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        feedback_scroll = Gtk.ScrolledWindow()
        feedback_scroll.set_min_content_height(200)
        feedback_scroll.add(self.feedback_view)
        content.pack_start(feedback_scroll, False, False, 0)
        self.feedback_include_doctor = Gtk.CheckButton(label="附带 Doctor 结果")
        self.feedback_include_bundle = Gtk.CheckButton(
            label="附带支持包（最大 5 MiB；口述文本字段会脱敏，发送前仍建议检查）"
        )
        content.pack_start(self.feedback_include_doctor, False, False, 0)
        content.pack_start(self.feedback_include_bundle, False, False, 0)
        actions = Gtk.Box(spacing=8)
        submit = Gtk.Button(label="发送反馈 / 创建 Issue")
        submit.get_style_context().add_class("suggested-action")
        submit.connect("clicked", self._on_feedback)
        actions.pack_start(submit, False, False, 0)
        self.feedback_status = Gtk.Label(xalign=0)
        self.feedback_status.set_line_wrap(True)
        actions.pack_start(self.feedback_status, True, True, 0)
        content.pack_start(actions, False, False, 0)
        return page

    def _load_values(self) -> None:
        normalization = self.runtime_config.get("normalization", {})
        slm = self.runtime_config.get("slm", {})
        feedback = self.runtime_config.get("feedback", {})
        self.itn_enabled.set_active(_as_bool(normalization.get("enabled"), True))
        self.compact_dates.set_active(_as_bool(normalization.get("compact_dates"), True))
        self.compact_times.set_active(_as_bool(normalization.get("compact_times"), True))
        self.compact_distances.set_active(_as_bool(normalization.get("compact_distances"), True))
        self.currency_symbols.set_active(_as_bool(normalization.get("currency_symbols"), True))
        self.slm_enabled.set_active(_as_bool(slm.get("enabled"), False))
        provider = str(slm.get("provider", "remote"))
        self.slm_provider.set_active_id(provider if provider in {"remote", "local_ephemeral"} else "remote")
        self.slm_endpoint.set_text(str(slm.get("endpoint", "")))
        self.slm_model.set_text(str(slm.get("model", "")))
        self.slm_api_key_env.set_text(str(slm.get("api_key_env", "")))
        self.slm_min_chars.set_value(float(slm.get("min_chars", 8)))
        self.slm_timeout.set_value(float(slm.get("stream_idle_timeout_ms", slm.get("timeout_ms", 20000))))
        self.slm_remote_stream.set_active(_as_bool(slm.get("remote_stream"), True))
        self.slm_thinking.set_active(_as_bool(slm.get("enable_thinking"), False))
        self.polish_by_default.set_active(_as_bool(self.module_config.get("polishbydefault"), False))
        self.feedback_endpoint.set_text(str(feedback.get("endpoint", "")))
        try:
            self._saved_audio_config = load_audio_config()
        except Exception as exc:  # noqa: BLE001
            self._saved_audio_config = {"device_name": "", "device_id": None, "sample_rate": 0}
            self.audio_status.set_text(f"音频配置读取失败：{exc}")
        if int(self._saved_audio_config.get("sample_rate") or 0) > 0:
            self.audio_sample_rate.set_value(
                int(self._saved_audio_config["sample_rate"])
            )
        GLib.idle_add(self._start_audio_refresh)

    def _current_normalization(self) -> dict[str, bool]:
        return {
            "enabled": self.itn_enabled.get_active(),
            "compact_dates": self.compact_dates.get_active(),
            "compact_times": self.compact_times.get_active(),
            "compact_distances": self.compact_distances.get_active(),
            "currency_symbols": self.currency_symbols.get_active(),
        }

    def _current_slm(self, *, preserve_secret: bool = True) -> dict[str, Any]:
        existing = self.runtime_config.get("slm", {})
        result = dict(existing if isinstance(existing, dict) else {})
        result.update(
            {
                "enabled": self.slm_enabled.get_active(),
                "provider": self.slm_provider.get_active_id() or "remote",
                "endpoint": self.slm_endpoint.get_text().strip(),
                "model": self.slm_model.get_text().strip(),
                "api_key_env": self.slm_api_key_env.get_text().strip(),
                "min_chars": int(self.slm_min_chars.get_value()),
                "timeout_ms": int(self.slm_timeout.get_value()),
                "stream_idle_timeout_ms": int(self.slm_timeout.get_value()),
                "remote_stream": self.slm_remote_stream.get_active(),
                "enable_thinking": self.slm_thinking.get_active(),
            }
        )
        entered_key = self.slm_api_key.get_text().strip()
        if entered_key:
            result["api_key"] = entered_key
        elif self.slm_clear_api_key.get_active() or not preserve_secret:
            result["api_key"] = ""
        return result

    def _on_save(self, _button: Gtk.Button) -> None:
        try:
            active_audio_id = self.audio_device.get_active_id()
            if active_audio_id is not None:
                device_id = int(active_audio_id)
                device_name = self.audio_device.get_active_text() or ""
                # The displayed item starts with “[id] ”; persist the original
                # PortAudio name for stable lookup across reboots.
                device_name = self._audio_devices.get(device_id, {}).get(
                    "name", device_name
                )
                save_audio_config(
                    device_name=str(device_name),
                    device_id=device_id,
                    sample_rate=int(self.audio_sample_rate.get_value()),
                )
            config = load_runtime_config()
            config["normalization"] = self._current_normalization()
            config["slm"] = self._current_slm()
            feedback = config.get("feedback")
            if not isinstance(feedback, dict):
                feedback = {}
            feedback["endpoint"] = self.feedback_endpoint.get_text().strip()
            config["feedback"] = feedback
            save_runtime_config(config)
            save_fcitx_module_config(
                {
                    "PolishByDefault": self.polish_by_default.get_active(),
                    "PolishMinChars": int(self.slm_min_chars.get_value()),
                    "PolishTimeoutMs": int(self.slm_timeout.get_value()),
                    "EnableThinking": self.slm_thinking.get_active(),
                }
            )
            self.runtime_config = config
            self.module_config = load_fcitx_module_config()
            self.slm_api_key.set_text("")
            self.slm_clear_api_key.set_active(False)
        except Exception as exc:  # noqa: BLE001
            self._message("保存失败", str(exc), Gtk.MessageType.ERROR)
            return

        def reload_services() -> None:
            paths = installation_paths()
            fcitx_module_present = any(path.is_file() for path in paths.fcitx_modules)
            backend_service_present = any(path.is_file() for path in paths.fcitx_services)
            if backend_service_present:
                backend_ok, backend_message = restart_backend()
            else:
                backend_ok, backend_message = True, "未安装 Fcitx 后台服务"
            if fcitx_module_present and shutil.which("fcitx5"):
                fcitx_ok, fcitx_message = restart_fcitx()
            else:
                fcitx_ok, fcitx_message = True, "未启用 Fcitx module"
            details = [
                "配置已同步写入 IBus 与 Fcitx。",
                (
                    "Fcitx 后台服务已重启"
                    if backend_service_present and backend_ok
                    else backend_message
                ),
                (
                    "Fcitx 5 已重载"
                    if fcitx_module_present and fcitx_ok
                    else fcitx_message
                ),
                "IBus 会在下一次按下录音键时自动重载配置。",
            ]
            GLib.idle_add(
                self._message,
                "保存成功",
                "\n".join(details),
                Gtk.MessageType.INFO,
            )

        threading.Thread(target=reload_services, daemon=True).start()

    def _start_audio_refresh(self) -> bool:
        self._on_refresh_audio(None)
        return False

    def _on_refresh_audio(self, _button: Gtk.Button | None) -> None:
        self.audio_status.set_text("正在枚举输入设备…")
        saved = getattr(self, "_saved_audio_config", {})

        def work() -> None:
            try:
                import sounddevice as sd

                devices: list[dict[str, Any]] = []
                for index, item in enumerate(sd.query_devices()):
                    if int(item.get("max_input_channels", 0)) <= 0:
                        continue
                    devices.append(
                        {
                            "id": index,
                            "name": str(item.get("name", f"Device {index}")),
                            "sample_rate": int(float(item.get("default_samplerate", 44100))),
                            "channels": int(item.get("max_input_channels", 0)),
                        }
                    )
                error = ""
            except Exception as exc:  # noqa: BLE001
                devices = []
                error = str(exc)
            GLib.idle_add(self._render_audio_devices, devices, saved, error)

        threading.Thread(target=work, daemon=True).start()

    def _render_audio_devices(
        self,
        devices: list[dict[str, Any]],
        saved: dict[str, Any],
        error: str,
    ) -> bool:
        self.audio_device.remove_all()
        self._audio_devices = {int(item["id"]): item for item in devices}
        if error:
            self.audio_status.set_text(f"无法枚举麦克风：{error}")
            return False
        if not devices:
            self.audio_status.set_text("没有检测到可用的输入设备")
            return False
        saved_id = saved.get("device_id")
        saved_name = str(saved.get("device_name") or "")
        selected_id: int | None = None
        for item in devices:
            device_id = int(item["id"])
            label = f"[{device_id}] {item['name']}"
            self.audio_device.append(str(device_id), label)
            if saved_name and item["name"] == saved_name:
                selected_id = device_id
            elif selected_id is None and saved_id == device_id:
                selected_id = device_id
        if selected_id is None:
            selected_id = int(devices[0]["id"])
        self.audio_device.set_active_id(str(selected_id))
        selected = self._audio_devices[selected_id]
        if not int(saved.get("sample_rate") or 0):
            self.audio_sample_rate.set_value(selected["sample_rate"])
        self.audio_status.set_text(f"检测到 {len(devices)} 个输入设备")
        return False

    def _on_test_audio(self, _button: Gtk.Button) -> None:
        active_id = self.audio_device.get_active_id()
        if active_id is None:
            self.audio_status.set_text("请先选择麦克风")
            return
        device_id = int(active_id)
        sample_rate = int(self.audio_sample_rate.get_value())
        self.audio_status.set_text("正在录音 2 秒，请正常说话…")

        def work() -> None:
            try:
                import numpy as np
                import sounddevice as sd

                frames = sd.rec(
                    int(sample_rate * 2),
                    samplerate=sample_rate,
                    channels=1,
                    dtype="float32",
                    device=device_id,
                )
                sd.wait()
                samples = np.asarray(frames, dtype=np.float32).reshape(-1)
                peak = float(np.max(np.abs(samples))) if samples.size else 0.0
                rms = float(np.sqrt(np.mean(np.square(samples)))) if samples.size else 0.0
                if peak < 0.002:
                    message = f"录音完成，但信号很弱：peak={peak:.4f}, RMS={rms:.4f}"
                elif peak > 0.98:
                    message = f"录音完成，但可能削波：peak={peak:.3f}, RMS={rms:.3f}"
                else:
                    message = f"麦克风正常：peak={peak:.3f}, RMS={rms:.3f}"
            except Exception as exc:  # noqa: BLE001
                message = f"录音测试失败：{exc}"
            GLib.idle_add(self.audio_status.set_text, message)

        threading.Thread(target=work, daemon=True).start()

    def _on_preview(self, _widget: Gtk.Widget) -> None:
        source = self.preview_input.get_text()
        self.preview_output.set_text("正在生成预览…")

        normalization = self._current_normalization()

        def work() -> None:
            try:
                from app.text_normalizer import normalize_text
                output = normalize_text(source, config=normalization)
            except Exception as exc:  # noqa: BLE001
                output = f"预览失败：{exc}"
            GLib.idle_add(self.preview_output.set_text, output)

        threading.Thread(target=work, daemon=True).start()

    def _load_terms(self) -> bool:
        path = ensure_terms_template(DEFAULT_TERMS_TEMPLATE)
        try:
            content = path.read_text(encoding="utf-8")
            self.terms_view.get_buffer().set_text(content)
            self.terms_status.set_text(f"当前文件：{path}")
        except Exception as exc:  # noqa: BLE001
            self.terms_status.set_text(f"读取失败：{exc}")
        return False

    def _on_save_terms(self, _button: Gtk.Button) -> None:
        buffer = self.terms_view.get_buffer()
        content = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)
        try:
            import yaml
            parsed = yaml.safe_load(content)
            if parsed is not None and not isinstance(parsed, dict):
                raise ValueError("YAML 顶层必须是映射")
            path = terms_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_name(f".{path.name}.tmp")
            temp.write_text(content, encoding="utf-8")
            os.chmod(temp, 0o600)
            os.replace(temp, path)
            self.terms_status.set_text(f"已验证并保存：{path}")
        except Exception as exc:  # noqa: BLE001
            self._message("术语库未保存", str(exc), Gtk.MessageType.ERROR)

    def _on_open_terms(self, _button: Gtk.Button) -> None:
        path = ensure_terms_template(DEFAULT_TERMS_TEMPLATE)
        uri = path.parent.resolve().as_uri()
        try:
            Gtk.show_uri_on_window(self, uri, Gdk.CURRENT_TIME)
        except Exception:  # noqa: BLE001 - desktop fallback.
            if shutil.which("xdg-open"):
                subprocess.Popen(["xdg-open", str(path.parent)])
            else:
                self._message("无法打开文件管理器", str(path.parent), Gtk.MessageType.WARNING)

    def _on_test_slm(self, _button: Gtk.Button) -> None:
        self.slm_test_status.set_text("正在测试…")
        config = self._current_slm()
        config["enabled"] = True
        config["min_chars"] = 1

        def work() -> None:
            try:
                from app.slm_polisher import SLMPolisher
                output, metrics = SLMPolisher(config).polish("这是一次 VoCoType AI 润色连接测试。", long_mode=True)
                if metrics.reason == "ok":
                    message = f"连接成功：{output}"
                else:
                    message = f"连接失败：{metrics.reason}"
            except Exception as exc:  # noqa: BLE001
                message = f"连接失败：{exc}"
            GLib.idle_add(self.slm_test_status.set_text, message)

        threading.Thread(target=work, daemon=True).start()

    def _refresh_install_status(self) -> bool:
        root = find_project_root()
        paths = installation_paths()
        lines = [
            f"源码目录：{root or '未发现'}",
            f"Polkit 授权：{'可用' if polkit_available() else '未检测到 pkexec'}",
            f"原生软件包：{'已安装' if native_package_removal_command(root) else '未检测到'}",
            f"Fcitx module：{'已安装' if any(path.is_file() for path in paths.fcitx_modules) else '未安装'}",
            f"Fcitx addon：{'已安装' if any(path.is_file() for path in paths.fcitx_addons) else '未安装'}",
            f"IBus launcher：{'已安装' if any(path.is_file() for path in paths.ibus_launchers) else '未安装'}",
            f"IBus component：{'已安装' if any(path.is_file() for path in paths.ibus_components) else '未安装'}",
        ]
        self.install_status.set_text("\n".join(lines))
        return False

    def _open_install_dialog(self, framework: str) -> None:
        if self._install_dialog is not None and self._install_dialog.get_visible():
            self._install_dialog.present()
            return
        is_ibus = framework == "ibus"
        title = "安装 / 修复 IBus" if is_ibus else "安装 / 修复 Fcitx 5"
        dialog = Gtk.Dialog(title=title, transient_for=self, modal=True)
        self._install_dialog = dialog
        dialog.connect("destroy", lambda _dialog: setattr(self, "_install_dialog", None))
        dialog.add_button("关闭", Gtk.ResponseType.CLOSE)
        start_button = dialog.add_button("开始安装", Gtk.ResponseType.APPLY)
        start_button.get_style_context().add_class("suggested-action")
        close_button = dialog.get_widget_for_response(Gtk.ResponseType.CLOSE)
        dialog.set_default_size(860, 650)

        content = dialog.get_content_area()
        options_card = self._card()
        python_combo = Gtk.ComboBoxText()
        python_combo.append("user", "用户级 Python 3.12（推荐）")
        python_combo.append("project", "项目虚拟环境（开发用）")
        python_combo.append("system", "系统 Python 3.11/3.12")
        python_combo.set_active_id("user")
        preserve = Gtk.CheckButton(label="保留现有 AI 与运行配置（词典和音频始终保留）")
        preserve.set_active(True)
        install_deps = Gtk.CheckButton(label="自动安装缺失的系统依赖（通过 Polkit 授权）")
        install_deps.set_active(True)
        bootstrap_uv = Gtk.CheckButton(label="缺少兼容 Python 时自动安装 uv/Python 3.12 到用户目录")
        bootstrap_uv.set_active(True)
        options_card.pack_start(self._row("Python 环境", control=python_combo), False, False, 0)
        options_card.pack_start(self._row("配置迁移", control=preserve), False, False, 0)
        options_card.pack_start(self._row("系统依赖", "需要权限时桌面会弹出密码/指纹授权框，不会打开终端。", install_deps), False, False, 0)
        options_card.pack_start(self._row("Python 引导", control=bootstrap_uv), False, False, 0)

        rime_enabled = Gtk.CheckButton(label="在 VoCoType IBus 内集成 Rime 拼音")
        rime_schema = Gtk.Entry()
        rime_schema.set_text("luna_pinyin")
        component_mode = Gtk.ComboBoxText()
        component_mode.append("auto", "自动（GNOME/Debian 使用系统 component）")
        component_mode.append("user", "仅用户目录")
        component_mode.append("system", "系统目录（需要 Polkit 授权）")
        component_mode.set_active_id("auto")
        if is_ibus:
            options_card.pack_start(self._row("Rime 集成", "默认关闭；也可继续使用系统中的其他 IBus 输入法。", rime_enabled), False, False, 0)
            options_card.pack_start(self._row("Rime schema", "启用 Rime 时使用，例如 luna_pinyin 或 rime_ice。", rime_schema), False, False, 0)
            options_card.pack_start(self._row("IBus component 位置", control=component_mode), False, False, 0)
        content.pack_start(options_card, False, False, 8)

        notice = Gtk.Label(
            label=(
                "管理员授权由系统 Polkit 代理显示；VoCoType 不读取、记录或保存管理员密码。"
                if polkit_available()
                else "未检测到 pkexec。用户目录步骤仍可运行，但系统依赖和系统 component 无法自动安装。"
            ),
            xalign=0,
        )
        notice.set_line_wrap(True)
        content.pack_start(notice, False, False, 8)

        text_view = Gtk.TextView()
        text_view.set_editable(False)
        text_view.set_monospace(True)
        scroller = Gtk.ScrolledWindow()
        scroller.set_vexpand(True)
        scroller.add(text_view)
        content.pack_start(scroller, True, True, 8)
        buffer = text_view.get_buffer()
        state = {"running": False, "done": False}
        option_widgets = [python_combo, preserve, install_deps, bootstrap_uv, rime_enabled, rime_schema, component_mode]

        def append(line: str) -> None:
            def update() -> bool:
                if not dialog.get_visible():
                    return False
                end_iter = buffer.get_end_iter()
                buffer.insert(end_iter, line + "\n")
                text_view.scroll_to_iter(buffer.get_end_iter(), 0.0, False, 0, 0)
                return False
            GLib.idle_add(update)

        def mark_finished(ok: bool) -> bool:
            state["running"] = False
            state["done"] = True
            close_button.set_sensitive(True)
            start_button.set_sensitive(False)
            self._refresh_install_status()
            self._on_refresh_audio(None)
            if ok:
                close_button.grab_focus()
            return False

        def begin() -> None:
            if state["running"]:
                return
            state["running"] = True
            close_button.set_sensitive(False)
            start_button.set_sensitive(False)
            for widget in option_widgets:
                widget.set_sensitive(False)
            opts = InstallOptions(
                python_choice=python_combo.get_active_id() or "user",
                preserve_config=preserve.get_active(),
                install_system_deps=install_deps.get_active(),
                bootstrap_uv=bootstrap_uv.get_active(),
                rime_enabled=rime_enabled.get_active() if is_ibus else False,
                rime_schema=rime_schema.get_text().strip() or "luna_pinyin",
                component_mode=component_mode.get_active_id() or "auto",
            )

            def work() -> None:
                ok, output = install_or_repair(
                    "ibus" if is_ibus else "fcitx5",
                    options=opts,
                    progress=append,
                )
                append("\n✅ 安装/修复完成" if ok else "\n❌ 安装/修复失败")
                if not ok and not output:
                    append("没有收到安装后端输出")
                GLib.idle_add(mark_finished, ok)

            threading.Thread(target=work, daemon=True).start()

        def on_response(_dialog: Gtk.Dialog, response: int) -> None:
            if response == Gtk.ResponseType.APPLY:
                begin()
            elif response == Gtk.ResponseType.CLOSE and not state["running"]:
                dialog.destroy()

        def prevent_close(_dialog: Gtk.Dialog, _event: Gdk.Event) -> bool:
            return state["running"]

        dialog.connect("response", on_response)
        dialog.connect("delete-event", prevent_close)
        dialog.show_all()

    def _open_uninstall_dialog(self, framework: str) -> None:
        if self._uninstall_dialog is not None and self._uninstall_dialog.get_visible():
            self._uninstall_dialog.present()
            return

        is_ibus = framework == "ibus"
        framework_name = "IBus" if is_ibus else "Fcitx 5"
        dialog = Gtk.Dialog(
            title=f"卸载 {framework_name}",
            transient_for=self,
            modal=True,
        )
        self._uninstall_dialog = dialog
        dialog.connect("destroy", lambda _dialog: setattr(self, "_uninstall_dialog", None))
        dialog.add_button("关闭", Gtk.ResponseType.CLOSE)
        start_button = dialog.add_button("开始卸载", Gtk.ResponseType.APPLY)
        start_button.get_style_context().add_class("destructive-action")
        close_button = dialog.get_widget_for_response(Gtk.ResponseType.CLOSE)
        dialog.set_default_size(820, 560)

        content = dialog.get_content_area()
        options_card = self._card()
        purge_runtime = Gtk.CheckButton(label="同时删除 Python 虚拟环境、模型和运行缓存")
        remove_user_data = Gtk.CheckButton(label="同时删除共享配置、术语和音频设置")
        options_card.pack_start(
            self._row(
                "运行环境",
                "默认只删除程序代码和 integration 文件，保留虚拟环境以便快速重装。",
                purge_runtime,
            ),
            False,
            False,
            0,
        )
        options_card.pack_start(
            self._row(
                "共享用户数据",
                "此选项会影响 IBus 与 Fcitx 5；默认关闭。",
                remove_user_data,
            ),
            False,
            False,
            0,
        )

        remove_system_component = Gtk.CheckButton(
            label="移除旧版安装器写入的系统 IBus component（通过 Polkit 授权）"
        )
        package_command = native_package_removal_command()
        if is_ibus:
            remove_system_component.set_active(package_command is None and polkit_available())
            remove_system_component.set_sensitive(package_command is None and polkit_available())
            options_card.pack_start(
                self._row(
                    "旧版系统 component",
                    "原生软件包管理的文件不会由设置中心直接删除。",
                    remove_system_component,
                ),
                False,
                False,
                0,
            )
        content.pack_start(options_card, False, False, 8)

        warning = (
            f"检测到 vocotype-linux 原生软件包。本操作只清理用户级 {framework_name} 运行环境；"
            f"如需删除 /usr 下的程序，请使用：{package_command}"
            if package_command
            else f"将卸载用户级 {framework_name} integration。默认保留 ~/.config/vocotype。"
        )
        notice = Gtk.Label(label=warning, xalign=0)
        notice.set_line_wrap(True)
        content.pack_start(notice, False, False, 8)

        text_view = Gtk.TextView()
        text_view.set_editable(False)
        text_view.set_monospace(True)
        scroller = Gtk.ScrolledWindow()
        scroller.set_vexpand(True)
        scroller.add(text_view)
        content.pack_start(scroller, True, True, 8)
        buffer = text_view.get_buffer()
        state = {"running": False}
        option_widgets = [purge_runtime, remove_user_data, remove_system_component]

        def append(line: str) -> None:
            def update() -> bool:
                if not dialog.get_visible():
                    return False
                buffer.insert(buffer.get_end_iter(), line + "\n")
                text_view.scroll_to_iter(buffer.get_end_iter(), 0.0, False, 0, 0)
                return False

            GLib.idle_add(update)

        def mark_finished(ok: bool) -> bool:
            state["running"] = False
            close_button.set_sensitive(True)
            start_button.set_sensitive(False)
            self._refresh_install_status()
            if ok:
                close_button.grab_focus()
            return False

        def begin() -> None:
            if state["running"]:
                return
            state["running"] = True
            close_button.set_sensitive(False)
            start_button.set_sensitive(False)
            for widget in option_widgets:
                widget.set_sensitive(False)
            options = UninstallOptions(
                purge_runtime=purge_runtime.get_active(),
                remove_user_data=remove_user_data.get_active(),
                remove_system_component=(
                    remove_system_component.get_active() if is_ibus else False
                ),
            )

            def work() -> None:
                ok, output = uninstall_framework(
                    "ibus" if is_ibus else "fcitx5",
                    options=options,
                    progress=append,
                )
                append("\n✅ 卸载完成" if ok else "\n❌ 卸载失败")
                if not ok and not output:
                    append("没有收到卸载后端输出")
                GLib.idle_add(mark_finished, ok)

            threading.Thread(target=work, daemon=True).start()

        def on_response(_dialog: Gtk.Dialog, response: int) -> None:
            if response == Gtk.ResponseType.APPLY:
                begin()
            elif response == Gtk.ResponseType.CLOSE and not state["running"]:
                dialog.destroy()

        dialog.connect("response", on_response)
        dialog.connect("delete-event", lambda _dialog, _event: state["running"])
        dialog.show_all()

    def _run_quick_action(self, action: Callable[[], tuple[bool, str]]) -> None:
        def work() -> None:
            try:
                ok, message = action()
            except Exception as exc:  # noqa: BLE001
                ok, message = False, str(exc)
            GLib.idle_add(self._message, "操作完成" if ok else "操作失败", message, Gtk.MessageType.INFO if ok else Gtk.MessageType.ERROR)
        threading.Thread(target=work, daemon=True).start()

    def _on_run_doctor(self, _button: Gtk.Button | None = None) -> None:
        self.doctor_summary_label.set_text("正在检查…")

        def work() -> None:
            checks = run_doctor()
            GLib.idle_add(self._render_doctor, checks)

        threading.Thread(target=work, daemon=True).start()

    def _render_doctor(self, checks: list[DoctorCheck]) -> bool:
        self.last_doctor_checks = checks
        for child in self.doctor_list.get_children():
            self.doctor_list.remove(child)
        classes = {"pass": "status-pass", "warn": "status-warn", "fail": "status-fail", "info": "status-pass"}
        icons = {"pass": "✓", "warn": "!", "fail": "✗", "info": "·"}
        for item in checks:
            card = self._card()
            status = Gtk.Label(label=f"{icons.get(item.status, '?')} {item.summary}", xalign=1)
            status.get_style_context().add_class(classes.get(item.status, "status-warn"))
            card.pack_start(self._row(item.title, item.details or item.repair_hint, status), False, False, 0)
            self.doctor_list.pack_start(card, False, False, 0)
        summary = doctor_summary(checks)
        text = f"通过 {summary.get('pass', 0)} · 警告 {summary.get('warn', 0)} · 失败 {summary.get('fail', 0)}"
        self.doctor_summary_label.set_text(text)
        self.overview_summary.set_text(text)
        self.doctor_list.show_all()
        return False

    def _on_export_bundle(self, _button: Gtk.Button) -> None:
        self.doctor_summary_label.set_text("正在生成支持包…")

        def work() -> None:
            try:
                path = create_support_bundle()
                self.last_bundle_path = path
                message = f"已生成：{path}"
            except Exception as exc:  # noqa: BLE001
                message = f"生成失败：{exc}"
            GLib.idle_add(self.doctor_summary_label.set_text, message)

        threading.Thread(target=work, daemon=True).start()

    def _on_feedback(self, _button: Gtk.Button) -> None:
        buffer = self.feedback_view.get_buffer()
        message = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True).strip()
        if not message:
            self.feedback_status.set_text("请先填写反馈内容。")
            return
        endpoint = self.feedback_endpoint.get_text().strip()
        include_doctor = self.feedback_include_doctor.get_active()
        include_bundle = self.feedback_include_bundle.get_active()
        self.feedback_status.set_text("正在准备反馈…")

        def work() -> None:
            try:
                checks = self.last_doctor_checks or (run_doctor() if include_doctor else [])
                doctor_payload = [asdict(item) for item in checks] if include_doctor else None
                doctor_text = "\n".join(f"[{item.status}] {item.title}: {item.summary}" for item in checks)
                bundle = None
                if include_bundle:
                    bundle = create_support_bundle()
                    self.last_bundle_path = bundle
                if endpoint:
                    response = submit_feedback(endpoint, message, doctor_payload=doctor_payload, bundle_path=bundle)
                    status = f"反馈已发送：{json.dumps(response, ensure_ascii=False)[:500]}"
                else:
                    opened = open_github_issue(message, doctor_text=doctor_text)
                    status = "已打开 GitHub issue 页面，请确认后提交。" if opened else "无法打开浏览器。"
            except Exception as exc:  # noqa: BLE001
                status = f"反馈失败：{exc}"
            GLib.idle_add(self.feedback_status.set_text, status)

        threading.Thread(target=work, daemon=True).start()

    def _message(self, title: str, text: str, message_type: Gtk.MessageType = Gtk.MessageType.INFO) -> bool:
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=message_type,
            buttons=Gtk.ButtonsType.OK,
            text=title,
        )
        dialog.format_secondary_text(text or "完成")
        dialog.run()
        dialog.destroy()
        return False


class SettingsApplication(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)

    def do_startup(self) -> None:
        Gtk.Application.do_startup(self)
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        screen = Gdk.Screen.get_default()
        if screen is not None:
            Gtk.StyleContext.add_provider_for_screen(screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def do_activate(self) -> None:
        window = self.props.active_window
        if window is None:
            window = SettingsWindow(self)
        window.show_all()
        window.present()


def main(argv: list[str] | None = None) -> int:
    app = SettingsApplication()
    return int(app.run(argv or []))


if __name__ == "__main__":
    raise SystemExit(main())
