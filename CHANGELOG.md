# Changelog

All notable changes to VoCoType Linux will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Renamed AI “health check” UI to an optional connection test and documented that it sends one real LLM request which may incur latency or provider charges.
- Enabling and saving AI configuration now clearly permits immediate Shift+F9, Ctrl+F9, and Playground use without a prior connection test.

### Fixed

- Fcitx 5 ordinary F9 recognition now uses the asynchronous Core transcription-task protocol instead of a single blocking socket request; Escape, continued typing, a new voice shortcut, and focus loss cancel the pending result immediately.
- Added a 120-second final-ASR watchdog for Fcitx 5 while retaining short per-poll socket timeouts, so a stuck backend cannot leave the frontend waiting indefinitely.
- Removed automatic macOS AI requests when opening Settings, entering the AI page, enabling AI, or editing endpoint fields.
- Removed misleading runtime, Playground, tutorial, and integration text that treated a successful connection test as persistent activation state.

## [5.0.0-beta.5] - 2026-07-30

### Added

- Added a shared `asr_prepare` protocol that starts the final offline recognizer and compiles the current terminology-derived hotword graph before final transcription is requested.
- Added recording-scoped ASR preparation leases to macOS InputMethodKit, Linux IBus, and the Fcitx 5 module, including the Universal package paths.
- Added release contracts that require every supported frontend to start final-ASR preparation at voice-key press and wait for its first preparation attempt before final inference.

### Changed

- Voice recording now begins immediately while a cold Core, offline ASR worker, punctuation model, and hotword graph are prepared in parallel with the user's speech.
- Core cold startup initializes the offline and streaming ASR workers concurrently instead of serially.
- The default offline-worker idle timeout increased from 60 seconds to 300 seconds; active recordings refresh the worker lease so long dictation cannot unload it mid-session.
- Fcitx backend recovery remains serialized by a single-owner start gate without delaying microphone capture, and IBus no longer waits synchronously for Core startup before recording.

### Fixed

- Fixed macOS, IBus, Fcitx 5, and Universal builds paying final-model cold-start and hotword-compilation latency only after the user released the voice shortcut.
- Fixed long recordings failing to overlap model preparation with speaking time.
- Fixed short cold recordings racing the background preparation request; final recognition now waits for that recording's first preparation attempt before proceeding.
- Fixed a possible Fcitx service-start gate ownership race in which a waiting prewarm thread could clear another thread's active start marker.

## [5.0.0-beta.4] - 2026-07-29

### Added

- Added a native Apple Silicon macOS distribution using InputMethodKit and AppKit, with the same local Core, FunASR workers, terminology, normalization, AI polishing, and voice-edit protocol used by the Linux product.
- Added a conventional drag-to-Applications DMG. The visible settings application carries a signed input-method payload and installs, registers, enables, activates, and upgrades it under the current user's `~/Library/Input Methods` directory.
- Added a native macOS settings center covering models, audio, AI configuration, diagnostics, tutorials, and a graphical user-dictionary workflow for adding hotwords, aliases, and protected terms without editing YAML directly.
- Added dictionary import, Finder reveal, external-edit hot reload, atomic writes, duplicate detection, and preservation of existing YAML comments and formatting where possible.
- Added clickable status panes, Escape/click cancellation, terminal-message timeouts, and regression smoke tests for click cleanup, stale timers, short recordings, busy-state gating, and actual microphone-start timing.
- Added macOS arm64 CI and Release packaging contracts so the DMG joins the nine Linux installers and the unified checksum set.

### Changed

- VoCoType-linux now officially supports Linux and macOS while retaining the historical project, repository, application, and configuration name.
- macOS settings text fields support native Command-Z plus Ctrl-Z undo, with Command-Shift-Z and Ctrl-Shift-Z redo.
- A voice operation now retains the controller and text client that accepted the initial hotkey press instead of consulting whichever InputMethodKit client happens to be active at key release.
- Global Carbon hotkey reloads are deferred until the current key cycle and voice operation have ended.
- Minimum recording duration on macOS is measured from the recorder's real `recording` event, not from the initial F9 press.

### Fixed

- Fixed the first macOS recording after installation committing to a transient system notification client or leaving two overlapping recorder timelines.
- Fixed F9 release events being lost when InputMethodKit activated another client and re-registered Carbon hotkeys while the key was still held.
- Fixed first-run CoreAudio initialization making release wait many seconds for a WAV that had never begun recording; pre-ready releases now cancel asynchronously in milliseconds.
- Fixed a macOS-only crash where short recordings left a null JSON result and completion logging called `value()` on it.
- Fixed short-recording and cancellation paths leaving the status pane visible indefinitely, leaving a ghost `recording=true` state, or blocking later F9 operations.
- Fixed the settings Close Window command occasionally targeting a system window instead of the VoCoType settings window.

