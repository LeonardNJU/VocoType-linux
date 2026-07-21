"""GTK 3 settings, setup, diagnostics, and feedback application."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
from collections import deque
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from vocotype_version import __version__
from app.slm_polisher import looks_like_api_key

from .config_service import (
    ensure_terms_template,
    load_audio_config,
    load_fcitx_module_config,
    load_runtime_config,
    save_audio_config,
    save_fcitx_module_config,
    save_runtime_config,
    terms_path,
    update_runtime_sections,
)
from .doctor import DoctorCheck, doctor_summary, run_doctor
from .install_integrity import probe_installation_integrity
from .feedback import (
    OFFICIAL_FEEDBACK_ENDPOINT,
    build_feedback_payload,
    open_github_issue,
    submit_feedback_payload,
)
from .playground_service import (
    RECORDING_DURATION_SECONDS,
    OutputDevice,
    edit_recording,
    last_recording_path,
    list_input_devices,
    list_output_devices,
    play_recording,
    record_audio,
    slm_config_fingerprint,
    slm_playground_gate,
    transcribe_recording,
)
from .setup_manager import (
    InstallOptions,
    UninstallOptions,
    fcitx_panel_style_support,
    find_project_root,
    install_or_repair,
    installation_paths,
    integration_status,
    native_package_removal_command,
    parse_install_progress,
    polkit_available,
    preferred_installed_framework,
    restart_backend,
    restart_fcitx,
    restart_ibus,
    restart_ibus_backend,
    uninstall_framework,
)
from .support_bundle import create_support_bundle
from .version_check import (
    fetch_release_integrity_manifest,
    query_latest_release,
)

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
window {
  background-color: @theme_bg_color;
  color: @theme_fg_color;
}
headerbar {
  background-color: @theme_bg_color;
  color: @theme_fg_color;
  border-bottom: 1px solid alpha(@theme_fg_color, 0.16);
}
.sidebar {
  background-color: shade(@theme_bg_color, 0.96);
  color: @theme_fg_color;
  border-right: 1px solid alpha(@theme_fg_color, 0.16);
  padding: 12px;
}
.page { padding: 28px 34px; }
.page-title { font-size: 24px; font-weight: 700; color: @theme_fg_color; }
.page-subtitle { font-size: 14px; color: alpha(@theme_fg_color, 0.68); margin-bottom: 14px; }
.section-title { font-size: 17px; font-weight: 700; color: @theme_fg_color; margin-top: 8px; }
.card {
  background-color: @theme_base_color;
  color: @theme_text_color;
  border: 1px solid alpha(@theme_fg_color, 0.16);
  border-radius: 12px;
  padding: 4px;
}
.card-row { padding: 12px 14px; border-bottom: 1px solid alpha(@theme_fg_color, 0.10); }
.card-row:last-child { border-bottom: 0; }
.row-title { font-size: 15px; font-weight: 600; color: @theme_text_color; }
.row-subtitle { font-size: 12px; color: alpha(@theme_text_color, 0.68); }
.status-pass { color: #168b46; font-weight: 600; }
.status-warn { color: #a66a00; font-weight: 600; }
.status-fail { color: #bf2c2c; font-weight: 600; }
.monospace { font-family: monospace; }
.preview {
  background-color: shade(@theme_base_color, 0.96);
  color: @theme_text_color;
  border-radius: 8px;
  padding: 12px;
}
.waveform {
  background-color: shade(@theme_base_color, 0.96);
  border: 1px solid alpha(@theme_fg_color, 0.18);
  border-radius: 8px;
}
.accent {
  background-color: @theme_selected_bg_color;
  color: @theme_selected_fg_color;
  border-radius: 8px;
  padding: 8px 15px;
}
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
        self._last_lifecycle_notice: str | None = None
        self._last_ibus_status = None
        self._last_fcitx_status = None
        self._preferred_framework: str | None = None
        self._slm_health_fingerprint: str | None = None
        cached_recording = last_recording_path()
        self._playground_recording_path: Path | None = (
            cached_recording if cached_recording.is_file() else None
        )
        self._playground_audio_busy = False
        self._playground_ai_busy = False
        self._syncing_audio_controls = False
        self._slm_toggle_guard = False
        self._slm_probe_in_flight = False
        self._loading_values = True
        self._restoring_lifecycle_framework = True
        self._playground_waveform: deque[tuple[float, float]] = deque(maxlen=240)
        self._build_header()
        self._build_layout()
        self._load_values()
        self._loading_values = False
        GLib.idle_add(self._refresh_panel_style_status)

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

        overview_page = self._overview_page()
        general_page = self._recognition_page()
        terms_page = self._terms_page()
        # Build the SLM settings before Playground so the Playground gate can
        # observe the live, unsaved controls as well as saved configuration.
        slm_page = self._slm_page()
        playground_page = self._playground_page()
        self.stack.add_titled(overview_page, "overview", "概览与安装")
        self.stack.add_titled(general_page, "general", "通用设置")
        self.stack.add_titled(playground_page, "playground", "Playground")
        self.stack.add_titled(terms_page, "terms", "用户词典")
        self.stack.add_titled(slm_page, "slm", "AI 功能")
        self.stack.add_titled(self._doctor_page(), "doctor", "诊断")
        self.tutorial_page = self._tutorial_page()
        self.tutorial_page.set_no_show_all(True)
        self.tutorial_page.hide()
        self.stack.add_titled(self.tutorial_page, "tutorial", "教程")
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

    def _text_entry(self, *, width_chars: int = 30) -> Gtk.Entry:
        entry = Gtk.Entry()
        entry.set_width_chars(max(30, int(width_chars)))
        entry.set_max_width_chars(max(60, int(width_chars)))
        entry.set_hexpand(False)
        entry.set_halign(Gtk.Align.END)
        return entry

    def _section_heading(self, title: str, subtitle: str = "") -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        title_label = Gtk.Label(label=title, xalign=0)
        title_label.get_style_context().add_class("section-title")
        box.pack_start(title_label, False, False, 0)
        if subtitle:
            subtitle_label = Gtk.Label(label=subtitle, xalign=0)
            subtitle_label.set_line_wrap(True)
            subtitle_label.get_style_context().add_class("row-subtitle")
            box.pack_start(subtitle_label, False, False, 0)
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
        install_card = self._card()

        summary = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=22)
        summary.set_border_width(14)
        summary.set_hexpand(True)

        environment_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=8
        )
        environment_box.set_hexpand(True)
        environment_title = Gtk.Label(label="安装环境检查", xalign=0)
        environment_title.get_style_context().add_class("row-title")
        self.install_environment_status = Gtk.Label(
            label="正在检查安装环境…", xalign=0
        )
        self.install_environment_status.set_line_wrap(True)
        self.install_environment_status.set_selectable(True)
        environment_box.pack_start(environment_title, False, False, 0)
        environment_box.pack_start(
            self.install_environment_status, False, False, 0
        )

        separator = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)

        framework_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=10
        )
        framework_box.set_hexpand(True)
        framework_title = Gtk.Label(label="选择输入法框架", xalign=0)
        framework_title.get_style_context().add_class("row-title")
        framework_box.pack_start(framework_title, False, False, 0)

        self.ibus_framework_radio = Gtk.RadioButton.new_with_label(
            None, "IBus"
        )
        self.fcitx_framework_radio = Gtk.RadioButton.new_with_label_from_widget(
            self.ibus_framework_radio, "Fcitx 5"
        )
        self.ibus_framework_radio.connect(
            "toggled", self._on_framework_radio_toggled, "ibus"
        )
        self.fcitx_framework_radio.connect(
            "toggled", self._on_framework_radio_toggled, "fcitx5"
        )
        self.ibus_choice_status = Gtk.Label(
            label="正在检查安装状态…", xalign=0
        )
        self.fcitx_choice_status = Gtk.Label(
            label="正在检查安装状态…", xalign=0
        )

        def framework_choice(
            radio: Gtk.RadioButton,
            description: str,
            status: Gtk.Label,
        ) -> Gtk.Widget:
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            radio.set_halign(Gtk.Align.START)
            box.pack_start(radio, False, False, 0)
            description_label = Gtk.Label(label=description, xalign=0)
            description_label.set_line_wrap(True)
            description_label.set_margin_start(28)
            description_label.get_style_context().add_class("row-subtitle")
            status.set_line_wrap(True)
            status.set_margin_start(28)
            box.pack_start(description_label, False, False, 0)
            box.pack_start(status, False, False, 0)
            return box

        framework_box.pack_start(
            framework_choice(
                self.ibus_framework_radio,
                "独立输入法引擎；安装后需要添加并切换到 VoCoType 输入源。",
                self.ibus_choice_status,
            ),
            False,
            False,
            0,
        )
        framework_box.pack_start(
            framework_choice(
                self.fcitx_framework_radio,
                "全局 Module；继续使用现有输入法，无需添加 VoCoType 输入源。",
                self.fcitx_choice_status,
            ),
            False,
            False,
            0,
        )

        summary.pack_start(environment_box, True, True, 0)
        summary.pack_start(separator, False, False, 0)
        summary.pack_start(framework_box, True, True, 0)
        install_card.pack_start(summary, False, True, 0)
        install_card.pack_start(
            Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL),
            False,
            False,
            0,
        )

        lifecycle_stack = Gtk.Stack()
        self.lifecycle_stack = lifecycle_stack
        lifecycle_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        lifecycle_stack.set_transition_duration(120)
        lifecycle_stack.set_hexpand(True)

        def lifecycle_page(
            framework: str,
            title: str,
            restart_backend_action: Callable[[], tuple[bool, str]],
            restart_framework_action: Callable[[], tuple[bool, str]],
        ) -> Gtk.Widget:
            panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            panel.set_border_width(14)
            status = Gtk.Label(
                label=f"正在检查 VoCoType（{title}）…", xalign=0
            )
            status.set_line_wrap(True)
            if framework == "ibus":
                self.ibus_install_status = status
            else:
                self.fcitx_install_status = status

            actions = Gtk.Grid()
            actions.set_row_spacing(8)
            actions.set_column_spacing(8)
            actions.set_column_homogeneous(True)
            actions.set_hexpand(True)

            install_button = Gtk.Button(
                label=f"安装 / 修复 VoCoType（{title}）"
            )
            install_button.get_style_context().add_class("suggested-action")
            install_button.connect(
                "clicked", lambda _button: self._open_install_dialog(framework)
            )
            uninstall_button = Gtk.Button(
                label=f"卸载 VoCoType（{title}）"
            )
            uninstall_button.connect(
                "clicked",
                lambda _button: self._open_uninstall_dialog(framework),
            )
            backend_button = Gtk.Button(label="重启 VoCoType 后台")
            backend_button.connect(
                "clicked",
                lambda _button: self._run_quick_action(
                    restart_backend_action
                ),
            )
            framework_button = Gtk.Button(label=f"重启 {title}")
            framework_button.connect(
                "clicked",
                lambda _button: self._run_quick_action(
                    restart_framework_action
                ),
            )
            for button in (
                install_button,
                uninstall_button,
                backend_button,
                framework_button,
            ):
                button.set_hexpand(True)
                button.set_halign(Gtk.Align.FILL)
            actions.attach(install_button, 0, 0, 1, 1)
            actions.attach(uninstall_button, 1, 0, 1, 1)
            actions.attach(backend_button, 0, 1, 1, 1)
            actions.attach(framework_button, 1, 1, 1, 1)
            panel.pack_start(status, False, False, 0)
            panel.pack_start(actions, False, False, 0)
            return panel

        lifecycle_stack.add_titled(
            lifecycle_page("ibus", "IBus", restart_ibus_backend, restart_ibus),
            "ibus",
            "IBus",
        )
        lifecycle_stack.add_titled(
            lifecycle_page(
                "fcitx5", "Fcitx 5", restart_backend, restart_fcitx
            ),
            "fcitx5",
            "Fcitx 5",
        )
        install_card.pack_start(lifecycle_stack, False, True, 0)
        content.pack_start(install_card, False, False, 0)

        ui_config = self.runtime_config.get("ui")
        saved_framework = (
            str(ui_config.get("lifecycle_framework", "")).strip().lower()
            if isinstance(ui_config, dict)
            else ""
        )
        if saved_framework not in {"ibus", "fcitx5"}:
            saved_framework = (
                "fcitx5"
                if "fcitx" in os.environ.get("XMODIFIERS", "").casefold()
                else "ibus"
            )
        GLib.idle_add(self._restore_lifecycle_framework, saved_framework)

        doctor_card = self._card()
        doctor_actions = Gtk.Box(spacing=8)
        doctor_button = Gtk.Button(label="运行快速检查")
        doctor_button.connect("clicked", self._on_run_doctor)
        open_doctor_button = Gtk.Button(label="查看详情")
        open_doctor_button.connect(
            "clicked",
            lambda _button: self.stack.set_visible_child_name("doctor"),
        )
        doctor_actions.pack_start(doctor_button, False, False, 0)
        doctor_actions.pack_start(open_doctor_button, False, False, 0)
        self.overview_summary = Gtk.Label(label="尚未运行检查", xalign=0)
        self.overview_summary.set_line_wrap(True)
        doctor_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        doctor_panel.pack_start(doctor_actions, False, False, 0)
        doctor_panel.pack_start(self.overview_summary, False, False, 0)
        doctor_card.pack_start(
            self._row(
                "运行状态",
                "快速检查后仅显示摘要；点击“查看详情”进入诊断页查看逐项结果与修复建议。",
                doctor_panel,
            ),
            False,
            False,
            0,
        )
        content.pack_start(doctor_card, False, False, 0)
        GLib.idle_add(self._refresh_install_status)
        return page

    def _selected_framework(self) -> str:
        if self.fcitx_framework_radio.get_active():
            return "fcitx5"
        return "ibus"

    def _restore_lifecycle_framework(self, framework: str) -> bool:
        radio = (
            self.fcitx_framework_radio
            if framework == "fcitx5"
            else self.ibus_framework_radio
        )
        radio.set_active(True)
        self.lifecycle_stack.set_visible_child_name(framework)
        GLib.idle_add(self._finish_lifecycle_framework_restore)
        return False

    def _finish_lifecycle_framework_restore(self) -> bool:
        self._restoring_lifecycle_framework = False
        self._update_framework_specific_content()
        return False

    def _on_framework_radio_toggled(
        self, button: Gtk.RadioButton, framework: str
    ) -> None:
        if not button.get_active():
            return
        self.lifecycle_stack.set_visible_child_name(framework)
        if self._loading_values or self._restoring_lifecycle_framework:
            return
        try:
            self.runtime_config = update_runtime_sections(
                {"ui": {"lifecycle_framework": framework}}
            )
        except Exception as exc:  # noqa: BLE001
            self._last_lifecycle_notice = (
                f"⚠️ 无法保存上次使用的框架：{exc}"
            )
        self._update_framework_specific_content()

    def _update_framework_specific_content(self) -> None:
        if self._last_ibus_status is None or self._last_fcitx_status is None:
            return
        preferred = preferred_installed_framework(
            self._last_ibus_status,
            self._last_fcitx_status,
            self._selected_framework(),
        )
        self._preferred_framework = preferred
        if not hasattr(self, "tutorial_page"):
            return
        if preferred is None:
            if self.stack.get_visible_child_name() == "tutorial":
                self.stack.set_visible_child_name("overview")
            self.tutorial_page.set_no_show_all(True)
            self.tutorial_page.hide()
            return
        self.tutorial_page.set_no_show_all(False)
        self.tutorial_page.show_all()
        self.tutorial_framework_stack.set_visible_child_name(preferred)
        if preferred == "ibus":
            self.tutorial_framework_title.set_text(
                "当前教程：IBus（独立输入法引擎）"
            )
        else:
            self.tutorial_framework_title.set_text(
                "当前教程：Fcitx 5（全局 Module）"
            )
        self.tutorial_page.show()

    def _recognition_page(self) -> Gtk.Widget:
        page, content = self._page(
            "通用设置",
            "集中配置麦克风、F9 状态样式、实时识别预览与 ITN。Playground 中的麦克风控件与这里双向同步。",
        )

        content.pack_start(
            self._section_heading(
                "麦克风与采样率",
                "这组设置同时供 F9、IBus、Fcitx 5 与 Playground 使用。",
            ),
            False,
            False,
            0,
        )
        audio_card = self._card()
        self.audio_device = Gtk.ComboBoxText()
        self.audio_device.set_hexpand(True)
        self.audio_device.connect("changed", self._on_audio_device_changed)
        self.audio_sample_rate = Gtk.SpinButton.new_with_range(8000, 192000, 1000)
        self.audio_sample_rate.set_value(44100)
        self.audio_sample_rate.connect(
            "value-changed", self._on_audio_sample_rate_changed
        )
        self.min_recording_ms = Gtk.SpinButton.new_with_range(0, 5000, 100)
        self.min_recording_ms.set_value(1000)
        self.min_recording_ms.set_numeric(True)
        refresh_audio = Gtk.Button(label="刷新音频设备")
        refresh_audio.connect("clicked", self._on_refresh_audio)
        self.audio_status = Gtk.Label(label="尚未枚举音频设备", xalign=0)
        self.audio_status.set_line_wrap(True)
        audio_card.pack_start(
            self._row(
                "输入设备",
                "选择后会同步到 Playground；成功录音时保存为 F9 使用设备。",
                self.audio_device,
            ),
            False,
            False,
            0,
        )
        audio_card.pack_start(
            self._row(
                "原生采样率",
                "按设备原生采样率采集；ASR 会在内部重采样到模型需要的采样率。",
                self.audio_sample_rate,
            ),
            False,
            False,
            0,
        )
        audio_card.pack_start(
            self._row(
                "最短有效录音（毫秒）",
                "不足此时长的 F9 / Shift+F9 / Ctrl+F9 录音会直接丢弃，不进入 ASR；默认 1000 ms，0 表示关闭限制。",
                self.min_recording_ms,
            ),
            False,
            False,
            0,
        )
        audio_actions = Gtk.Box(spacing=8)
        audio_actions.pack_start(refresh_audio, False, False, 0)
        audio_actions.pack_start(self.audio_status, True, True, 0)
        audio_card.pack_start(
            self._row("设备状态", control=audio_actions), False, False, 0
        )
        content.pack_start(audio_card, False, False, 0)

        content.pack_start(
            self._section_heading(
                "F9 交互样式",
                "Fcitx 5 与 IBus 使用完全一致的第一行状态与第二行实时预览。",
            ),
            False,
            False,
            0,
        )
        panel_card = self._card()
        self.panel_style = Gtk.ComboBoxText()
        self.panel_style.append("minimal", "极简：🎤 录音中 / ⏳ 识别中")
        self.panel_style.append("animated", "动画：正在听状态动画")
        self.panel_style.set_active_id("minimal")
        self.panel_style.connect("changed", self._on_panel_style_changed)
        self.panel_style_status = Gtk.Label(xalign=0)
        self.panel_style_status.set_line_wrap(True)
        panel_control = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        panel_control.pack_start(self.panel_style, False, False, 0)
        panel_control.pack_start(self.panel_style_status, False, False, 0)
        panel_card.pack_start(
            self._row(
                "状态样式",
                "极简模式保持“录音中”；动画模式保持“正在听”动画。流式 partial 始终显示在第二行。",
                panel_control,
            ),
            False,
            False,
            0,
        )
        content.pack_start(panel_card, False, False, 0)

        content.pack_start(
            self._section_heading(
                "实时识别预览（2-pass）",
                "在线模型只负责录音期间的第二行预览；松键后仍由原高精度离线模型给出最终结果。",
            ),
            False,
            False,
            0,
        )
        streaming_card = self._card()
        self.asr_streaming_enabled = self._switch()
        streaming_card.pack_start(
            self._row(
                "启用实时识别预览",
                "可选功能，需要另行安装 native streaming runtime。首次使用会按需加载约 238 MB 官方在线模型；本地 native worker 空闲后自动退出并释放内存。",
                self.asr_streaming_enabled,
            ),
            False,
            False,
            0,
        )
        content.pack_start(streaming_card, False, False, 0)

        content.pack_start(
            self._section_heading(
                "数字与格式（ITN）",
                "控制识别后的数字、日期、时间、距离和金额格式。",
            ),
            False,
            False,
            0,
        )
        itn_card = self._card()
        self.itn_enabled = self._switch()
        self.compact_dates = self._switch()
        self.compact_times = self._switch()
        self.compact_distances = self._switch()
        self.currency_symbols = self._switch()
        itn_card.pack_start(self._row("启用数字与 ITN", "关闭后保留用户词典替换，但不改写中文数字。", self.itn_enabled), False, False, 0)
        itn_card.pack_start(self._row("紧凑日期", "例如：二零二六年五月十一号 → 2026/05/11", self.compact_dates), False, False, 0)
        itn_card.pack_start(self._row("24 小时时间", "例如：下午三点二十分 → 15:20", self.compact_times), False, False, 0)
        itn_card.pack_start(self._row("路程单位缩写", "例如：三百二十米 → 320m", self.compact_distances), False, False, 0)
        itn_card.pack_start(self._row("金额符号", "例如：一百二十八元 → ¥128", self.currency_symbols), False, False, 0)
        content.pack_start(itn_card, False, False, 0)

        preview_card = self._card()
        self.preview_input = self._text_entry()
        self.preview_input.set_text("二零二六年五月十一号下午三点二十分跑了三百二十米，花了一百二十八元")
        self.preview_input.connect("activate", self._on_preview)
        preview_button = Gtk.Button(label="生成 ITN 预览")
        preview_button.connect("clicked", self._on_preview)
        input_box = Gtk.Box(spacing=8)
        input_box.pack_start(self.preview_input, True, True, 0)
        input_box.pack_start(preview_button, False, False, 0)
        self.preview_output = Gtk.Label(label="点击“生成 ITN 预览”查看结果", xalign=0)
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
            "AI 润色与语音编辑",
            "配置本地或 OpenAI-compatible 远程服务。Shift+F9 用于润色；Ctrl+F9 的所有指令理解、同音词消歧与导航规划也统一由该模型完成。",
        )
        card = self._card()
        self.slm_enabled = self._switch()
        self.slm_remote_stream = self._switch()
        self.slm_thinking = self._switch()
        self.slm_provider = Gtk.ComboBoxText()
        self.slm_provider.append("remote", "远程 OpenAI-compatible")
        self.slm_provider.append("local_ephemeral", "本地按需模型")
        self.slm_endpoint = self._text_entry()
        self.slm_model = self._text_entry()
        self.slm_api_key_env = self._text_entry()
        self.slm_api_key_env.set_placeholder_text("例如 DEEPSEEK_API_KEY（这里只填变量名）")
        self.slm_api_key = self._text_entry()
        self.slm_api_key.set_visibility(False)
        self.slm_api_key.set_placeholder_text("直接粘贴 sk-...；留空则保留现有凭据")
        self.slm_clear_api_key = Gtk.CheckButton(label="清除已保存的直接 API Key")
        self.slm_min_chars = Gtk.SpinButton.new_with_range(0, 2000, 1)
        self.slm_timeout = Gtk.SpinButton.new_with_range(1000, 120000, 1000)
        card.pack_start(
            self._row(
                "启用 AI 功能",
                "F9 始终直接输出；Shift+F9 调用润色；Ctrl+F9 必须通过模型结合 surrounding context 生成安全编辑计划。填好模型、端点与凭据后再打开；切换到 ON 时会自动真实测活。",
                self.slm_enabled,
            ),
            False,
            False,
            0,
        )
        card.pack_start(self._row("Provider", control=self.slm_provider), False, False, 0)
        card.pack_start(self._row("API 地址", "可填写服务根地址或 /v1/chat/completions。", self.slm_endpoint), False, False, 0)
        card.pack_start(self._row("模型", control=self.slm_model), False, False, 0)
        card.pack_start(self._row("API Key 环境变量名（高级）", "这里只填写 DEEPSEEK_API_KEY 这类变量名，不要粘贴 sk-... 密钥。", self.slm_api_key_env), False, False, 0)
        card.pack_start(self._row("直接 API Key", "把 sk-... 密钥粘贴在这里；配置文件权限固定为 0600。", self.slm_api_key), False, False, 0)
        card.pack_start(self._row("清除直接凭据", "切换到环境变量凭据时可清除旧值。", self.slm_clear_api_key), False, False, 0)
        card.pack_start(self._row("最少润色字符数", "0 表示不限制。", self.slm_min_chars), False, False, 0)
        card.pack_start(self._row("流式空闲超时（毫秒）", control=self.slm_timeout), False, False, 0)
        card.pack_start(self._row("远程流式输出", "Fcitx 候选框实时显示可见增量。", self.slm_remote_stream), False, False, 0)
        card.pack_start(self._row("允许 reasoning/thinking", "思考内容不会进入最终提交。", self.slm_thinking), False, False, 0)
        content.pack_start(card, False, False, 0)
        actions = Gtk.Box(spacing=8)
        test_button = Gtk.Button(label="测活 AI 端点 / 模型")
        test_button.connect("clicked", self._on_test_slm)
        actions.pack_start(test_button, False, False, 0)
        self.slm_test_status = Gtk.Label(xalign=0)
        self.slm_test_status.set_line_wrap(True)
        actions.pack_start(self.slm_test_status, True, True, 0)
        content.pack_start(actions, False, False, 0)

        self.slm_enabled.connect(
            "notify::active", self._on_slm_enabled_changed
        )
        for widget, signal in (
            (self.slm_provider, "changed"),
            (self.slm_endpoint, "changed"),
            (self.slm_model, "changed"),
            (self.slm_api_key_env, "changed"),
            (self.slm_api_key, "changed"),
            (self.slm_clear_api_key, "toggled"),
        ):
            widget.connect(signal, self._on_slm_config_changed)
        return page

    def _playground_page(self) -> Gtk.Widget:
        page, content = self._page(
            "Playground",
            "依次测试麦克风、真实转录、AI 润色与语音编辑。输入设备和采样率与“通用设置”双向同步。",
        )

        content.pack_start(
            self._section_heading("1. 测试麦克风", "录音、查看波形，并从指定输出设备回放。"),
            False,
            False,
            0,
        )
        audio_card = self._card()
        self.playground_audio_device = Gtk.ComboBoxText()
        self.playground_audio_device.set_hexpand(True)
        self.playground_audio_device.connect(
            "changed", self._on_playground_audio_device_changed
        )
        self.playground_audio_sample_rate = Gtk.SpinButton.new_with_range(
            8000, 192000, 1000
        )
        self.playground_audio_sample_rate.set_value(44100)
        self.playground_audio_sample_rate.connect(
            "value-changed", self._on_playground_audio_sample_rate_changed
        )
        self.audio_output = Gtk.ComboBoxText()
        self.audio_output.set_hexpand(True)
        self.audio_output.connect("changed", self._on_audio_output_changed)
        self.playground_audio_status = Gtk.Label(
            label="尚未枚举音频设备", xalign=0
        )
        self.playground_audio_status.set_line_wrap(True)
        self.playground_waveform = Gtk.DrawingArea()
        self.playground_waveform.set_size_request(-1, 110)
        self.playground_waveform.get_style_context().add_class("waveform")
        self.playground_waveform.connect("draw", self._draw_playground_waveform)
        self.playground_waveform.set_hexpand(True)
        self.playground_waveform.set_vexpand(False)
        self.playground_waveform.set_valign(Gtk.Align.FILL)

        self.playground_refresh_audio_button = Gtk.Button(label="刷新设备")
        self.playground_refresh_audio_button.connect("clicked", self._on_refresh_audio)
        self.playground_record_button = Gtk.Button(
            label=f"录音 {int(RECORDING_DURATION_SECONDS)} 秒"
        )
        self.playground_record_button.get_style_context().add_class("suggested-action")
        self.playground_record_button.connect("clicked", self._on_playground_record)
        self.playground_play_button = Gtk.Button(label="回放上次录音")
        self.playground_play_button.connect("clicked", self._on_playground_play)
        audio_actions = Gtk.Box(spacing=8)
        audio_actions.pack_start(self.playground_refresh_audio_button, False, False, 0)
        audio_actions.pack_start(self.playground_record_button, False, False, 0)
        audio_actions.pack_start(self.playground_play_button, False, False, 0)

        audio_card.pack_start(
            self._row(
                "输入设备",
                "与通用设置同步；成功录音后保存为 F9 使用设备。",
                self.playground_audio_device,
            ),
            False,
            False,
            0,
        )
        audio_card.pack_start(
            self._row(
                "采样率",
                "与通用设置同步。",
                self.playground_audio_sample_rate,
            ),
            False,
            False,
            0,
        )
        audio_card.pack_start(
            self._row(
                "输出设备",
                "回放会明确发送到所选 PipeWire/PulseAudio sink。",
                self.audio_output,
            ),
            False,
            False,
            0,
        )
        audio_card.pack_start(
            self._row("录音与回放", control=audio_actions), False, False, 0
        )
        waveform_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        waveform_section.get_style_context().add_class("card-row")
        waveform_title = Gtk.Label(label="实时波形", xalign=0)
        waveform_title.get_style_context().add_class("row-title")
        waveform_subtitle = Gtk.Label(
            label="录音期间滚动显示并自动放大；原始 WAV 不会被修改。",
            xalign=0,
        )
        waveform_subtitle.set_line_wrap(True)
        waveform_subtitle.get_style_context().add_class("row-subtitle")
        waveform_section.pack_start(waveform_title, False, False, 0)
        waveform_section.pack_start(waveform_subtitle, False, False, 0)
        waveform_section.pack_start(self.playground_waveform, False, True, 0)
        audio_card.pack_start(waveform_section, False, False, 0)
        audio_card.pack_start(
            self._row("状态", control=self.playground_audio_status),
            False,
            False,
            0,
        )
        content.pack_start(audio_card, False, False, 0)

        content.pack_start(
            self._section_heading("2. 测试转录", "调用当前已安装的真实 ASR 后台。"),
            False,
            False,
            0,
        )
        asr_card = self._card()
        self.playground_transcribe_button = Gtk.Button(label="转录上次录音")
        self.playground_transcribe_button.connect(
            "clicked", self._on_playground_transcribe
        )
        self.playground_transcribe_status = Gtk.Label(
            label="录音完成后可调用当前 VoCoType ASR 后台检查识别内容。",
            xalign=0,
        )
        self.playground_transcribe_status.set_line_wrap(True)
        asr_actions = Gtk.Box(spacing=8)
        asr_actions.pack_start(self.playground_transcribe_button, False, False, 0)
        asr_actions.pack_start(self.playground_transcribe_status, True, True, 0)
        self.playground_transcript_view = Gtk.TextView()
        self.playground_transcript_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.playground_transcript_view.set_editable(True)
        self.playground_transcript_view.get_buffer().set_text(
            "转录结果会显示在这里；你可以直接编辑以对照实际口述。"
        )
        transcript_scroll = Gtk.ScrolledWindow()
        transcript_scroll.set_min_content_height(110)
        transcript_scroll.add(self.playground_transcript_view)
        asr_card.pack_start(
            self._row("真实 ASR 转录", control=asr_actions), False, False, 0
        )
        asr_card.pack_start(transcript_scroll, False, False, 12)
        content.pack_start(asr_card, False, False, 0)

        self.playground_ai_gate_status = Gtk.Label(xalign=0)
        self.playground_ai_gate_status.set_line_wrap(True)
        content.pack_start(self.playground_ai_gate_status, False, False, 0)
        self.playground_ai_controls = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=16
        )
        self.playground_ai_controls.set_sensitive(False)

        self.playground_ai_controls.pack_start(
            self._section_heading(
                "3. 测试 AI 润色",
                "使用已启用且已测活的当前模型整理文本，不带编辑指令。",
            ),
            False,
            False,
            0,
        )
        polish_card = self._card()
        self.playground_polish_source = Gtk.TextView()
        self.playground_polish_source.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.playground_polish_source.get_buffer().set_text(
            "这是一段有一点啰嗦而且表达不够自然的测试文本，希望 AI 帮我整理得更清楚。"
        )
        polish_source_scroll = Gtk.ScrolledWindow()
        polish_source_scroll.set_min_content_height(100)
        polish_source_scroll.add(self.playground_polish_source)
        self.playground_polish_button = Gtk.Button(label="测试 AI 润色")
        self.playground_polish_button.connect("clicked", self._on_playground_polish)
        self.playground_polish_status = Gtk.Label(xalign=0)
        self.playground_polish_status.set_line_wrap(True)
        polish_actions = Gtk.Box(spacing=8)
        polish_actions.pack_start(self.playground_polish_button, False, False, 0)
        polish_actions.pack_start(self.playground_polish_status, True, True, 0)
        self.playground_polish_result = Gtk.TextView()
        self.playground_polish_result.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.playground_polish_result.set_editable(True)
        self.playground_polish_result.get_buffer().set_text(
            "AI 润色结果会显示在这里。"
        )
        polish_result_scroll = Gtk.ScrolledWindow()
        polish_result_scroll.set_min_content_height(110)
        polish_result_scroll.add(self.playground_polish_result)
        polish_card.pack_start(Gtk.Label(label="待润色文本", xalign=0), False, False, 12)
        polish_card.pack_start(polish_source_scroll, False, False, 12)
        polish_card.pack_start(polish_actions, False, False, 12)
        polish_card.pack_start(Gtk.Label(label="润色结果", xalign=0), False, False, 12)
        polish_card.pack_start(polish_result_scroll, False, False, 12)
        self.playground_ai_controls.pack_start(polish_card, False, False, 0)

        self.playground_ai_controls.pack_start(
            self._section_heading(
                "4. 测试语音编辑",
                "所有指令都会把 ASR 结果、上下文、光标和选区交给已测活的模型生成受限编辑计划；可验证同音词替换、翻译、LaTeX 与文本生成。",
            ),
            False,
            False,
            0,
        )
        edit_card = self._card()
        self.playground_edit_source = Gtk.TextView()
        self.playground_edit_source.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.playground_edit_source.get_buffer().set_text(
            "A 是旧版本标记，后文仍然引用 A。"
        )
        edit_source_scroll = Gtk.ScrolledWindow()
        edit_source_scroll.set_min_content_height(110)
        edit_source_scroll.add(self.playground_edit_source)
        self.playground_ai_instruction = self._text_entry()
        self.playground_ai_instruction.set_text("把 A 替换成 B")
        examples = Gtk.Box(spacing=6)
        for label, key in (
            ("把 A 替换成 B", "replace"),
            ("翻译成英语", "translate"),
            ("转成 LaTeX", "latex"),
            ("写一条好评", "review"),
        ):
            button = Gtk.Button(label=label)
            button.connect("clicked", self._on_playground_edit_example, key)
            examples.pack_start(button, False, False, 0)
        self.playground_edit_button = Gtk.Button(
            label=f"录制语音指令 {int(RECORDING_DURATION_SECONDS)} 秒并测试"
        )
        self.playground_edit_button.connect("clicked", self._on_playground_edit)
        self.playground_edit_status = Gtk.Label(xalign=0)
        self.playground_edit_status.set_line_wrap(True)
        edit_actions = Gtk.Box(spacing=8)
        edit_actions.pack_start(self.playground_edit_button, False, False, 0)
        edit_actions.pack_start(self.playground_edit_status, True, True, 0)
        self.playground_edit_result = Gtk.TextView()
        self.playground_edit_result.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.playground_edit_result.set_editable(True)
        self.playground_edit_result.get_buffer().set_text(
            "语音编辑结果会显示在这里。"
        )
        edit_result_scroll = Gtk.ScrolledWindow()
        edit_result_scroll.set_min_content_height(120)
        edit_result_scroll.add(self.playground_edit_result)
        edit_card.pack_start(Gtk.Label(label="上下文文本", xalign=0), False, False, 12)
        edit_card.pack_start(edit_source_scroll, False, False, 12)
        edit_card.pack_start(
            self._row(
                "请对麦克风说",
                "此文本仅作为口述提示；真正的指令来自麦克风录音与 ASR。",
                self.playground_ai_instruction,
            ),
            False,
            False,
            0,
        )
        edit_card.pack_start(self._row("范例", control=examples), False, False, 0)
        edit_card.pack_start(edit_actions, False, False, 12)
        edit_card.pack_start(Gtk.Label(label="编辑结果", xalign=0), False, False, 12)
        edit_card.pack_start(edit_result_scroll, False, False, 12)
        self.playground_ai_controls.pack_start(edit_card, False, False, 0)
        content.pack_start(self.playground_ai_controls, False, False, 0)

        # Backward-compatible aliases for helper code that treats AI controls
        # as one unit; the visible UI now keeps polish and edit independent.
        self.playground_ai_source = self.playground_polish_source
        self.playground_ai_result = self.playground_polish_result
        self.playground_ai_status = self.playground_polish_status

        self._update_playground_recording_actions()
        GLib.idle_add(self._update_playground_slm_gate)
        return page

    def _doctor_page(self) -> Gtk.Widget:
        page, content = self._page(
            "诊断",
            "自动检查常见安装与运行问题。仍无法解决时，可生成不含录音和凭据的支持包。",
        )
        version_card = self._card()
        self.doctor_version_status = Gtk.Label(
            label=f"当前版本：{__version__}；尚未查询 GitHub 最新 release。",
            xalign=0,
        )
        self.doctor_version_status.set_line_wrap(True)
        latest_button = Gtk.Button(label="查询 GitHub 最新版本")
        latest_button.connect("clicked", self._on_check_latest_version)
        version_card.pack_start(
            self._row(
                "版本与远程完整性",
                "查询 release 版本；若该版本提供完整性清单，同时对关键安装文件执行 SHA-256 对照。",
                latest_button,
            ),
            False,
            False,
            0,
        )
        version_card.pack_start(
            self._row("查询结果", control=self.doctor_version_status),
            False,
            False,
            0,
        )
        content.pack_start(version_card, False, False, 0)

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
        page, content = self._page(
            "教程",
            "教程会根据当前已安装的集成和“概览与安装”中的框架选择自动切换。",
        )
        self.tutorial_framework_title = Gtk.Label(xalign=0)
        self.tutorial_framework_title.get_style_context().add_class(
            "section-title"
        )
        content.pack_start(
            self.tutorial_framework_title, False, False, 0
        )

        tutorial_stack = Gtk.Stack()
        tutorial_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        tutorial_stack.set_transition_duration(120)
        tutorial_stack.set_hexpand(True)
        self.tutorial_framework_stack = tutorial_stack

        def tutorial_panel(steps: list[tuple[str, str]]) -> Gtk.Widget:
            card = self._card()
            for title, description in steps:
                card.pack_start(
                    self._row(title, description), False, False, 0
                )
            return card

        ibus_steps = [
            (
                "1. 安装 IBus 集成",
                "在“概览与安装”选择 IBus，执行安装 / 修复，并按提示重启 IBus。",
            ),
            (
                "2. 添加并切换输入法",
                "在系统“键盘 / 输入源”中添加 VoCoType，然后切换到 VoCoType 输入法；IBus 版本不是全局插件。",
            ),
            (
                "3. 选择文本输入方案",
                "可以启用 VoCoType 内置 Rime，也可以只把它作为语音输入引擎使用。",
            ),
            (
                "4. Playground 验证",
                "先测试麦克风、回放与真实 ASR；AI 润色需先完成端点 / 模型测活。",
            ),
            (
                "5. 使用语音功能",
                "按住 F9 普通识别，Shift+F9 润色，Ctrl+F9 语音编辑；语音编辑依赖应用提供 surrounding text。",
            ),
            (
                "6. 排障",
                "F9 无响应时运行 Doctor，重点检查 IBus component、引擎进程、安装哈希与输入源是否正确。",
            ),
        ]
        fcitx_steps = [
            (
                "1. 安装 Fcitx 5 集成",
                "在“概览与安装”选择 Fcitx 5，执行安装 / 修复，并按提示重启 Fcitx 5。",
            ),
            (
                "2. 不要添加 VoCoType 输入法",
                "VoCoType 是全局 Module。继续使用雾凇拼音、Rime、Mozc 或其他现有输入法，直接按 F9 即可。",
            ),
            (
                "3. Playground 验证",
                "先测试麦克风、回放与真实 ASR；AI 润色需先完成端点 / 模型测活。",
            ),
            (
                "4. 使用语音功能",
                "按住 F9 普通识别，Shift+F9 润色，Ctrl+F9 语音编辑；语音编辑依赖应用提供 surrounding text。",
            ),
            (
                "5. 添加术语",
                "在用户词典中加入项目名、人名和专业术语；hotword 提高识别概率，aliases 统一最终拼写。",
            ),
            (
                "6. 排障",
                "F9 无响应时运行 Doctor，重点检查全局 Module、后台服务、安装哈希与旧版残留。",
            ),
        ]
        tutorial_stack.add_titled(
            tutorial_panel(ibus_steps), "ibus", "IBus"
        )
        tutorial_stack.add_titled(
            tutorial_panel(fcitx_steps), "fcitx5", "Fcitx 5"
        )
        content.pack_start(tutorial_stack, False, True, 0)
        return page

    def _feedback_page(self) -> Gtk.Widget:
        page, content = self._page(
            "反馈",
            "可直接发送给 VoCoType 维护者，也可以创建公开 GitHub Issue。发送前会展示完整上传内容。",
        )

        form_card = self._card()
        self.feedback_category = Gtk.ComboBoxText()
        for category_id, label in (
            ("bug", "问题 / Bug"),
            ("installation", "安装与升级"),
            ("compatibility", "兼容性"),
            ("usability", "易用性"),
            ("feature", "功能建议"),
            ("other", "其他"),
        ):
            self.feedback_category.append(category_id, label)
        self.feedback_category.set_active_id("bug")
        form_card.pack_start(
            self._row("反馈类型", "用于维护者分类和合并重复报告。", self.feedback_category),
            False,
            False,
            0,
        )

        self.feedback_contact = self._text_entry()
        self.feedback_contact.set_placeholder_text("可选：邮箱或 GitHub 用户名")
        form_card.pack_start(
            self._row(
                "联系方式",
                "不填写也可匿名提交；不填写时维护者无法追问复现细节。",
                self.feedback_contact,
            ),
            False,
            False,
            0,
        )

        self.feedback_view = Gtk.TextView()
        self.feedback_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        feedback_scroll = Gtk.ScrolledWindow()
        feedback_scroll.set_min_content_height(200)
        feedback_scroll.add(self.feedback_view)
        message_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        message_box.set_border_width(14)
        message_title = Gtk.Label(label="反馈内容", xalign=0)
        message_title.get_style_context().add_class("row-title")
        message_hint = Gtk.Label(
            label="请写明发生了什么、如何复现，以及你期望的结果。最多 10,000 字。",
            xalign=0,
        )
        message_hint.set_line_wrap(True)
        message_hint.get_style_context().add_class("row-subtitle")
        message_box.pack_start(message_title, False, False, 0)
        message_box.pack_start(message_hint, False, False, 0)
        message_box.pack_start(feedback_scroll, False, False, 0)
        form_card.pack_start(message_box, False, False, 0)
        content.pack_start(form_card, False, False, 0)

        privacy_card = self._card()
        self.feedback_include_doctor = Gtk.CheckButton(label="附带 Doctor 结果")
        self.feedback_include_bundle = Gtk.CheckButton(
            label="附带脱敏支持包（最大 5 MiB，默认关闭）"
        )
        privacy_card.pack_start(
            self._row(
                "诊断信息",
                "Doctor 和支持包都不会自动附带。支持包不含原始录音、API Key 或词典正文，但仍应在预览中检查。",
                self.feedback_include_doctor,
            ),
            False,
            False,
            0,
        )
        privacy_card.pack_start(
            self._row(
                "支持包",
                "包含脱敏配置、Doctor、服务日志与 Fcitx 诊断；服务器附件默认私有保存。",
                self.feedback_include_bundle,
            ),
            False,
            False,
            0,
        )
        content.pack_start(privacy_card, False, False, 0)

        advanced = Gtk.Expander(label="高级：使用自托管反馈服务器")
        advanced_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        advanced_box.set_border_width(12)
        self.feedback_use_custom_endpoint = Gtk.CheckButton(label="启用自定义端点")
        self.feedback_endpoint = self._text_entry()
        self.feedback_endpoint.set_placeholder_text("https://example.org/v1/feedback")
        self.feedback_use_custom_endpoint.connect(
            "toggled",
            lambda button: self.feedback_endpoint.set_sensitive(button.get_active()),
        )
        advanced_box.pack_start(self.feedback_use_custom_endpoint, False, False, 0)
        advanced_box.pack_start(
            self._row(
                "自定义端点",
                "仅供企业、发行版或 fork 使用；普通用户应使用官方端点。",
                self.feedback_endpoint,
            ),
            False,
            False,
            0,
        )
        advanced.add(advanced_box)
        content.pack_start(advanced, False, False, 0)

        actions = Gtk.Box(spacing=8)
        submit = Gtk.Button(label="发送给 VoCoType 维护者")
        submit.get_style_context().add_class("suggested-action")
        submit.connect("clicked", self._on_feedback)
        github = Gtk.Button(label="在 GitHub 创建公开 Issue")
        github.connect("clicked", self._on_feedback_github)
        actions.pack_start(submit, False, False, 0)
        actions.pack_start(github, False, False, 0)
        self.feedback_status = Gtk.Label(xalign=0)
        self.feedback_status.set_line_wrap(True)
        actions.pack_start(self.feedback_status, True, True, 0)
        content.pack_start(actions, False, False, 0)
        return page

    def _load_values(self) -> None:
        normalization = self.runtime_config.get("normalization", {})
        asr_streaming = self.runtime_config.get("asr_streaming", {})
        slm = self.runtime_config.get("slm", {})
        ui = self.runtime_config.get("ui", {})
        if not isinstance(ui, dict):
            ui = {}
        feedback = self.runtime_config.get("feedback", {})
        audio_runtime = self.runtime_config.get("audio", {})
        if not isinstance(audio_runtime, dict):
            audio_runtime = {}
        self.min_recording_ms.set_value(
            max(0, min(5000, int(audio_runtime.get("min_recording_ms", 1000))))
        )
        self.asr_streaming_enabled.set_active(
            _as_bool(asr_streaming.get("enabled"), False)
        )
        self.itn_enabled.set_active(_as_bool(normalization.get("enabled"), True))
        self.compact_dates.set_active(_as_bool(normalization.get("compact_dates"), True))
        self.compact_times.set_active(_as_bool(normalization.get("compact_times"), True))
        self.compact_distances.set_active(_as_bool(normalization.get("compact_distances"), True))
        self.currency_symbols.set_active(_as_bool(normalization.get("currency_symbols"), True))
        self._slm_health_fingerprint = (
            str(slm.get("verified_fingerprint", "")).strip() or None
        )
        self.slm_enabled.set_active(_as_bool(slm.get("enabled"), False))
        provider = str(slm.get("provider", "remote"))
        self.slm_provider.set_active_id(provider if provider in {"remote", "local_ephemeral"} else "remote")
        self.slm_endpoint.set_text(str(slm.get("endpoint", "")))
        self.slm_model.set_text(str(slm.get("model", "")))
        api_key_env = str(slm.get("api_key_env", ""))
        direct_api_key = str(slm.get("api_key", ""))
        if not direct_api_key and looks_like_api_key(api_key_env):
            self.slm_api_key_env.set_text("")
            self.slm_api_key.set_text(api_key_env)
            self.slm_test_status.set_text(
                "⚠️ 检测到密钥曾误填在环境变量名字段；已自动迁移，保存设置后永久修正。"
            )
        else:
            self.slm_api_key_env.set_text(api_key_env)
        self.slm_min_chars.set_value(float(slm.get("min_chars", 8)))
        self.slm_timeout.set_value(float(slm.get("stream_idle_timeout_ms", slm.get("timeout_ms", 20000))))
        self.slm_remote_stream.set_active(_as_bool(slm.get("remote_stream"), True))
        self.slm_thinking.set_active(_as_bool(slm.get("enable_thinking"), False))
        panel_style = str(
            ui.get("panel_style", self.module_config.get("panelstyle", "minimal"))
        ).strip().lower()
        self.panel_style.set_active_id(
            panel_style if panel_style in {"minimal", "animated"} else "minimal"
        )
        self.feedback_endpoint.set_text(str(feedback.get("endpoint", "")))
        self.feedback_use_custom_endpoint.set_active(
            _as_bool(feedback.get("use_custom_endpoint"), False)
        )
        self.feedback_endpoint.set_sensitive(
            self.feedback_use_custom_endpoint.get_active()
        )
        try:
            self._saved_audio_config = load_audio_config()
        except Exception as exc:  # noqa: BLE001
            self._saved_audio_config = {"device_name": "", "device_id": None, "sample_rate": 0}
            self._set_audio_status(f"音频配置读取失败：{exc}")
        if int(self._saved_audio_config.get("sample_rate") or 0) > 0:
            sample_rate = int(self._saved_audio_config["sample_rate"])
            self.audio_sample_rate.set_value(sample_rate)
            self.playground_audio_sample_rate.set_value(sample_rate)
        GLib.idle_add(self._start_audio_refresh)
        GLib.idle_add(self._update_playground_slm_gate)

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
        api_key_env = self.slm_api_key_env.get_text().strip()
        entered_key = self.slm_api_key.get_text().strip()
        if looks_like_api_key(api_key_env) and not entered_key:
            entered_key = api_key_env
            api_key_env = ""
        elif api_key_env and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", api_key_env):
            raise ValueError(
                "API Key 环境变量名无效。请输入类似 DEEPSEEK_API_KEY 的变量名；"
                "真正的 sk-... 密钥请填入“直接 API Key”。"
            )
        result.update(
            {
                "enabled": self.slm_enabled.get_active(),
                "provider": self.slm_provider.get_active_id() or "remote",
                "endpoint": self.slm_endpoint.get_text().strip(),
                "model": self.slm_model.get_text().strip(),
                "api_key_env": api_key_env,
                "min_chars": int(self.slm_min_chars.get_value()),
                "timeout_ms": int(self.slm_timeout.get_value()),
                "stream_idle_timeout_ms": int(self.slm_timeout.get_value()),
                "remote_stream": self.slm_remote_stream.get_active(),
                "enable_thinking": self.slm_thinking.get_active(),
            }
        )
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
                device_name = getattr(self, "_audio_devices", {}).get(
                    device_id, {}
                ).get("name", device_name)
                save_audio_config(
                    device_name=str(device_name),
                    device_id=device_id,
                    sample_rate=int(self.audio_sample_rate.get_value()),
                )
            config = load_runtime_config()
            streaming = config.get("asr_streaming")
            if not isinstance(streaming, dict):
                streaming = {}
            streaming["enabled"] = self.asr_streaming_enabled.get_active()
            config["asr_streaming"] = streaming
            audio_runtime = config.get("audio")
            if not isinstance(audio_runtime, dict):
                audio_runtime = {}
            audio_runtime["min_recording_ms"] = int(
                self.min_recording_ms.get_value()
            )
            config["audio"] = audio_runtime
            config["normalization"] = self._current_normalization()
            ui = config.get("ui")
            if not isinstance(ui, dict):
                ui = {}
            ui["panel_style"] = self.panel_style.get_active_id() or "minimal"
            config["ui"] = ui
            slm_config = self._current_slm()
            if slm_config.get("enabled"):
                validation_error = self._slm_validation_error(slm_config)
                if validation_error:
                    raise ValueError(validation_error)
                if slm_config_fingerprint(slm_config) != self._slm_health_fingerprint:
                    raise ValueError(
                        "当前 AI 配置尚未通过测活；请关闭后重新打开 AI 润色开关。"
                    )
                slm_config["verified_fingerprint"] = self._slm_health_fingerprint
            config["slm"] = slm_config
            feedback = config.get("feedback")
            if not isinstance(feedback, dict):
                feedback = {}
            feedback["endpoint"] = self.feedback_endpoint.get_text().strip()
            feedback["use_custom_endpoint"] = self.feedback_use_custom_endpoint.get_active()
            config["feedback"] = feedback
            save_runtime_config(config)
            save_fcitx_module_config(
                {
                    "MinRecordingMs": int(self.min_recording_ms.get_value()),
                    "PolishMinChars": int(self.slm_min_chars.get_value()),
                    "PolishTimeoutMs": int(self.slm_timeout.get_value()),
                    "EnableThinking": self.slm_thinking.get_active(),
                    "PanelStyle": self.panel_style.get_active_id() or "minimal",
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
            details = ["✅ VoCoType 配置已保存。"]
            if backend_service_present:
                backend_ok, backend_message = restart_backend()
                details.append(
                    "Fcitx 后台服务已重启" if backend_ok else backend_message
                )
            if fcitx_module_present and shutil.which("fcitx5"):
                fcitx_ok, fcitx_message = restart_fcitx()
                details.append("Fcitx 5 已重载" if fcitx_ok else fcitx_message)
            GLib.idle_add(
                self._message,
                "保存成功",
                "\n".join(details),
                Gtk.MessageType.INFO,
            )

        threading.Thread(target=reload_services, daemon=True).start()

    def _set_audio_status(self, message: str) -> None:
        if hasattr(self, "audio_status"):
            self.audio_status.set_text(message)
        if hasattr(self, "playground_audio_status"):
            self.playground_audio_status.set_text(message)

    def _start_audio_refresh(self) -> bool:
        self._on_refresh_audio(None)
        return False

    def _on_refresh_audio(self, _button: Gtk.Button | None) -> None:
        self._set_audio_status("正在枚举输入与输出设备…")
        self._playground_audio_busy = True
        self._update_playground_recording_actions()
        saved = getattr(self, "_saved_audio_config", {})

        def work() -> None:
            errors: list[str] = []
            try:
                devices = [
                    {
                        "id": item.device_id,
                        "name": item.name,
                        "sample_rate": item.sample_rate,
                        "channels": item.channels,
                    }
                    for item in list_input_devices()
                ]
            except Exception as exc:  # noqa: BLE001
                devices = []
                errors.append(f"输入设备：{exc}")
            try:
                outputs = list_output_devices()
            except Exception as exc:  # noqa: BLE001
                outputs = []
                errors.append(f"输出设备：{exc}")
            GLib.idle_add(
                self._render_audio_devices,
                devices,
                outputs,
                saved,
                "；".join(errors),
            )

        threading.Thread(target=work, daemon=True).start()

    def _render_audio_devices(
        self,
        devices: list[dict[str, Any]],
        outputs: list[OutputDevice],
        saved: dict[str, Any],
        error: str,
    ) -> bool:
        self._rendering_audio_devices = True
        self.audio_device.remove_all()
        self.playground_audio_device.remove_all()
        self.audio_output.remove_all()
        self._audio_devices = {int(item["id"]): item for item in devices}
        self._audio_outputs = {str(index): item for index, item in enumerate(outputs)}

        saved_id = saved.get("device_id")
        saved_name = str(saved.get("device_name") or "")
        selected_id: int | None = None
        for item in devices:
            device_id = int(item["id"])
            label = f"[{device_id}] {item['name']}"
            self.audio_device.append(str(device_id), label)
            self.playground_audio_device.append(str(device_id), label)
            if saved_name and item["name"] == saved_name:
                selected_id = device_id
            elif selected_id is None and saved_id == device_id:
                selected_id = device_id
        if selected_id is None and devices:
            selected_id = int(devices[0]["id"])
        if selected_id is not None:
            self.audio_device.set_active_id(str(selected_id))
            self.playground_audio_device.set_active_id(str(selected_id))
            selected = self._audio_devices[selected_id]
            selected_rate = int(saved.get("sample_rate") or selected["sample_rate"])
            self.audio_sample_rate.set_value(selected_rate)
            self.playground_audio_sample_rate.set_value(selected_rate)

        selected_output_id: str | None = None
        for key, item in self._audio_outputs.items():
            suffix = "（系统默认）" if item.is_default else ""
            self.audio_output.append(key, f"{item.name}{suffix}")
            if selected_output_id is None and item.is_default:
                selected_output_id = key
        if selected_output_id is None and outputs:
            selected_output_id = "0"
        if selected_output_id is not None:
            self.audio_output.set_active_id(selected_output_id)

        self._rendering_audio_devices = False
        self._playground_audio_busy = False
        parts = [f"检测到 {len(devices)} 个输入设备", f"{len(outputs)} 个输出设备"]
        if error:
            parts.append(f"⚠️ {error}")
        if not devices:
            parts.append("无法录音")
        if not outputs:
            parts.append("无法回放")
        if devices and outputs:
            parts.append(
                f"点击“录音 {int(RECORDING_DURATION_SECONDS)} 秒”开始实际试听"
            )
            selected_output = next((item for item in outputs if item.is_default), outputs[0])
            if "hdmi" in (selected_output.name + selected_output.device_id).casefold():
                parts.append(
                    "⚠️ 当前系统默认输出是 HDMI；无声时请改选 Speakers 或 Headphones"
                )
        self._set_audio_status("；".join(parts) + "。")
        self._update_playground_recording_actions()
        return False

    def _sync_audio_device(self, source: Gtk.ComboBoxText, target: Gtk.ComboBoxText) -> None:
        if self._syncing_audio_controls:
            return
        active_id = source.get_active_id()
        if active_id is None or target.get_active_id() == active_id:
            return
        self._syncing_audio_controls = True
        try:
            target.set_active_id(active_id)
        finally:
            self._syncing_audio_controls = False

    def _sync_audio_sample_rate(
        self, source: Gtk.SpinButton, target: Gtk.SpinButton
    ) -> None:
        if self._syncing_audio_controls:
            return
        value = int(source.get_value())
        if int(target.get_value()) == value:
            return
        self._syncing_audio_controls = True
        try:
            target.set_value(value)
        finally:
            self._syncing_audio_controls = False

    def _apply_selected_audio_device(
        self, source: Gtk.ComboBoxText, target: Gtk.ComboBoxText
    ) -> None:
        if getattr(self, "_rendering_audio_devices", False):
            return
        self._sync_audio_device(source, target)
        active_id = source.get_active_id()
        if active_id is None:
            self._update_playground_recording_actions()
            return
        selected = getattr(self, "_audio_devices", {}).get(int(active_id), {})
        if selected.get("sample_rate"):
            rate = int(selected["sample_rate"])
            self._syncing_audio_controls = True
            try:
                self.audio_sample_rate.set_value(rate)
                self.playground_audio_sample_rate.set_value(rate)
            finally:
                self._syncing_audio_controls = False
        self._set_audio_status(
            f"已切换输入设备；录制新的 {int(RECORDING_DURATION_SECONDS)} 秒样本后会保存为 F9 使用设备。"
        )
        self._update_playground_recording_actions()

    def _on_audio_device_changed(self, _widget: Gtk.ComboBoxText) -> None:
        if self._syncing_audio_controls:
            return
        self._apply_selected_audio_device(
            self.audio_device, self.playground_audio_device
        )

    def _on_playground_audio_device_changed(
        self, _widget: Gtk.ComboBoxText
    ) -> None:
        if self._syncing_audio_controls:
            return
        self._apply_selected_audio_device(
            self.playground_audio_device, self.audio_device
        )

    def _on_audio_sample_rate_changed(self, _widget: Gtk.SpinButton) -> None:
        self._sync_audio_sample_rate(
            self.audio_sample_rate, self.playground_audio_sample_rate
        )

    def _on_playground_audio_sample_rate_changed(
        self, _widget: Gtk.SpinButton
    ) -> None:
        self._sync_audio_sample_rate(
            self.playground_audio_sample_rate, self.audio_sample_rate
        )

    def _set_panel_style_status(self, message: str) -> bool:
        self.panel_style_status.set_text(message)
        return False

    def _refresh_panel_style_status(self) -> bool:
        supported, module = fcitx_panel_style_support()
        if supported:
            self.panel_style_status.set_text(
                f"✅ 当前 module 支持此设置：{module}"
            )
        elif module is not None:
            self.panel_style_status.set_text(
                "⚠️ 当前已安装 module 版本过旧；请在“概览与安装 → Fcitx 5”"
                "执行安装 / 修复后生效。"
            )
        else:
            self.panel_style_status.set_text(
                "⚠️ 尚未安装 VoCoType（Fcitx 5）module。"
            )
        return False

    def _on_panel_style_changed(self, _widget: Gtk.ComboBoxText) -> None:
        if self._loading_values:
            return
        style = self.panel_style.get_active_id() or "minimal"
        try:
            save_fcitx_module_config({"PanelStyle": style})
            runtime = load_runtime_config()
            ui = runtime.get("ui")
            if not isinstance(ui, dict):
                ui = {}
            ui["panel_style"] = style
            runtime["ui"] = ui
            save_runtime_config(runtime)
            self.runtime_config = runtime
            self.module_config = load_fcitx_module_config()
        except Exception as exc:  # noqa: BLE001
            self.panel_style_status.set_text(f"❌ 保存状态样式失败：{exc}")
            return
        self.panel_style_status.set_text("⏳ 配置已保存；正在重载 Fcitx 5，IBus 将在下次录音读取新样式…")

        def work() -> None:
            supported, module = fcitx_panel_style_support()
            if not supported:
                if module is None:
                    message = "⚠️ 配置已保存，但尚未安装 Fcitx module。"
                else:
                    message = (
                        "⚠️ 配置已保存，但当前系统 module 版本过旧；"
                        "请在概览页选择 Fcitx 5 后执行安装 / 修复。"
                    )
                GLib.idle_add(self._set_panel_style_status, message)
                GLib.idle_add(self._refresh_install_status)
                return
            ok, detail = restart_fcitx()
            if ok:
                label = "极简" if style == "minimal" else "动画"
                message = f"✅ Fcitx 5 与 IBus 已统一切换为{label}模式。"
            else:
                message = f"❌ 配置已保存，但 Fcitx 5 重载失败：{detail}"
            GLib.idle_add(self._set_panel_style_status, message)
            GLib.idle_add(self._refresh_install_status)

        threading.Thread(target=work, daemon=True).start()

    def _on_audio_output_changed(self, _widget: Gtk.ComboBoxText) -> None:
        if getattr(self, "_rendering_audio_devices", False):
            return
        output = self._selected_audio_output()
        if output is not None:
            self._set_audio_status(f"回放输出已切换到：{output.name}")
        self._update_playground_recording_actions()

    def _selected_audio_output(self) -> OutputDevice | None:
        active_id = self.audio_output.get_active_id()
        if active_id is None:
            return None
        return getattr(self, "_audio_outputs", {}).get(active_id)

    def _draw_playground_waveform(self, widget: Gtk.DrawingArea, context: Any) -> bool:
        width = max(1, widget.get_allocated_width())
        height = max(1, widget.get_allocated_height())
        center = height / 2.0
        color = widget.get_style_context().get_color(Gtk.StateFlags.NORMAL)
        context.set_line_width(1.0)
        context.set_source_rgba(color.red, color.green, color.blue, 0.22)
        context.move_to(0, center)
        context.line_to(width, center)
        context.stroke()

        points = tuple(self._playground_waveform)
        if not points:
            return False
        step = width / max(1, len(points) - 1)
        visible_peak = max(
            max(abs(minimum), abs(maximum)) for minimum, maximum in points
        )
        # Display gain is visualization-only. A quiet but valid microphone
        # signal should still occupy the canvas instead of looking flat.
        display_peak = max(0.01, min(1.0, visible_peak * 1.2))
        scale = max(1.0, (center - 8.0) / display_peak)
        context.set_line_width(max(1.0, min(2.0, step * 0.55)))
        context.set_source_rgba(color.red, color.green, color.blue, 0.92)
        for index, (minimum, maximum) in enumerate(points):
            x = index * step
            context.move_to(x, center - maximum * scale)
            context.line_to(x, center - minimum * scale)
        context.stroke()
        return False

    def _append_playground_waveform(
        self, envelope: tuple[tuple[float, float], ...]
    ) -> bool:
        self._playground_waveform.extend(envelope)
        self.playground_waveform.queue_draw()
        return False

    def _update_playground_recording_actions(self) -> bool:
        if not hasattr(self, "playground_record_button"):
            return False
        has_device = self.playground_audio_device.get_active_id() is not None
        has_output = self._selected_audio_output() is not None
        has_recording = bool(
            self._playground_recording_path
            and self._playground_recording_path.is_file()
        )
        idle = not self._playground_audio_busy
        self.playground_refresh_audio_button.set_sensitive(idle)
        self.playground_record_button.set_sensitive(idle and has_device)
        self.playground_play_button.set_sensitive(idle and has_recording and has_output)
        self.playground_transcribe_button.set_sensitive(idle and has_recording)
        return False

    def _on_playground_record(self, _button: Gtk.Button) -> None:
        active_id = self.playground_audio_device.get_active_id()
        if active_id is None:
            self._set_audio_status("请先选择麦克风")
            return
        device_id = int(active_id)
        sample_rate = int(self.playground_audio_sample_rate.get_value())
        device_name = str(
            getattr(self, "_audio_devices", {}).get(device_id, {}).get(
                "name", self.playground_audio_device.get_active_text() or ""
            )
        )
        self._playground_audio_busy = True
        self._playground_waveform.clear()
        self.playground_waveform.queue_draw()
        self._update_playground_recording_actions()
        self._set_audio_status(
            f"🎙️ 正在录音 {int(RECORDING_DURATION_SECONDS)} 秒，请持续正常说话…"
        )

        def work() -> None:
            try:
                recording = record_audio(
                    device_id=device_id,
                    device_name=device_name,
                    sample_rate=sample_rate,
                    waveform_callback=lambda envelope: GLib.idle_add(
                        self._append_playground_waveform, envelope
                    ),
                )
                save_audio_config(
                    device_name=device_name,
                    device_id=device_id,
                    sample_rate=sample_rate,
                    test_peak=recording.peak,
                    test_rms=recording.rms,
                    preserve_test=False,
                )
                error = ""
            except Exception as exc:  # noqa: BLE001
                recording = None
                error = str(exc)

            def finish() -> bool:
                self._playground_audio_busy = False
                if recording is None:
                    self._set_audio_status(f"❌ 录音失败：{error}")
                else:
                    self._playground_recording_path = recording.path
                    self._saved_audio_config = load_audio_config()
                    if recording.peak < 0.002:
                        prefix = "⚠️ 录音几乎没有信号"
                    elif recording.peak < 0.06:
                        prefix = "⚠️ 录音音量偏低（回放会自动增益）"
                    elif recording.peak > 0.98:
                        prefix = "⚠️ 录音可能削波"
                    else:
                        prefix = "✅ 录音完成"
                    self._set_audio_status(
                        f"{prefix}：{recording.duration_seconds:.2f} 秒，"
                        f"peak={recording.peak:.3f}，RMS={recording.rms:.3f}。"
                        "请选择正确的输出设备并点击“回放上次录音”。"
                    )
                self._update_playground_recording_actions()
                return False

            GLib.idle_add(finish)

        threading.Thread(target=work, daemon=True).start()

    def _on_playground_play(self, _button: Gtk.Button) -> None:
        path = self._playground_recording_path
        if path is None or not path.is_file():
            self._set_audio_status(
                f"请先录制 {int(RECORDING_DURATION_SECONDS)} 秒样本"
            )
            self._update_playground_recording_actions()
            return
        output = self._selected_audio_output()
        if output is None:
            self._set_audio_status("请先选择输出设备")
            self._update_playground_recording_actions()
            return
        self._playground_audio_busy = True
        self._update_playground_recording_actions()
        self._set_audio_status(f"🔊 正在回放到：{output.name}…")

        def work() -> None:
            try:
                result = play_recording(path, output_device=output)
                gain_note = (
                    f"；自动增益 +{result.gain_db:.1f} dB"
                    if result.gain_db >= 0.5
                    else "；原始音量无需增益"
                )
                message = (
                    f"✅ 回放完成（{result.duration_seconds:.2f} 秒）；"
                    f"后端：{result.backend}；输出：{result.output_name}"
                    f"{gain_note}。"
                )
            except Exception as exc:  # noqa: BLE001
                message = f"❌ 回放失败：{exc}"

            def finish() -> bool:
                self._playground_audio_busy = False
                self._set_audio_status(message)
                self._update_playground_recording_actions()
                return False

            GLib.idle_add(finish)

        threading.Thread(target=work, daemon=True).start()

    def _on_playground_transcribe(self, _button: Gtk.Button) -> None:
        path = self._playground_recording_path
        if path is None or not path.is_file():
            self.playground_transcribe_status.set_text("请先录制 3 秒样本")
            self._update_playground_recording_actions()
            return
        self._playground_audio_busy = True
        self._update_playground_recording_actions()
        self.playground_transcribe_status.set_text("⏳ 正在调用当前 ASR 后台…")

        def work() -> None:
            try:
                response = transcribe_recording(path)
                text = str(response.get("text", "")).strip()
                if text:
                    message = "✅ 转录完成；请对照刚才实际说的话检查。"
                else:
                    message = "⚠️ 转录完成，但模型返回空文本。"
            except Exception as exc:  # noqa: BLE001
                text = ""
                message = f"❌ 转录失败：{exc}"

            def finish() -> bool:
                self._playground_audio_busy = False
                if text:
                    self.playground_transcript_view.get_buffer().set_text(text)
                self.playground_transcribe_status.set_text(message)
                self._update_playground_recording_actions()
                return False

            GLib.idle_add(finish)

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

    def _set_slm_enabled_silently(self, active: bool) -> None:
        self._slm_toggle_guard = True
        try:
            self.slm_enabled.set_active(active)
        finally:
            self._slm_toggle_guard = False

    @staticmethod
    def _slm_validation_error(config: dict[str, Any]) -> str:
        provider = str(config.get("provider", "remote")).strip().lower()
        model = str(config.get("model", "")).strip()
        if provider in {"local", "ephemeral", "local_once", "local_ephemeral"}:
            local_model = str(config.get("local_model", "")).strip()
            if not model and not local_model:
                return "请先填写本地模型。"
            return ""
        endpoint = str(config.get("endpoint", "")).strip()
        if not endpoint:
            return "请先填写 OpenAI-compatible API 端点。"
        if not model:
            return "请先填写模型名称。"
        direct_key = str(config.get("api_key", "")).strip()
        env_name = str(config.get("api_key_env", "")).strip()
        env_key = str(os.environ.get(env_name, "")).strip() if env_name else ""
        if not direct_key and not env_key:
            return "请先填写直接 API Key，或提供已设置的 API Key 环境变量。"
        return ""

    def _persist_slm_config(self, config: dict[str, Any]) -> None:
        runtime = load_runtime_config()
        runtime["slm"] = dict(config)
        save_runtime_config(runtime)
        self.runtime_config = runtime

    def _reload_backend_after_slm_change(self) -> None:
        def work() -> None:
            paths = installation_paths()
            if not any(path.is_file() for path in paths.fcitx_services):
                return
            ok, detail = restart_backend()
            if not ok:
                GLib.idle_add(
                    self.slm_test_status.set_text,
                    f"⚠️ AI 配置已保存，但 Fcitx 后台重启失败：{detail}",
                )

        threading.Thread(target=work, daemon=True).start()

    def _on_slm_enabled_changed(self, *_args: object) -> None:
        if self._loading_values or self._slm_toggle_guard:
            return
        if not self.slm_enabled.get_active():
            try:
                config = self._current_slm()
                config["enabled"] = False
                self._persist_slm_config(config)
                self._reload_backend_after_slm_change()
            except Exception as exc:  # noqa: BLE001
                self.slm_test_status.set_text(f"❌ 无法保存关闭状态：{exc}")
            self._update_playground_slm_gate()
            return

        try:
            config = self._current_slm()
        except Exception as exc:  # noqa: BLE001
            self._set_slm_enabled_silently(False)
            self.slm_test_status.set_text(f"❌ AI 配置无效：{exc}")
            self._update_playground_slm_gate()
            return
        config["enabled"] = True
        error = self._slm_validation_error(config)
        if error:
            self._set_slm_enabled_silently(False)
            self.slm_test_status.set_text(f"❌ {error}")
            self._update_playground_slm_gate()
            return

        self._set_slm_enabled_silently(False)
        self.slm_enabled.set_sensitive(False)
        self._slm_probe_in_flight = True
        self._slm_health_fingerprint = None
        tested_fingerprint = slm_config_fingerprint(config)
        self.slm_test_status.set_text("⏳ 正在测活；成功后才会启用 AI 润色…")
        self._update_playground_slm_gate()

        def work() -> None:
            try:
                from app.slm_polisher import SLMPolisher

                probe_config = {**config, "enabled": True, "min_chars": 1}
                polisher = SLMPolisher(probe_config)
                output, metrics = polisher.polish(
                    "这是一次 VoCoType AI 润色连接测试。",
                    long_mode=True,
                )
                if metrics.reason == "ok":
                    prefix = (
                        f"{polisher.credential_warning} "
                        if polisher.credential_warning
                        else ""
                    )
                    message = f"✅ {prefix}测活成功，AI 润色已启用：{output}"
                    success = True
                else:
                    message = f"❌ {polisher.format_failure_message(metrics.reason)}"
                    success = False
            except Exception as exc:  # noqa: BLE001
                message = f"❌ 测活失败，AI 润色保持关闭：{exc}"
                success = False

            def finish() -> bool:
                self._slm_probe_in_flight = False
                self.slm_enabled.set_sensitive(True)
                saved = dict(config)
                if success:
                    saved["enabled"] = True
                    saved["verified_fingerprint"] = tested_fingerprint
                    saved["verified_at"] = int(__import__("time").time())
                    self._slm_health_fingerprint = tested_fingerprint
                else:
                    saved["enabled"] = False
                    saved.pop("verified_fingerprint", None)
                    saved.pop("verified_at", None)
                    self._slm_health_fingerprint = None
                try:
                    self._persist_slm_config(saved)
                except Exception as exc:  # noqa: BLE001
                    self._slm_health_fingerprint = None
                    self._set_slm_enabled_silently(False)
                    self.slm_test_status.set_text(f"❌ 测活结果无法保存：{exc}")
                    self._update_playground_slm_gate()
                    return False
                self._set_slm_enabled_silently(success)
                self._reload_backend_after_slm_change()
                if success:
                    self._slm_toggle_guard = True
                    try:
                        self.slm_api_key.set_text("")
                        self.slm_clear_api_key.set_active(False)
                    finally:
                        self._slm_toggle_guard = False
                self.slm_test_status.set_text(message)
                self._update_playground_slm_gate()
                return False

            GLib.idle_add(finish)

        threading.Thread(target=work, daemon=True).start()

    def _on_slm_config_changed(self, *_args: object) -> None:
        if self._loading_values or self._slm_toggle_guard:
            return
        if self.slm_enabled.get_active() and not self._slm_probe_in_flight:
            try:
                current = slm_config_fingerprint(self._current_slm())
            except Exception:
                current = ""
            if current != self._slm_health_fingerprint:
                self._set_slm_enabled_silently(False)
                self._slm_health_fingerprint = None
                try:
                    config = self._current_slm()
                    config["enabled"] = False
                    config.pop("verified_fingerprint", None)
                    config.pop("verified_at", None)
                    self._persist_slm_config(config)
                except Exception:
                    pass
                self.slm_test_status.set_text(
                    "⚠️ AI 配置已更改，功能已自动关闭；重新打开开关即可测活。"
                )
        if hasattr(self, "playground_ai_controls"):
            self._update_playground_slm_gate()

    def _update_playground_slm_gate(self) -> bool:
        if not hasattr(self, "playground_ai_controls"):
            return False
        try:
            config = self._current_slm()
            ready, reason = slm_playground_gate(
                config,
                verified_fingerprint=self._slm_health_fingerprint,
            )
        except Exception as exc:  # noqa: BLE001
            ready = False
            reason = f"AI 配置无效：{exc}"
        self.playground_ai_controls.set_sensitive(
            ready and not self._playground_ai_busy
        )
        self.playground_ai_gate_status.set_text(
            ("✅ " if ready else "🔒 ") + reason
        )
        return False

    @staticmethod
    def _text_view_text(view: Gtk.TextView) -> str:
        buffer = view.get_buffer()
        return buffer.get_text(
            buffer.get_start_iter(),
            buffer.get_end_iter(),
            True,
        )

    def _set_playground_ai_status(self, message: str) -> None:
        if hasattr(self, "playground_polish_status"):
            self.playground_polish_status.set_text(message)
        if hasattr(self, "playground_edit_status"):
            self.playground_edit_status.set_text(message)

    def _set_playground_ai_busy(self, busy: bool) -> None:
        self._playground_ai_busy = busy
        self._update_playground_slm_gate()

    def _playground_ai_config(self) -> dict[str, Any] | None:
        try:
            config = self._current_slm()
        except Exception as exc:  # noqa: BLE001
            self._set_playground_ai_status(f"❌ AI 配置无效：{exc}")
            self._update_playground_slm_gate()
            return None
        ready, reason = slm_playground_gate(
            config,
            verified_fingerprint=self._slm_health_fingerprint,
        )
        if not ready:
            self._set_playground_ai_status(f"🔒 {reason}")
            self._update_playground_slm_gate()
            return None
        config["enabled"] = True
        config["min_chars"] = 0
        return config

    def _on_playground_polish(self, _button: Gtk.Button) -> None:
        config = self._playground_ai_config()
        if config is None:
            return
        source = self._text_view_text(self.playground_polish_source).strip()
        if not source:
            self.playground_polish_status.set_text("请输入需要润色的文本")
            return
        self._set_playground_ai_busy(True)
        self.playground_polish_status.set_text("⏳ 正在调用 AI 润色…")

        def work() -> None:
            try:
                from app.slm_polisher import SLMPolisher

                polisher = SLMPolisher(config)
                output, metrics = polisher.polish(source, long_mode=True)
                if metrics.reason == "ok":
                    message = f"✅ 润色完成，耗时 {metrics.latency_ms:.0f} ms"
                    success = True
                else:
                    message = f"❌ {polisher.format_failure_message(metrics.reason)}"
                    success = False
            except Exception as exc:  # noqa: BLE001
                output = ""
                message = f"❌ AI 润色失败：{exc}"
                success = False

            def finish() -> bool:
                self._set_playground_ai_busy(False)
                if success:
                    self.playground_polish_result.get_buffer().set_text(output)
                self.playground_polish_status.set_text(message)
                return False

            GLib.idle_add(finish)

        threading.Thread(target=work, daemon=True).start()

    def _on_playground_edit_example(
        self, _button: Gtk.Button, key: str
    ) -> None:
        examples = {
            "replace": (
                "A 是旧版本标记，后文仍然引用 A。",
                "把 A 替换成 B",
            ),
            "translate": (
                "VoCoType 是一个离线优先的 Linux 语音输入工具。",
                "把这段文字翻译成英语",
            ),
            "latex": (
                "勾股定理是 a 的平方加 b 的平方等于 c 的平方。",
                "把数学表达式转换成 LaTeX 公式",
            ),
            "review": (
                "这个语音输入工具识别快、隐私好、安装方便。",
                "把这段体验写成一条自然、可信的好评",
            ),
        }
        source, instruction = examples.get(key, examples["replace"])
        self.playground_edit_source.get_buffer().set_text(source)
        self.playground_ai_instruction.set_text(instruction)
        self.playground_edit_status.set_text("已载入范例，可直接测试。")

    def _on_playground_edit(self, _button: Gtk.Button) -> None:
        if self._playground_ai_config() is None:
            return
        active_id = self.playground_audio_device.get_active_id()
        if active_id is None:
            self.playground_edit_status.set_text("请先在第 1 区选择麦克风。")
            return
        source_buffer = self.playground_edit_source.get_buffer()
        source = source_buffer.get_text(
            source_buffer.get_start_iter(),
            source_buffer.get_end_iter(),
            True,
        )
        if not source.strip():
            self.playground_edit_status.set_text("请输入用于测试的上下文文本。")
            return
        cursor_pos = source_buffer.get_iter_at_mark(
            source_buffer.get_insert()
        ).get_offset()
        selection = source_buffer.get_selection_bounds()
        if selection:
            selection_start, selection_end = selection
            anchor_pos = selection_start.get_offset()
            selected_text = source_buffer.get_text(
                selection_start, selection_end, True
            )
        else:
            anchor_pos = cursor_pos
            selected_text = ""

        device_id = int(active_id)
        sample_rate = int(self.playground_audio_sample_rate.get_value())
        device_name = str(
            getattr(self, "_audio_devices", {}).get(device_id, {}).get(
                "name", self.playground_audio_device.get_active_text() or ""
            )
        )
        spoken_hint = self.playground_ai_instruction.get_text().strip()
        audio_path = last_recording_path().with_name("voice-edit-command.wav")
        self._playground_audio_busy = True
        self._set_playground_ai_busy(True)
        self._playground_waveform.clear()
        self.playground_waveform.queue_draw()
        self._update_playground_recording_actions()
        self.playground_edit_status.set_text(
            f"🎙️ 正在录制语音编辑指令，请说：{spoken_hint or '你的编辑指令'}"
        )

        def work() -> None:
            try:
                record_audio(
                    device_id=device_id,
                    device_name=device_name,
                    sample_rate=sample_rate,
                    duration_seconds=RECORDING_DURATION_SECONDS,
                    output_path=audio_path,
                    waveform_callback=lambda envelope: GLib.idle_add(
                        self._append_playground_waveform, envelope
                    ),
                )
                response = edit_recording(
                    audio_path,
                    context_text=source,
                    cursor_pos=cursor_pos,
                    anchor_pos=anchor_pos,
                    selected_text=selected_text,
                )
                instruction = str(response.get("instruction", "")).strip()
                output = str(
                    response.get("expected_text")
                    or response.get("new_text")
                    or source
                )
                mode = str(response.get("mode", "")).strip()
                message = (
                    f"✅ 语音编辑完成；识别指令：{instruction or '（未返回）'}"
                    + (f"；模式：{mode}" if mode else "")
                )
                success = True
            except Exception as exc:  # noqa: BLE001
                output = ""
                message = f"❌ 语音编辑失败：{exc}"
                success = False
            finally:
                try:
                    audio_path.unlink()
                except OSError:
                    pass

            def finish() -> bool:
                self._playground_audio_busy = False
                self._set_playground_ai_busy(False)
                if success:
                    self.playground_edit_result.get_buffer().set_text(output)
                self.playground_edit_status.set_text(message)
                self._update_playground_recording_actions()
                return False

            GLib.idle_add(finish)

        threading.Thread(target=work, daemon=True).start()

    def _on_test_slm(self, _button: Gtk.Button) -> None:
        try:
            config = self._current_slm()
        except Exception as exc:  # noqa: BLE001
            self._slm_health_fingerprint = None
            self.slm_test_status.set_text(f"❌ AI 配置无效：{exc}")
            self._update_playground_slm_gate()
            return
        original_enabled = self.slm_enabled.get_active()
        config["enabled"] = True
        error = self._slm_validation_error(config)
        if error:
            self._slm_health_fingerprint = None
            self.slm_test_status.set_text(f"❌ {error}")
            self._update_playground_slm_gate()
            return
        config["min_chars"] = 1
        tested_fingerprint = slm_config_fingerprint(config)
        self._slm_health_fingerprint = None
        self.slm_test_status.set_text("⏳ 正在执行 AI 端点/模型测活…")
        self._update_playground_slm_gate()

        def work() -> None:
            try:
                from app.slm_polisher import SLMPolisher

                polisher = SLMPolisher(config)
                output, metrics = polisher.polish(
                    "这是一次 VoCoType AI 润色连接测试。",
                    long_mode=True,
                )
                if metrics.reason == "ok":
                    prefix = (
                        f"{polisher.credential_warning} "
                        if polisher.credential_warning
                        else ""
                    )
                    message = f"✅ {prefix}测活成功：{output}"
                    success = True
                else:
                    message = f"❌ {polisher.format_failure_message(metrics.reason)}"
                    success = False
            except Exception as exc:  # noqa: BLE001
                message = f"❌ 测活失败：{exc}"
                success = False

            def finish() -> bool:
                if success:
                    saved = dict(config)
                    saved["enabled"] = original_enabled
                    saved["verified_fingerprint"] = tested_fingerprint
                    saved["verified_at"] = int(__import__("time").time())
                    try:
                        self._persist_slm_config(saved)
                    except Exception as exc:  # noqa: BLE001
                        self._slm_health_fingerprint = None
                        self.slm_test_status.set_text(
                            f"❌ 测活成功但无法保存验证状态：{exc}"
                        )
                        self._update_playground_slm_gate()
                        return False
                    self._slm_health_fingerprint = tested_fingerprint
                else:
                    self._slm_health_fingerprint = None
                self.slm_test_status.set_text(message)
                self._update_playground_slm_gate()
                return False

            GLib.idle_add(finish)

        threading.Thread(target=work, daemon=True).start()

    def _refresh_install_status(self) -> bool:
        root = find_project_root()
        fcitx_status = integration_status("fcitx5", project_root=root)
        ibus_status = integration_status("ibus", project_root=root)

        def framework_text(name: str, status) -> str:
            if status.state == "complete":
                return f"✅ VoCoType（{name}）：安装完整"
            if status.state == "partial":
                return (
                    f"⚠️ VoCoType（{name}）：安装不完整\n"
                    f"缺少：{', '.join(status.missing)}"
                )
            return f"❌ VoCoType（{name}）：未安装"

        package_command = native_package_removal_command(root)
        environment_lines = []
        if self._last_lifecycle_notice:
            environment_lines.append(self._last_lifecycle_notice)
        environment_lines.extend(
            [
                f"{'✅' if root else '❌'} 源码目录：{root or '未发现'}",
                f"{'✅' if polkit_available() else '⚠️'} Polkit 授权："
                f"{'可用' if polkit_available() else '未检测到 pkexec'}",
                (
                    "✅ 原生软件包：已安装"
                    if package_command
                    else "ℹ️ 原生软件包：未安装（当前可使用源码安装）"
                ),
            ]
        )
        self.install_environment_status.set_text("\n".join(environment_lines))
        self.ibus_install_status.set_text(framework_text("IBus", ibus_status))
        self.fcitx_install_status.set_text(framework_text("Fcitx 5", fcitx_status))

        def choice_text(status) -> str:
            if status.state == "complete":
                return "✅ 已安装完整"
            if status.state == "partial":
                return "⚠️ 已安装但不完整"
            return "○ 尚未安装，可选择后执行安装"

        self.ibus_choice_status.set_text(choice_text(ibus_status))
        self.fcitx_choice_status.set_text(choice_text(fcitx_status))
        self._last_ibus_status = ibus_status
        self._last_fcitx_status = fcitx_status
        self._update_framework_specific_content()
        return False

    def _open_install_dialog(self, framework: str) -> None:
        if self._install_dialog is not None and self._install_dialog.get_visible():
            self._install_dialog.present()
            return
        is_ibus = framework == "ibus"
        title = "安装 / 修复 VoCoType（IBus）" if is_ibus else "安装 / 修复 VoCoType（Fcitx 5）"
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
        rime_schema = self._text_entry()
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

        progress_label = Gtk.Label(
            label=f"等待开始安装 VoCoType（{'IBus' if is_ibus else 'Fcitx 5'}）",
            xalign=0,
        )
        progress_label.set_line_wrap(True)
        progress_bar = Gtk.ProgressBar()
        progress_bar.set_show_text(True)
        progress_bar.set_fraction(0.0)
        progress_bar.set_text("等待开始")
        content.pack_start(progress_label, False, False, 4)
        content.pack_start(progress_bar, False, False, 4)

        text_view = Gtk.TextView()
        text_view.set_editable(False)
        text_view.set_monospace(True)
        scroller = Gtk.ScrolledWindow()
        scroller.set_vexpand(True)
        scroller.add(text_view)
        content.pack_start(scroller, True, True, 8)
        buffer = text_view.get_buffer()
        state = {"running": False, "done": False, "fraction": 0.0}
        option_widgets = [python_combo, preserve, install_deps, bootstrap_uv, rime_enabled, rime_schema, component_mode]

        def append(line: str) -> None:
            parsed_progress = parse_install_progress(line.strip())

            def update() -> bool:
                if not dialog.get_visible():
                    return False
                if parsed_progress is not None:
                    fraction, message = parsed_progress
                    fraction = max(float(state["fraction"]), fraction)
                    state["fraction"] = fraction
                    progress_bar.set_fraction(fraction)
                    progress_bar.set_text(f"{round(fraction * 100)}%")
                    progress_label.set_text(f"⏳ {message}")
                    return False
                end_iter = buffer.get_end_iter()
                buffer.insert(end_iter, line + "\n")
                text_view.scroll_to_iter(buffer.get_end_iter(), 0.0, False, 0, 0)
                return False
            GLib.idle_add(update)

        def mark_finished(ok: bool) -> bool:
            state["running"] = False
            state["done"] = True
            framework_name = "IBus" if is_ibus else "Fcitx 5"
            if ok:
                state["fraction"] = 1.0
                progress_bar.set_fraction(1.0)
                progress_bar.set_text("✅ 100%")
                progress_label.set_text("✅ 程序安装与运行验收完成")
            else:
                progress_bar.set_fraction(float(state["fraction"]))
                progress_bar.set_text("❌ 安装失败")
                progress_label.set_text("❌ 安装失败；请查看下方日志")
            self._last_lifecycle_notice = (
                f"✅ 最近一次安装 / 修复 VoCoType（{framework_name}）成功"
                if ok
                else f"❌ 最近一次安装 / 修复 VoCoType（{framework_name}）失败"
            )
            close_button.set_label("关闭")
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
            state["fraction"] = 0.02
            progress_bar.set_fraction(0.02)
            progress_bar.set_text("2%")
            progress_label.set_text(
                f"⏳ 正在准备安装 VoCoType（{'IBus' if is_ibus else 'Fcitx 5'}）…"
            )
            self._last_lifecycle_notice = (
                f"⏳ 正在安装 / 修复 VoCoType（{'IBus' if is_ibus else 'Fcitx 5'}）"
            )
            self._refresh_install_status()
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
                append("\n✅ 程序安装/修复步骤完成" if ok else "\n❌ 安装/修复失败")
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
            title=f"卸载 VoCoType（{framework_name}）",
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
        purge_runtime = Gtk.CheckButton(label="同时删除该 integration 的 Python 虚拟环境与运行缓存（共享 ASR 模型保留）")
        remove_user_data = Gtk.CheckButton(label="同时删除 VoCoType 用户配置、术语和音频设置")
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
                "VoCoType 用户数据",
                "此选项会删除 VoCoType 的统一用户配置；所有已安装 integration 都会受影响。默认关闭。",
                remove_user_data,
            ),
            False,
            False,
            0,
        )

        package_command = native_package_removal_command()
        source_fcitx_marker = Path(
            "/usr/share/vocotype/.source-fcitx-integration"
        ).is_file()
        system_ibus_component = Path(
            "/usr/share/ibus/component/vocotype.xml"
        ).is_file()
        if package_command:
            ownership_note = Gtk.Label(
                label=(
                    "ℹ️ /usr 下的 VoCoType 文件由原生软件包管理；"
                    f"本窗口不会越权删除。请使用：{package_command}"
                ),
                xalign=0,
            )
            ownership_note.set_line_wrap(True)
            options_card.pack_start(
                self._row("系统文件所有权", control=ownership_note),
                False,
                False,
                0,
            )
        elif not is_ibus and source_fcitx_marker:
            ownership_note = Gtk.Label(
                label="✅ 将通过 Polkit 一并移除源码安装器写入的系统 VoCoType（Fcitx 5）addon。",
                xalign=0,
            )
            ownership_note.set_line_wrap(True)
            options_card.pack_start(
                self._row("系统 Fcitx addon", control=ownership_note),
                False,
                False,
                0,
            )
        elif is_ibus and system_ibus_component:
            ownership_note = Gtk.Label(
                label="✅ 将通过 Polkit 一并移除非软件包管理的系统 VoCoType（IBus）component。",
                xalign=0,
            )
            ownership_note.set_line_wrap(True)
            options_card.pack_start(
                self._row("系统 IBus component", control=ownership_note),
                False,
                False,
                0,
            )
        content.pack_start(options_card, False, False, 8)

        warning = (
            f"检测到 vocotype-linux 原生软件包。本操作只清理用户级 {framework_name} 运行环境；"
            f"如需删除 /usr 下的程序，请使用：{package_command}"
            if package_command
            else (
                f"将完整卸载源码安装的 VoCoType（{framework_name}）。"
                "默认保留 ~/.config/vocotype 和共享 ASR 模型缓存。"
            )
        )
        notice = Gtk.Label(label=warning, xalign=0)
        notice.set_line_wrap(True)
        content.pack_start(notice, False, False, 8)

        progress_label = Gtk.Label(
            label=f"等待开始卸载 VoCoType（{framework_name}）",
            xalign=0,
        )
        progress_label.set_line_wrap(True)
        progress_bar = Gtk.ProgressBar()
        progress_bar.set_show_text(True)
        progress_bar.set_text("等待开始")
        progress_bar.set_pulse_step(0.06)
        content.pack_start(progress_label, False, False, 4)
        content.pack_start(progress_bar, False, False, 4)

        text_view = Gtk.TextView()
        text_view.set_editable(False)
        text_view.set_monospace(True)
        scroller = Gtk.ScrolledWindow()
        scroller.set_vexpand(True)
        scroller.add(text_view)
        content.pack_start(scroller, True, True, 8)
        buffer = text_view.get_buffer()
        state = {"running": False, "pulse_source": 0}
        option_widgets = [purge_runtime, remove_user_data]

        def pulse_progress() -> bool:
            if not state["running"] or not dialog.get_visible():
                state["pulse_source"] = 0
                return False
            progress_bar.pulse()
            return True

        def append(line: str) -> None:
            def update() -> bool:
                if not dialog.get_visible():
                    return False
                clean_line = line.strip()
                if clean_line:
                    progress_label.set_text(clean_line)
                buffer.insert(buffer.get_end_iter(), line + "\n")
                text_view.scroll_to_iter(buffer.get_end_iter(), 0.0, False, 0, 0)
                return False

            GLib.idle_add(update)

        def failure_message(output: str) -> str:
            if "SYSTEM_FCITX_REMOVE_FAILED" in output:
                return "系统 VoCoType（Fcitx 5）addon 未能移除；请查看授权与错误日志"
            if "SYSTEM_COMPONENT_REMOVE_FAILED" in output:
                return "系统 VoCoType（IBus）component 未能移除；请查看授权与错误日志"
            if "RESTART_FAILED" in output:
                return f"VoCoType 文件已清理，但 {framework_name} 重启失败"
            return "卸载失败或仅完成了部分清理；请查看下方日志"

        def mark_finished(ok: bool, output: str) -> bool:
            state["running"] = False
            pulse_source = int(state["pulse_source"])
            if pulse_source:
                GLib.source_remove(pulse_source)
                state["pulse_source"] = 0
            progress_bar.set_fraction(1.0 if ok else 0.0)
            progress_bar.set_text("✅ 卸载完成" if ok else "❌ 卸载失败")
            result_text = (
                f"✅ VoCoType（{framework_name}）卸载完成"
                if ok
                else f"❌ {failure_message(output)}"
            )
            progress_label.set_text(result_text)
            self._last_lifecycle_notice = (
                f"✅ 最近一次卸载 VoCoType（{framework_name}）成功"
                if ok
                else f"❌ 最近一次卸载 VoCoType（{framework_name}）失败"
            )
            close_button.set_sensitive(True)
            start_button.set_sensitive(False)
            self._refresh_install_status()
            close_button.grab_focus()
            return False

        def begin() -> None:
            if state["running"]:
                return
            state["running"] = True
            self._last_lifecycle_notice = f"⏳ 正在卸载 VoCoType（{framework_name}）"
            self._refresh_install_status()
            progress_bar.set_fraction(0.0)
            progress_bar.set_text("⏳ 正在卸载…")
            progress_label.set_text(f"⏳ 正在准备卸载 VoCoType（{framework_name}）…")
            state["pulse_source"] = GLib.timeout_add(100, pulse_progress)
            close_button.set_sensitive(False)
            start_button.set_sensitive(False)
            for widget in option_widgets:
                widget.set_sensitive(False)
            options = UninstallOptions(
                purge_runtime=purge_runtime.get_active(),
                remove_user_data=remove_user_data.get_active(),
                remove_system_component=(not package_command and is_ibus),
                remove_system_integration=(not package_command and not is_ibus),
            )

            def work() -> None:
                ok, output = uninstall_framework(
                    "ibus" if is_ibus else "fcitx5",
                    options=options,
                    progress=append,
                )
                append("\n✅ 卸载完成" if ok else f"\n❌ {failure_message(output)}")
                if not ok and not output:
                    append("没有收到卸载后端输出")
                GLib.idle_add(mark_finished, ok, output)

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

    def _on_check_latest_version(self, _button: Gtk.Button) -> None:
        self.doctor_version_status.set_text(
            "⏳ 正在查询 GitHub release 与完整性清单…"
        )

        def work() -> None:
            try:
                release = query_latest_release(current_version=__version__)
                if release.comparison == "older":
                    headline = (
                        f"⚠️ 当前 {release.current}，最新 {release.latest}；建议升级。"
                    )
                elif release.comparison == "newer":
                    headline = (
                        f"ℹ️ 当前 {release.current} 高于最新 release {release.latest}，"
                        "可能正在使用开发版。"
                    )
                else:
                    headline = f"✅ 当前版本 {release.current} 已是最新 release。"

                manifest = fetch_release_integrity_manifest(release.tag)
                if manifest is None:
                    message = (
                        headline
                        + "\n该 release 未提供安装完整性清单；仅完成版本号比较。"
                    )
                else:
                    report = probe_installation_integrity(manifest)
                    icon = {"pass": "✅", "warn": "⚠️", "fail": "❌"}.get(
                        report.status, "ℹ️"
                    )
                    message = (
                        f"{headline}\n{icon} 相对最新 release 的安装审计："
                        f"{report.summary}"
                    )
                    if report.status in {"warn", "fail"} and report.details:
                        message += "\n" + report.details
            except Exception as exc:  # noqa: BLE001
                message = f"❌ GitHub 版本查询失败：{exc}"
            GLib.idle_add(self.doctor_version_status.set_text, message)

        threading.Thread(target=work, daemon=True).start()

    def _on_run_doctor(self, _button: Gtk.Button | None = None) -> None:
        self.doctor_summary_label.set_text("⏳ 正在检查…")
        self.overview_summary.set_text("⏳ 正在检查…")

        def work() -> None:
            checks = run_doctor()
            GLib.idle_add(self._render_doctor, checks)

        threading.Thread(target=work, daemon=True).start()

    def _populate_doctor_list(self, container: Gtk.Box, checks: list[DoctorCheck]) -> None:
        for child in container.get_children():
            container.remove(child)
        classes = {
            "pass": "status-pass",
            "warn": "status-warn",
            "fail": "status-fail",
            "info": "status-pass",
        }
        icons = {"pass": "✅", "warn": "⚠️", "fail": "❌", "info": "ℹ️"}
        for item in checks:
            expander = Gtk.Expander()
            heading = Gtk.Label(
                label=f"{icons.get(item.status, '❓')} {item.title}：{item.summary}",
                xalign=0,
            )
            heading.set_line_wrap(True)
            heading.get_style_context().add_class(classes.get(item.status, "status-warn"))
            expander.set_label_widget(heading)
            expander.set_expanded(item.status in {"warn", "fail"})

            details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            details.set_margin_start(24)
            details.set_margin_end(8)
            details.set_margin_top(6)
            details.set_margin_bottom(8)
            if item.details:
                detail_label = Gtk.Label(label=item.details, xalign=0)
                detail_label.set_selectable(True)
                detail_label.set_line_wrap(True)
                details.pack_start(detail_label, False, False, 0)
            if item.repair_hint:
                hint = Gtk.Label(label=f"建议：{item.repair_hint}", xalign=0)
                hint.set_selectable(True)
                hint.set_line_wrap(True)
                hint.get_style_context().add_class("status-warn")
                details.pack_start(hint, False, False, 0)
            if not item.details and not item.repair_hint:
                details.pack_start(Gtk.Label(label="没有额外信息", xalign=0), False, False, 0)
            expander.add(details)
            container.pack_start(expander, False, False, 0)
        container.show_all()

    def _render_doctor(self, checks: list[DoctorCheck]) -> bool:
        self.last_doctor_checks = checks
        self._populate_doctor_list(self.doctor_list, checks)
        summary = doctor_summary(checks)
        text = (
            f"✅ 通过 {summary.get('pass', 0)} · "
            f"ℹ️ 信息 {summary.get('info', 0)} · "
            f"⚠️ 警告 {summary.get('warn', 0)} · "
            f"❌ 失败 {summary.get('fail', 0)}"
        )
        self.doctor_summary_label.set_text(text)
        self.overview_summary.set_text(text)
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

    def _feedback_text(self) -> str:
        buffer = self.feedback_view.get_buffer()
        return buffer.get_text(
            buffer.get_start_iter(), buffer.get_end_iter(), True
        ).strip()

    def _on_feedback(self, _button: Gtk.Button) -> None:
        message = self._feedback_text()
        if not message:
            self.feedback_status.set_text("请先填写反馈内容。")
            return
        category = self.feedback_category.get_active_id() or "other"
        contact = self.feedback_contact.get_text().strip()
        include_doctor = self.feedback_include_doctor.get_active()
        include_bundle = self.feedback_include_bundle.get_active()
        use_custom = self.feedback_use_custom_endpoint.get_active()
        endpoint = (
            self.feedback_endpoint.get_text().strip()
            if use_custom
            else OFFICIAL_FEEDBACK_ENDPOINT
        )
        if use_custom and not endpoint:
            self.feedback_status.set_text("已启用自定义端点，但地址为空。")
            return
        self.feedback_status.set_text("正在准备发送预览…")

        def prepare() -> None:
            try:
                checks = self.last_doctor_checks or (
                    run_doctor() if include_doctor else []
                )
                doctor_payload = (
                    [asdict(item) for item in checks] if include_doctor else None
                )
                payload = build_feedback_payload(
                    message,
                    category=category,
                    contact=contact,
                    doctor_payload=doctor_payload,
                )
                bundle = None
                if include_bundle:
                    bundle = create_support_bundle()
                    self.last_bundle_path = bundle
                GLib.idle_add(
                    self._confirm_feedback_send,
                    endpoint,
                    payload,
                    bundle,
                )
            except Exception as exc:  # noqa: BLE001
                GLib.idle_add(
                    self.feedback_status.set_text,
                    f"准备反馈失败：{exc}",
                )

        threading.Thread(target=prepare, daemon=True).start()

    def _confirm_feedback_send(
        self,
        endpoint: str,
        payload: dict[str, Any],
        bundle: Path | None,
    ) -> bool:
        preview = dict(payload)
        preview["destination"] = endpoint
        if bundle is not None:
            preview["support_bundle"] = {
                "path": str(bundle),
                "size_bytes": bundle.stat().st_size,
            }
        else:
            preview["support_bundle"] = None
        dialog = Gtk.Dialog(title="确认发送反馈", transient_for=self, modal=True)
        dialog.add_button("取消", Gtk.ResponseType.CANCEL)
        send_button = dialog.add_button("确认发送", Gtk.ResponseType.APPLY)
        send_button.get_style_context().add_class("suggested-action")
        dialog.set_default_size(760, 560)
        content = dialog.get_content_area()
        content.set_border_width(12)
        content.set_spacing(10)
        notice = Gtk.Label(
            label="以下内容将通过 HTTPS 发送。请检查路径、主机名或其他可能识别你的信息。",
            xalign=0,
        )
        notice.set_line_wrap(True)
        content.pack_start(notice, False, False, 0)
        output = Gtk.TextView()
        output.set_editable(False)
        output.set_cursor_visible(False)
        output.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        output.get_buffer().set_text(
            json.dumps(preview, ensure_ascii=False, indent=2)
        )
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.add(output)
        content.pack_start(scroller, True, True, 0)
        dialog.show_all()
        response = dialog.run()
        dialog.destroy()
        if response != Gtk.ResponseType.APPLY:
            self.feedback_status.set_text("已取消发送。")
            return False

        self.feedback_status.set_text("正在发送反馈…")

        def send() -> None:
            try:
                result = submit_feedback_payload(
                    endpoint,
                    payload,
                    bundle_path=bundle,
                )
                feedback_id = str(result.get("feedback_id", "")).strip()
                duplicate = bool(result.get("duplicate"))
                status = (
                    f"反馈已收到：{feedback_id}"
                    if feedback_id
                    else "反馈已收到。"
                )
                if duplicate:
                    status += "（已合并到相同报告）"
            except Exception as exc:  # noqa: BLE001
                status = f"反馈失败：{exc}"
            GLib.idle_add(self.feedback_status.set_text, status)

        threading.Thread(target=send, daemon=True).start()
        return False

    def _on_feedback_github(self, _button: Gtk.Button) -> None:
        message = self._feedback_text()
        if not message:
            self.feedback_status.set_text("请先填写反馈内容。")
            return
        include_doctor = self.feedback_include_doctor.get_active()
        include_bundle = self.feedback_include_bundle.get_active()
        self.feedback_status.set_text("正在准备 GitHub Issue…")

        def work() -> None:
            try:
                checks = self.last_doctor_checks or (
                    run_doctor() if include_doctor else []
                )
                doctor_text = "\n".join(
                    f"[{item.status}] {item.title}: {item.summary}"
                    for item in checks
                )
                bundle = None
                if include_bundle:
                    bundle = create_support_bundle()
                    self.last_bundle_path = bundle
                opened = open_github_issue(message, doctor_text=doctor_text)
                if opened:
                    status = "已打开 GitHub Issue 页面，请检查后提交。"
                    if bundle is not None:
                        status += f" 请手动附加支持包：{bundle}"
                else:
                    status = "无法打开浏览器。"
            except Exception as exc:  # noqa: BLE001
                status = f"准备 GitHub Issue 失败：{exc}"
            GLib.idle_add(self.feedback_status.set_text, status)

        threading.Thread(target=work, daemon=True).start()

    def _message(self, title: str, text: str, message_type: Gtk.MessageType = Gtk.MessageType.INFO) -> bool:
        """Show bounded, scrollable operation output instead of an unbounded label."""

        dialog = Gtk.Dialog(title=title, transient_for=self, modal=True)
        dialog.add_button("关闭", Gtk.ResponseType.CLOSE)
        dialog.set_default_size(680, 420)
        dialog.set_resizable(True)

        content = dialog.get_content_area()
        content.set_border_width(12)
        content.set_spacing(10)

        heading = Gtk.Label(label=title, xalign=0)
        heading.set_line_wrap(True)
        heading.get_style_context().add_class(
            "status-fail" if message_type == Gtk.MessageType.ERROR else "row-title"
        )
        content.pack_start(heading, False, False, 0)

        output = Gtk.TextView()
        output.set_editable(False)
        output.set_cursor_visible(False)
        output.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        output.get_buffer().set_text(text or "完成")

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.set_min_content_height(220)
        scroller.set_min_content_width(520)
        scroller.add(output)
        content.pack_start(scroller, True, True, 0)

        dialog.show_all()
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