## [4.1.2-beta.1] - 2026-07-28

### Fixed

- Accepted numeric GTK boolean values in ITN preview requests while continuing to distinguish malformed JSON from valid requests with invalid field types.
- Re-resolved PortAudio devices by stable name within each active runtime, negotiated mono or stereo capture, downmixed stereo input for ASR, and avoided stale device indices across PortAudio restarts.
- Hardened settings restart and AI Playground readiness gates for the Linux desktop workflow.

## [4.1.1-beta.3] - 2026-07-24

### Fixed

- Fixed recorded Fcitx shortcuts that appeared saved in the settings UI but remained `F9` in the running addon and `vocotype.conf`. The settings center now updates the live addon through Fcitx `Controller1.SetConfig` instead of editing the file and immediately restarting Fcitx, which allowed the old in-memory configuration to overwrite the new value.
- Saving now reads back and verifies both the running Fcitx configuration and the persisted configuration file before reporting success. Installed Fcitx integrations no longer fall back to an unverified direct file write.
- Removed the duplicate Fcitx shortcut copy from VoCoType JSON. `config.json` now owns shared audio/ASR/AI/UI settings only, `ibus.json` owns IBus shortcuts only, and `vocotype.conf` is the sole persistent source for Fcitx shortcuts and Fcitx-specific options.
- Added an idempotent legacy-layout migration: shared fields move from `fcitx5-backend.json` into `config.json`, IBus shortcuts move into `ibus.json`, and the legacy file is archived. Existing `vocotype.conf` always wins; legacy JSON seeds Fcitx only when no persistent Fcitx config exists.
- Fcitx configuration and conflict scanning now consistently honor `XDG_CONFIG_HOME` instead of assuming `~/.config`.
- Suppressed harmless ALSA/JACK backend-probe diagnostics during audio-device enumeration and enumerate input/output devices in one PortAudio session, so launching the settings center no longer prints repeated error-looking noise to the terminal.

### Added

- Added end-to-end fake Fcitx controller coverage for settings application, live readback, disk persistence, restart-free behavior, role-specific layout migration, existing-Fcitx precedence, missing-Fcitx initialization, and idempotent second startup.
- Doctor now reports shared-config responsibility, IBus shortcut configuration, and Fcitx persistent/live synchronization as separate checks instead of presenting three copies as peer configuration sources.

## [4.1.1-beta.2] - 2026-07-24

### Fixed

- Fixed the Fcitx 5 push-to-talk regression introduced with recordable shortcuts: beginning a recording no longer clears the active shortcut before its key-release event arrives, so releasing `F9`, `Alt_R`, or another configured key now stops recording and starts transcription.
- Fixed false shortcut conflicts caused by configuration files left behind by uninstalled Fcitx addons. Addon-specific shortcut files are scanned only while matching addon metadata is still installed; active Fcitx, KDE, GNOME, and X11 conflicts remain blocked.

### Added

- Added regression contracts for Fcitx press/release session initialization and headless tests covering both stale and installed addon shortcut configurations.

## [4.1.1-beta.1] - 2026-07-24

### Changed

- Reorganized the repository around module ownership: product code now lives under `src/`, input-method adapters under `src/integrations/`, the feedback service owns its deployment assets under `src/services/feedback/`, and the project website lives under `web/`.
- Consolidated installation, diagnostics, site generation, benchmarks, and repository-level tests under `scripts/`, while keeping package builders and package audits together under `packaging/`.
- Moved the Nix implementation beside Debian, RPM, and Arch packaging at `packaging/nix/package.nix`; standard `flake.nix` and `flake.lock` entry points remain at the repository root.
- Centralized user, developer, architecture, integration, and service documentation under `docs/`, removing duplicate component README files.

### Added

- Added a repository-layout ADR and architecture guide documenting ownership boundaries, entry points, and the rationale for colocating implementation, tests, and deployment assets.
- Added structural contract checks that reject obsolete top-level directories, scattered component documentation, stale source paths, and unsupported repository layouts.

### Removed

- Removed legacy top-level `native`, `fcitx5`, `ibus`, `feedback_service`, `deploy`, `installers`, `tools`, `tests`, `site`, `data`, and `nix` implementation directories after migrating their maintained contents into the new module structure.

## [4.1.0-beta.1] - 2026-07-23

### Added

- Added recorded, independently configurable shortcuts for normal transcription, AI polishing, and voice editing across Fcitx 5 and IBus. The settings center rejects unsafe printable/navigation keys, duplicate VoCoType bindings, known Fcitx/KDE/GNOME conflicts, and occupied X11 root grabs.
- Added a locked source-built Nix flake for `x86_64-linux` and `aarch64-linux`, with universal, IBus-only, and Fcitx5-only outputs plus a full GitHub Actions build/smoke job.
- Added a headless shortcut validation probe and deterministic regression tests for unsafe keys, desktop conflicts, and right-side modifier shortcuts such as `Alt_R`.

### Changed

- Fcitx and IBus now select voice mode from the configured action shortcut rather than inferring polishing/editing from Shift/Ctrl on one hard-coded F9 key.
- The native Fcitx module accepts compile-time or environment-injected recorder/backend paths so Nix store installations do not depend on FHS `/usr` paths or a systemd user unit.
- Invalid shortcut values written manually to JSON or Fcitx configuration are rejected again by the runtime and fall back to the safe defaults.

## [4.0.0-beta.1] - 2026-07-23

### Added

- Added a compiled PortAudio recorder/player, checksum-pinned ModelScope model manager, GTK settings center, native IBus/librime engine, and native Fcitx 5 module.
- Added native settings coverage for install/repair/uninstall, input/output devices, waveform playback, ASR and voice-edit Playground, ITN, terminology, SLM, Rime schema, version checks, integrity checks, support bundles, private feedback, and GitHub issue creation.
- Restored the Beta3 settings experience in C++ with a GTK HeaderBar, StackSidebar navigation, scrollable card pages, synchronized General Settings and Playground audio controls, framework-specific tutorials, card-based Doctor results, and the full feedback form.
- Added a compiled Boost.Beast/SQLite feedback receiver with multipart/JSON uploads, HMAC rate limiting, deduplication, private attachments, retention, backups, and an operator CLI.
- Added a compiled static documentation generator and repository-wide native architecture contracts.

### Changed

- The entire repository product path is now C++/CMake/shell: desktop clients, core and workers, settings UI, feedback service, installation, package/release tooling, tests, and documentation publishing no longer require Python.
- DEB, RPM, and Arch packages contain only ELF executables, shared libraries, resources, and lifecycle scripts; source archives contain no Python source or dependency manifest.
- Fcitx 5 and IBus share the same C++ core, audio recorder, normalization/terminology layer, OpenAI-compatible SSE client, and validated voice-edit planner.
- Fcitx polishing now shows `正在润色... （等待模型输出 XXs/<timeout>s）` on the first line and the rough ASR text on the next row.
- Source and graphical installers preserve existing configuration and model caches, create native payload checksums, and restart Fcitx through the graphical desktop session.
- IBus-only controls are now visible only while IBus is selected; the IBus Rime schema is chosen from an enumerated dropdown of installed schemas. Fcitx-only panel, output, and composing controls are hidden in IBus mode.

### Fixed

- Fixed Ctrl+F9 full-text replacement in gedit and other GTK clients: VoCoType now validates the surrounding snapshot before editing, then sends delete-surrounding and replacement commit in the same ordered input-method transaction instead of mistaking a stale surrounding-text cache for deletion failure.
- Removed the destructive failure mode that could delete the source text, suppress the replacement, and report that the input box did not support replacement.
- Removed clipboard injection fallbacks and internal `del=? sur=1` capability diagnostics from the user-facing Fcitx panel.
- Fixed the native Core lifecycle so settings, Fcitx, and IBus reuse the persistent user service when available. Settings no longer kill the service and fork a parent-bound temporary Core; Fcitx automatically starts the service and retries F9 when the socket is missing.
- Fixed settings startup with Beta3/legacy configurations that stored switches as numeric `0/1` values instead of JSON booleans.
- Fixed native Core parsing of legacy boolean settings. Numeric/string values such as `asr_streaming.enabled = 1` and `slm.enabled = 1` no longer silently fall back to disabled, so enabling 2-pass now starts the online worker and produces live partial text.
- Fixed upgrades from the legacy standalone Fcitx input method. Install / repair now backs up and migrates `~/.config/fcitx5/profile`, removes stale `Name=vocotype` entries, restores Rime or another valid default input method, detects disabled addons through `GetAddons`, enables `vocotype` through `SetAddonsState`, restarts Fcitx, and verifies the active addon state. Doctor now reports both addon state and legacy profile references.
- Fixed native IBus component metadata so the engine binary, user registration, and packaged system component all report the repository release version instead of a hard-coded V3 value.
- Build `vocotype-core` separately on each target distribution while keeping only the FunASR workers and inference libraries portable, avoiding cross-distribution libc/libcurl symbol-version leakage.
- Finalize the target-distribution Core before writing native payload checksums, and use one metadata-driven RPM selector across CI and release builds.

### Removed

- Removed all Python client/server implementations, Python package metadata, virtual environments, wheelhouses, Python release assets, and Python fallback launchers.
- Removed runtime and build dependencies on PyGObject, NumPy, SoundDevice, PyYAML, Python FunASR wrappers, FastAPI/Uvicorn, and MkDocs.

## [3.0.0-beta.3] - 2026-07-23

### Added

- Added a shared “取消句尾句号” switch under General Settings. It removes one final Chinese full stop or ASCII period from F9 / Shift+F9 voice commits while preserving question marks, exclamation marks, ordinary Rime typing, and Ctrl+F9 voice-edit replacements.

### Changed

- Moved the real AI endpoint/model health check directly above the “启用 AI 功能” switch. Automatic first-enable probing now displays its in-progress and final status where users can see it.

### Fixed

- Native-package repair now removes stale source-install launchers and desktop entries under `~/.local` that shadow `/usr/bin/vocotype-settings` and force the private ASR Python to import GTK/PyGObject.
- The source-installed settings launcher now selects an interpreter only after it can import the complete settings application.
- Unified trailing-period behavior across Fcitx 5 and IBus while preserving the existing opt-in default.


## [3.0.0-beta.2] - 2026-07-22

### Fixed

- The native `vocotype-settings` launcher now selects a distro Python only after it can import the complete GTK settings application. On Arch, it no longer picks an unrelated Python 3.12 installation that cannot import the distro `python-gobject` package.
- IBus now calls the distro librime C API through an in-tree, standard-library `ctypes` adapter instead of an external compiled Python binding. Universal and IBus packages declare the correct distro-specific librime, deployment-tool, and schema-data dependencies; deploy an isolated selected schema; and smoke-test a real keyboard event, preedit, and candidate list on Ubuntu, Fedora, and Arch.
- Replaced the guarded WeTextProcessing/Pynini fallback with expanded deterministic Chinese classifier rules. All normalization and terminology regressions remain covered while the package-local wheelhouse drops by roughly 160 MiB.
- DEB, RPM, and Arch packages now declare NumPy as a settings-center bootstrap dependency, matching the top-level Playground import used before the private ASR runtime is created.
- Playground microphone enumeration, recording, WAV processing, and PortAudio playback now run through the private Python 3.12 audio worker. The GTK bootstrap no longer silently treats missing system `sounddevice`/`soundfile` modules as an empty device list.
- Doctor now distinguishes the distribution Python used by the GTK settings center from the private Python 3.12 ASR/audio runtime. Runtime dependencies are checked with the canonical installer probe, and the removed WeTextProcessing ITN module is no longer reported as missing.
- Universal and specialized package payloads under `/usr` no longer count as an installed user integration. IBus or Fcitx 5 is reported as absent until the current user has runtime code, launchers, services, or user registration artifacts.
- The overview page now distinguishes “the package provides system components” from “the current user configured this integration,” preventing package-only IBus files from appearing as a partial installation.
- Installation status refreshes when the user returns to the overview page and through a new explicit “刷新状态” button, in addition to installation/uninstallation lifecycle refreshes.
- Native-package smoke tests now execute the real settings launcher probe, and runtime tests can isolate the system streaming prefix from packages installed on the developer host.


## [3.0.0-beta.1] - 2026-07-22

### Added

- Added reproducible source archives, Python wheel/sdist builds, a shared native-package staging contract, and DEB/RPM/Arch build recipes.
- Added Python 3.11/3.12 CI, package-layout and launcher behavior tests, real Fcitx multiarch staging tests, and validation-gated GitHub Release publishing with checksums and a machine-readable manifest.
- Fcitx 5 and IBus now share a fully graphical install/repair workflow. All choices and logs stay in the settings window; missing system packages and system-level IBus component registration use desktop Polkit authorization dialogs through `pkexec`, with no terminal password prompt.
- Fcitx 5 now installs a true global `Category=Module` addon: `F9` and its modifiers work with the user's existing Rime, Pinyin, Mozc, keyboard, or other Fcitx input method without proxying ordinary key events.
- Added a shared `~/.config/vocotype/terms.yaml` terminology layer with deterministic canonical replacements, protected spans, live reload, legacy Geequlim dictionary compatibility, and native Contextual Paraformer hotwords.
- Added guarded Chinese ITN with `WeTextProcessing==1.2.0`, an expanded numeric regression matrix, and independently configurable compact date, time, distance, and currency styles.
- Added a GTK settings center for graphical install/repair, synchronized IBus/Fcitx configuration, terminology editing, AI connection testing, Doctor checks, privacy-safe support bundles, tutorials, and feedback submission/GitHub fallback.
- Added OpenAI-compatible SSE polishing events, Fcitx live previews, asynchronous start/poll/cancel tasks, OpenRouter reasoning/header support, and configurable stream idle timeouts.
- Added one shared surrounding-text voice-editing pipeline for IBus and Fcitx 5. The configured SLM now interprets every command, resolves ASR homophones from context, and returns a validated `replace`, `key_actions`, or `no_op` plan; local adapters only verify and execute the plan.
- Added optional native FunASR 2-pass streaming ASR previews for both IBus and Fcitx 5. Partial hypotheses update the preedit while recording, while the complete offline Contextual Paraformer pipeline remains the sole source of committed text.
- Added installation-integrity manifests, local/remote version checks, a consolidated settings experience, framework-specific configuration panels, tutorials, and expanded Playground diagnostics.
- Added an official privacy-conscious feedback service with multipart support-bundle uploads, deduplication, rate limits, retention policies, and an administrative triage CLI.

### Changed

- DEB, RPM, and Arch releases now publish universal, IBus-only, and Fcitx5-only complete packages. All flavors include the audited native 2-pass runtime and locked Python 3.12 runtime closure; specialized flavors omit the other integration and its system dependency. Installation never compiles VoCoType or third-party dependencies locally.
- Audio decoding and resampling now use soundfile, NumPy, and SciPy end to end; VoCoType passes contiguous NumPy waveforms directly to FunASR ONNX and no longer contains a GStreamer/PyGObject compatibility path.
- User-facing configuration messages now describe one VoCoType configuration instead of implying that saving settings installs or configures both input frameworks.
- Native package-manager transactions remain offline and noninteractive. The graphical settings center creates the user runtime from package-local wheels and downloads only the selected models and Python runtime when needed.
- The Fcitx module version now follows `vocotype_version.py`, and packaged installations reuse the system module/component instead of recompiling or requesting duplicate Polkit authorization.
- The default ASR model is now the official Contextual Paraformer ONNX snapshot; both empty and configured native-hotword inference paths are supported.
- Fcitx 5 no longer embeds `pyrime`, creates a separate Rime session, or requires users to add VoCoType as an input method.
- AI polishing and voice editing now use one OpenAI-compatible API contract. The endpoint may be local or remote; VoCoType no longer contains a local model worker, scheduler, warmup, keepalive, or PyTorch/Transformers dependency path.
- IBus and Fcitx 5 can optionally show mutable ASR preedit while recording; release immediately enters the original full-recording offline recognition path. Remote polishing calls can still consume SSE internally for idle-timeout and long-output improvements.
- Numeric/ITN rewriting can now be disabled at runtime while terminology canonicalization remains active; compact styles default to ISO-like dates, 24-hour times, SI distance symbols, and `¥` currency output.
- Python distribution metadata now describes the combined IBus/Fcitx 5 Linux package as `vocotype-linux`.
- Installation examples consistently use the `VocoType-linux` clone directory.

### Fixed

- Restored the Fcitx asynchronous transcription and polish-poll IPC methods and made unresolved module symbols a link-time error.
- Native-package CI now selects every flavor by exact package name and distro version, so universal, IBus, and Fcitx packages cannot be confused.
- Disabled invalid RPM debugsource side packages for flavors without compiled source.
- Separated the distro Python/GTK bootstrap from the isolated Python 3.12 ASR runtime, preserving Ubuntu 22.04 support.
- Replaced the Debian multiarch streaming-worker ELF symlink with an executable wrapper so `$ORIGIN` resolves from the private runtime directory.
- Locked distro-specific PyGObject wheels to verified versions and added byte-for-byte payload manifests, wheel ZIP/CRC validation, and pre-install archive audits for DEB, RPM, and Arch.
- Ubuntu Python 3.11/3.12 CI now installs the verified PyGObject 3.50.2 binding under an explicit resolver constraint instead of drifting to a newer girepository-2.0-only release.
- Native package markers now record their owning package manager, so uninstall guidance remains correct even when multiple package-manager binaries are present; legacy packages retain PATH-based fallback detection.
- Prevented Debian reproducibility tooling from rewriting package-local wheels and corrupting ZIP64 metadata.
- Normalized GitHub-safe asset filenames before generating manifests and checksums, and made dry runs assemble and validate the exact downloadable Release asset set.
- Recordings shorter than the configured minimum duration are rejected consistently by the shared ASR service, IBus, and Fcitx 5 instead of entering inference with unusable audio.
- Ubuntu 22.04/24.04 now install a Python 3.12-compatible PyGObject release without requiring the newer `girepository-2.0` toolchain.
- IBus 1.5.26 no longer fails to import when optional `OSK` and `SYNC_PROCESS_KEY` capability constants are absent.
- The system-Python installer validates the complete FunASR ONNX runtime before installing the IBus launcher.
- Legacy `local_ephemeral` configurations are disabled with a migration message instead of attempting to start an embedded model worker.


## [2.2.3] - 2026-04-06

### Changed

- **普通 F9 数字输入更顺畅**：
  - 在常规语音输入链路中新增中文数字到阿拉伯数字的后处理
  - 支持日期、序号、百分比、小数与常见口语简写数字表达
  - 例如：`二零二六年四月五号` -> `2026年4月5号`，`第三十二章` -> `第32章`

### Fixed

- **减少数字误判**：
  - 避免把 `了解一下` 误转成 `了解1下`
  - 避免把 `三四下车`、`三四下` 这类近似表达误拼成连续数字

## [2.2.1] - 2026-03-29

### Added

- **IBus 语音编辑模式（`Ctrl+F9`）**：
  - 新增 surrounding 能力门控：`cap=0` 时直接提示并停止
  - 新增 SLM 指令编辑链路：基于输入框全文 + 光标/选区执行改写
  - 新增确定性编辑命令（替换/删除/插入/格式化/剪贴板/撤销重做）
  - 新增“输入类生成指令”：如“输入一段…/写一段…/生成一段…”
  - 新增导航命令下发（行首/行尾/词级移动/全选/选词）
  - 新增上下文诊断命令：`显示上下文信息` 输出 `[VT-SURR ...]`
- **IBus surrounding 探针快捷键**：
  - 新增 `Ctrl+Shift+F9` 探针回填
  - 新增脚本 `scripts/diagnostics/test-surrounding-probe.sh` 用于多场景兼容性测试

### Changed

- **编辑状态展示优化**：
  - 录音阶段提示从 preedit 改为 auxiliary，避免覆盖选中文本
  - 增加环境状态提示（`sur/del/active/sel`）
- **撤销/重做策略升级**：
  - 从“仅内部编辑历史”改为智能分流：
    - 最近一次为语音编辑且状态匹配：内部撤销栈
    - 其他情况：下发应用级 `Ctrl+Z / Ctrl+Shift+Z`

### Fixed

- **修复选中文本编辑不可用**：避免编辑状态文本临时替换选中内容导致快照失配
- **修复替换重复上屏**：替换前后确认 `delete_surrounding_text` 结果，失败时拒绝提交
- **修复上下文输出后无法撤销**：`commit_only` 路径补充历史入栈
- **增强激活态校验**：录音/编辑/导航前检查引擎激活状态，失活时取消操作

### Documentation

- 更新根文档与 IBus 文档：
  - 新增 `Ctrl+F9` / `Ctrl+Shift+F9` 使用说明
  - 新增常见编辑/导航/生成类语音指令示例
  - 补充 `slm.edit_enabled`、`slm.edit_max_tokens` 配置说明
  - 补充 surrounding 探针脚本用法

## [2.2.0] - 2026-03-28 (pre-release)

### Added

- **新增 LLM 后处理链路（长句模式）**：
  - `Shift+F9` 新增长句模式后处理（ASR + 标点 + 可选 SLM/LLM 润色）
  - 新增本地一次性加载 worker：按下预热、释放后自动回收
  - 新增后处理基准脚本：`scripts/benchmarks/slm-pipeline.py`
  - 新增后处理单元测试：`tests/test_slm_polisher.py`

### Changed

- **安装脚本增强（IBus/Fcitx5）**：
  - SLM 保持可选安装，不启用时不安装模型
  - 启用后可交互选择：
    - 本地模型（`local_ephemeral`）
    - 远程 API（`remote`）
  - 远程 API 模式支持交互写入：`model`、`endpoint`、`api_key`
- **默认阈值优化**：
  - `min_chars` 默认从 `20` 下调到 `8`，减少长句模式被 `too_short` 跳过的概率
- **远程稳定性改进**：
  - 远程请求失败时增加“直连重试（绕过代理）”机制，降低代理环境下的偶发失败

### Documentation

- 更新根文档、IBus、Fcitx5 文档：
  - 新增本地模型与远程 API 两种配置方式
  - 补充 SLM 参数说明和使用建议
  - 补充失败提示与调试方式

## [2.1.2] - 2026-01-21

### Fixed

- **修复**：Ctrl+Space 无法切换 Rime ascii_mode 的问题
  - **问题**：用户在 `default.custom.yaml` 中配置 `Ctrl+Space` 切换 `ascii_mode`，但在 VoCoType 中不生效
  - **原因**：IBus 和 Fcitx5 后端错误地将 `Ctrl+Space` 作为输入法切换热键拦截，导致按键未传递给 Rime
  - **解决**：移除 `Ctrl+Space` 拦截，仅保留 `Super+Space` 作为输入法切换热键，允许 Rime 按照用户配置处理 `Ctrl+Space`
  - **影响范围**：IBus (`ibus/engine.py:574-579`) 和 Fcitx5 (`fcitx5/addon/vocotype.cpp:441-447`) 后端

## [2.1.1] - 2026-01-20

### Changed

- **Python 版本要求调整**：
  - 支持版本：Python 3.11–3.12
  - **不再支持 Python 3.10**

- **PyGObject 版本限制**：
  - 限制为 `<3.51`，避免 Ubuntu 22.04 因缺少 `libgirepository-2` 导致安装失败

- **安装脚本改进**：
  - 项目/用户级虚拟环境优先使用 `uv` 工具
  - 系统 Python 安装仅在用户明确选择时才遍历

### Fixed

- **修复**：Fcitx5 插件路径检测
  - 补充 `/usr/lib/x86_64-linux-gnu/fcitx5` 路径（Ubuntu 系统修复）
  - 确保在 Debian/Ubuntu 系统上正确检测插件目录

### Documentation

- **更新**：Debian/Ubuntu 依赖说明
  - 说明 Ubuntu 22.04 的 librime/ibus-rime 版本偏旧
  - 建议使用 Rime 功能时手动编译安装 librime + ibus-rime

### Compatibility Notes

- ⚠️ **不兼容变更**：不再支持 Python 3.10，请使用 Python 3.11 或 3.12
- ⚠️ **Ubuntu 22.04**：若使用 Rime 功能，建议手动编译安装 librime + ibus-rime

## [2.1.0]

### Changed

- **代码重构**：重整代码结构，提升可维护性
- **功能增强**：增加输入方案选择功能

### Fixed

- **修复**：多个安装不稳定问题
  - 提升安装成功率
  - 改进依赖检测和安装流程

## [2.0.0]

### Added

- **Fcitx5 支持**：新增 Fcitx5 输入法框架支持
  - 项目从 `vocotype-ibus` 更名为 `vocotype-linux`
  - 同时支持 IBus 和 Fcitx5 两种输入法框架
  - 用户可根据系统环境选择对应的安装脚本

### Changed

- **项目更名**：`vocotype-ibus` → `vocotype-linux`
  - 反映多框架支持的定位
  - 更广泛的 Linux 桌面环境兼容性

## [1.1.0] - 2026-01-02

### Added

#### 🎯 Rime 拼音输入集成（可选）

- **完整版输入法**：现在可以选择安装"完整版"，在同一个输入法内同时支持：
  - **F9 语音输入**：按住 F9 说话，松开后自动识别
  - **拼音输入**：直接打字，Rime 引擎处理拼音输入并显示候选词
  - 一个输入法搞定所有需求，无需切换

- **纯语音版（推荐新手）**：保持原有的纯语音输入功能
  - 仅 F9 语音输入
  - 依赖少，安装简单
  - 可与其他拼音输入法（如 ibus-rime）配合使用

- **Rime 配置共享**：完整版使用 `~/.config/ibus/rime/` 作为配置目录
  - 与 ibus-rime 共享词库和配置
  - 如果已经配置过 ibus-rime，所有设置和词库都会自动继承
  - 无需重复配置

- **优雅降级**：即使安装了完整版，如果 pyrime 不可用，引擎会自动切换到纯语音模式

#### 🚀 安装体验改进

- **交互式安装向导**：
  ```
  请选择安装版本：
    [1] 纯语音版（推荐新手）- 仅语音输入，依赖少
    [2] 完整版 - 语音 + Rime 拼音输入，一个输入法全搞定
  ```

- **多平台自动检测与安装**：
  - 自动检测 Linux 发行版（Fedora/RHEL、Debian/Ubuntu、Arch Linux）
  - 提供对应的系统依赖安装命令
  - 可选择自动安装或手动安装系统依赖
  - 智能检测 librime-devel 是否已安装，避免重复安装

- **Python 环境选择**：安装时可选择：
  - 项目虚拟环境（推荐）
  - 用户级虚拟环境
  - 系统 Python

- **依赖管理优化**：
  - 优先使用 `uv` 工具（如果可用）创建虚拟环境和安装依赖
  - 自动回退到 `python3 -m venv` 和 `pip`

### Changed

#### ⚙️ 技术架构改进

- **Rime 集成方式完全重写**：
  - **移除**：基于 IBus InputContext 代理方式（存在架构缺陷）
  - **新增**：直接使用 `pyrime` 库调用 `librime`
  - **优势**：
    - 无阻塞、无超时问题
    - 更高效的按键处理
    - 更可靠的候选词显示

- **按键处理优化**：
  - 正确的 IBus 到 Rime modifier mask 转换
  - 支持 Shift、Ctrl、Alt、Lock 等修饰键
  - 不再手动调用 `post_process_key_event()`（由 IBus 框架自动处理）

- **UI 更新改进**：
  - 使用 Rime Context API 直接获取预编辑文本和候选词
  - 正确设置下划线样式和光标位置
  - 支持候选词注释（comment）显示

#### 📦 依赖变更

- **核心依赖**（必需）：
  ```toml
  sounddevice==0.5.2
  librosa==0.11.0
  soundfile==0.13.1
  funasr_onnx==0.4.1
  jieba==0.42.1
  PyGObject>=3.42.0
  modelscope==1.30.0
  torch>=2.9.1
  ```

- **可选依赖**（新增）：
  ```toml
  [project.optional-dependencies]
  rime = ["pyrime>=0.2.1"]
  full = ["pyrime>=0.2.1"]
  ```

- **系统依赖**（完整版需要）：
  - Fedora/RHEL: `librime-devel ibus-rime`
  - Debian/Ubuntu: `librime-dev ibus-rime`
  - Arch Linux: `librime ibus-rime`

### Fixed

- **修复**：引擎激活超时问题
  - **问题**：使用 InputContext 代理 Rime 时，`set_engine()` 调用阻塞导致超时
  - **解决**：切换到 pyrime 直接集成，彻底消除阻塞

- **修复**：GObject 警告
  - **问题**：`g_object_is_floating: assertion 'G_IS_OBJECT (object)' failed`
  - **原因**：错误地手动调用 `post_process_key_event()`
  - **解决**：移除手动调用，由 IBus 框架自动处理

- **修复**：pyrime 二进制兼容性问题
  - **问题**：Python 3.12 构建的 .so 文件无法在 Python 3.13 中使用
  - **解决**：为每个 Python 版本正确编译对应的二进制模块

### Documentation

- **完全重写 README**：
  - 新增两种版本对比表
  - 详细的分版本安装指南
  - 功能对比和使用场景说明
  - 常见问题解答更新

- **安装脚本改进**：
  - 清晰的版本选择提示
  - 多平台支持说明
  - 依赖安装引导

## [1.0.0] - Initial Release

### Added

- 基于 VoCoType 核心引擎的 IBus 输入法实现
- F9 PTT (Push-to-Talk) 语音输入
- 基于 FunASR Paraformer 的离线语音识别
- 交互式音频设备配置向导
- 自动模型下载
- 用户级安装支持（`~/.local/`）

### Features

- 100% 离线，隐私安全
- 0.1 秒级识别响应
- 700MB 内存占用
- 纯 CPU 推理，无需 GPU
- 中英混合输入支持
- 识别准确率 >95%

---

## 升级指南

### 从 1.0.0 升级到 1.1.0

#### 选项 1：保持纯语音版

如果您只需要语音输入功能，无需任何操作。现有安装继续正常工作。

#### 选项 2：升级到完整版（语音 + Rime）

1. **安装系统依赖**：

   ```bash
   # Fedora / RHEL
   sudo dnf install librime-devel ibus-rime

   # Ubuntu / Debian
   sudo apt install librime-dev ibus-rime

   # Arch Linux
   sudo pacman -S librime ibus-rime
   ```

2. **安装 pyrime**：

   ```bash
   # 如果使用项目虚拟环境
   .venv/bin/pip install pyrime

   # 如果使用用户级虚拟环境
   ~/.local/share/vocotype/.venv/bin/pip install pyrime
   ```

3. **重启 IBus**：

   ```bash
   ibus restart
   ```

4. **验证**：切换到 VoCoType 输入法，尝试：
   - 按住 F9 说话（语音输入）
   - 直接打字（拼音输入）

#### 全新安装

建议重新运行安装脚本，它会引导您选择合适的版本：

```bash
cd VocoType-linux
./scripts/install/ibus/install.sh
```

---

## 技术细节

### Rime 集成实现

**v1.0.0（已移除）**：
```python
# ❌ 旧方法：通过 InputContext 代理
self._rime_context = IBus.InputContext(...)
self._rime_context.set_engine("rime")  # 阻塞！
```

**v1.1.0（当前）**：
```python
# ✅ 新方法：直接使用 pyrime
from pyrime.session import Session
self._rime_session = Session(traits=traits, api=api)
handled = self._rime_session.process_key(keyval, rime_mask)
```

### 配置目录结构

```
~/.config/
├── vocotype/
│   └── audio.conf          # VoCoType 音频配置
└── ibus/
    └── rime/               # Rime 配置（与 ibus-rime 共享）
        ├── default.yaml
        ├── luna_pinyin.yaml
        └── ...

~/.local/share/
├── vocotype/               # VoCoType 安装目录
│   ├── app/
│   ├── ibus/
│   └── .venv/
└── ibus/
    └── component/
        └── vocotype.xml    # IBus 组件配置
```

---

## 致谢

- **[pyrime](https://github.com/TypeDuck-HK/pyrime)** - 优秀的 librime Python 绑定
- **[ibus-rime](https://github.com/rime/ibus-rime)** - Rime IBus 输入法，为我们的集成提供了配置共享基础
- **[RIME](https://rime.im/)** - 强大的开源中文输入法引擎
