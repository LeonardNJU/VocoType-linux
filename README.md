# VoCoType-linux

<p align="center">
  <img src="web/og-image.png" alt="VoCoType Linux — 按住语音快捷键说话，松开即可输入文字" width="100%">
</p>

<p align="center"><strong>Linux 与 macOS 上的原生本地语音输入：按住说话，松开上屏。</strong></p>

<p align="center">
  <a href="https://vocotype-linux.lsamc.website">官网</a> ·
  <a href="https://github.com/LeonardNJU/VocoType-linux/releases">下载</a> ·
  <a href="https://vocotype-linux.lsamc.website/docs/">文档</a> ·
  <a href="https://github.com/LeonardNJU/VocoType-linux/issues">问题反馈</a>
</p>

**VoCoType-linux** 是面向 Linux 与 macOS 的开源语音输入工具。核心语音识别在本地运行，无需 GPU；可选连接本机、局域网或云端的 OpenAI-compatible API，对长句进行润色并根据输入框上下文执行语音编辑。

- Linux：原生支持 **Fcitx 5** 与 **IBus**；
- macOS：原生使用 **InputMethodKit Palette Input Method**，与当前键盘输入源共存；
- 三个平台前端共用 C++ Core、FunASR worker、术语、ITN、AI润色与受限语音编辑协议。

## 📰 VoCoType-linux V5 正式版

**[VoCoType-linux v5.0.0](https://github.com/LeonardNJU/VocoType-linux/releases/tag/v5.0.0)** 是 V5 原生架构的首个稳定版本，正式覆盖 Linux Fcitx 5、Linux IBus 与 Apple Silicon macOS。

- 本地 FunASR 最终识别、可选实时预览、热词、术语保护与 ITN 全部由共享 C++ Core 提供；
- `F9` 普通听写、`Shift+F9` AI 润色和 `Ctrl+F9` 语音编辑在三套前端保持一致语义；
- 按下语音快捷键时即并行准备最终 ASR、标点模型和当前热词图，显著降低冷启动后的松键等待；
- Fcitx 5 普通 F9 已改为异步、可取消任务：按 `Esc`、继续输入、开始新语音操作或输入框失焦都会立即取消等待；
- 设置中心的 AI“连接测试”明确为可选诊断；保存有效配置后可直接使用 AI 功能，无需每次启动后测试；
- 正式版安装资产包含 macOS arm64 DMG，以及 Debian、RPM、Arch 的 Universal、IBus-only、Fcitx5-only 软件包和统一校验和。

[前往 V5 正式版 Release 下载](https://github.com/LeonardNJU/VocoType-linux/releases/tag/v5.0.0)。macOS Apple Silicon 用户请选择 `VoCoType-linux-5.0.0-macOS-arm64.dmg`。

macOS 安装包目前仍采用 ad-hoc 签名、尚未经过 Apple notarization；首次打开可能需要在“系统设置 → 隐私与安全性”中选择“仍要打开”。

<details>
<summary><strong>为什么移植到 macOS 后仍然叫 VoCoType-linux？</strong></summary>

原版 VocoType 在维护者自己的 Mac 上会闪退；与此同时，我认为 VoCoType-linux 的全局按住说话、实时预览、语音编辑、图形设置中心、用户词典和诊断设计更适合完整的桌面工作流。因此，这次不是重新包装另一套产品，而是把 VoCoType-linux 本身原生移植到 macOS。

macOS原生听写在我的实际工作流中也不理想：中文识别准确率不够稳定；说到一半开始手动打字后，通常不能自然继续同一次听写，需要关闭后重新打开；启停和续说操作比较繁琐。它也没有长句润色、基于上下文的语音编辑、热词、别名替换和术语保护等 VoCoType-linux高级功能。

为了保持仓库、配置格式、发布资产、文档、问题追踪和用户认知的连续性，macOS版本继续沿用 **VoCoType-linux** 名称，不另起一个产品名。这里的“Linux”是项目历史名称，不再表示只支持 Linux。

</details>

## 为什么使用 VoCoType-linux

- **本地语音识别**：语音默认不离开设备，断网也能输入。
- **系统级输入**：在聊天窗口、浏览器、编辑器、终端和 AI 工具中直接提交文字。
- **中文输入优化**：支持中英混合识别、原生热词、用户术语与数字格式化。
- **按住即说**：默认 `F9`快速识别、`Shift+F9` AI润色、`Ctrl+F9`语音编辑。
- **语音编辑**：结合 surrounding text理解替换、改写、翻译、导航、LaTeX与撤销重做指令。
- **图形化管理**：安装、模型、麦克风、AI配置、用户词典、Doctor和反馈均有原生界面。
- **纯 CPU 可用**：普通笔记本和台式机即可运行，无需独立显卡。

## Demo

https://github.com/user-attachments/assets/94772920-0f9e-4dff-8da5-c9026eb23256

IBus语音编辑：

https://github.com/user-attachments/assets/4b936014-9477-4794-8d04-aa31d34577a0

## 快速开始

### macOS：拖入 Applications

系统要求：**Apple Silicon（arm64），macOS 13或更高版本**。

1. 从 [V5 正式版 Release](https://github.com/LeonardNJU/VocoType-linux/releases/tag/v5.0.0) 下载 `VoCoType-linux-5.0.0-macOS-arm64.dmg`；
2. 打开 DMG，将 `VoCoType-linux.app`拖到右侧 `Applications`；
3. 从“应用程序”打开 VoCoType-linux；
4. 首次启动会把内置输入法安装到 `~/Library/Input Methods/VoCoType-linux.app`并激活；
5. 允许麦克风权限，然后在任意文本框中按住 `F9`说话。

V5 正式版仍采用 **ad-hoc签名且尚未经过 Apple公证**。首次打开若被 Gatekeeper阻止：先尝试打开一次并关闭提示，然后进入 **系统设置 → 隐私与安全性 → 仍要打开**，在确认框中再次选择“仍要打开”。这是为该 App建立本机例外，不是导入或信任一张 ad-hoc证书。

升级时直接用新版本覆盖 `/Applications/VoCoType-linux.app`并启动；App会更新内置输入法，同时保留配置、用户词典和模型。完整说明见 [macOS安装与排障](docs/getting-started/macos.md)。

### Linux：安装发行包

在 [V5 正式版 Release](https://github.com/LeonardNJU/VocoType-linux/releases/tag/v5.0.0) 下载适合发行版与输入法框架的软件包：

```bash
# Debian / Ubuntu
sudo apt install ./vocotype-linux_*.deb

# Fedora / RHEL 系
sudo dnf install ./vocotype-linux-*.rpm

# Arch Linux
sudo pacman -U ./vocotype-linux-*.pkg.tar.zst
```

随后从应用菜单打开 **VoCoType设置**，安装或修复 Fcitx 5 / IBus、校验模型并在 Playground完成麦克风测试。Release提供 Universal、IBus-only和Fcitx5-only三种 flavor；只安装其中一种。

### Linux：从源码构建

```bash
git clone https://github.com/LeonardNJU/VocoType-linux.git
cd VocoType-linux

# Fcitx 5
bash scripts/install/fcitx5/install.sh --install-system-deps --download-models

# 或 IBus
bash scripts/install/ibus/install.sh --install-system-deps --download-models
```

### Nix / NixOS

```bash
nix run github:LeonardNJU/VocoType-linux/v5.0.0#settings
nix build github:LeonardNJU/VocoType-linux/v5.0.0#vocotype-fcitx5
nix build github:LeonardNJU/VocoType-linux/v5.0.0#vocotype-ibus
nix build github:LeonardNJU/VocoType-linux/v5.0.0#vocotype-universal
```

详见 [Nix与NixOS文档](docs/getting-started/nix.md)。

## 基本使用

以下是默认值。Linux和macOS设置中心均可管理相关功能；Linux支持录制自定义快捷键，macOS使用系统级全局热键。

| 默认快捷键 | 功能 |
|---|---|
| `F9` | 按住录音；可选实时预览，松开后由完整离线识别提交最终文字 |
| `Shift+F9` | 识别后使用已配置的 AI模型润色 |
| `Ctrl+F9` | 语音编辑：改写、替换、插入、删除、翻译、导航、撤销等 |
| `Esc`或点击浮层 | 取消当前语音操作并立即关闭状态浮层 |

### macOS

VoCoType-linux使用 Palette Input Method，与当前英文、拼音或其他键盘输入源共存。无需反复切换输入法；激活一次后即可在支持文本输入的应用中使用全局快捷键。一次语音操作会固定绑定按下快捷键时的输入框，即使系统通知或其他应用中途激活，也不会把最终文字提交到错误客户端。

### Fcitx 5

VoCoType-linux作为全局 Module工作，安装后无需把它添加到输入法列表。继续使用现有 Rime、拼音、Mozc或键盘布局，直接按语音快捷键。

### IBus

VoCoType-linux作为独立 IBus引擎运行，并提供与 Fcitx 5相同的普通听写、润色和语音编辑语义。

## 主要功能

### 离线与实时语音识别

使用 FunASR Contextual Paraformer ONNX在本地完成最终识别；可选启用 FunASR 2-pass，在说话时持续更新预览。实时结果只用于展示，松键后仍由完整录音、原生热词、标点、术语和 ITN链路唯一决定最终文字。

### 图形化用户词典

用户词典不要求普通用户直接编辑 YAML：

- **新增热词**：填写 canonical，可添加多条 aliases，并分别选择热词与保护；
- **新增保护词**：只填写需要保护的固定表达；
- **导入用户词典**：验证 YAML后原子替换；
- **热更新词典**：外部批量修改当前文件后重新加载；
- **在 Finder / 文件管理器中显示**：需要手工编辑时直接定位权威文件。

YAML仍是可迁移、可批量维护的权威格式。图形化新增会保留既有注释和排版，并拒绝重复 canonical或保护词。

### 逆文本标准化（ITN）

可将口语中的日期、时间、距离和金额转换为适合书写的格式，例如：

```text
二零二六年五月十一号 → 2026/05/11
下午三点二十分       → 15:20
三百二十米           → 320m
一百二十八元         → ¥128
```

### AI润色与语音编辑

AI页配置 OpenAI-compatible endpoint、model、API key、超时、SSE与thinking。端点可以位于本机、局域网或云端；VoCoType-linux不启动或管理模型进程。

- `Shift+F9`：断句、纠错和长句润色；
- `Ctrl+F9`：结合输入框全文、光标与选区生成受限编辑计划；
- 支持替换、插入、删除、翻译、评论、导航、LaTeX修改、撤销与重做；
- 失败时保留原始识别文本，不执行未验证的自由形式操作。

若端点不在本机，转写文本会发送到该接口；语音编辑还会发送当前应用提供的 surrounding text、光标与选区。

### Playground、诊断与设置中心

Linux使用 GTK 3/C++设置中心，macOS使用 AppKit原生设置中心。两端均覆盖模型、麦克风、AI、术语、诊断和教程；macOS另外负责首次安装/升级 InputMethodKit组件。设置文本区域支持系统原生 `⌘Z`，同时兼容 `Ctrl+Z`撤销和对应重做快捷键。

## 支持范围

| 平台 / 输入框架 | 支持方式 | 发行资产 |
|---|---|---|
| **macOS / InputMethodKit** | Palette Input Method，与当前键盘输入源共存 | Apple Silicon DMG |
| **Linux / Fcitx 5** | 全局 Module，可继续使用 Rime / 拼音 / Mozc | DEB、RPM、Arch、Nix |
| **Linux / IBus** | 独立输入法引擎 | DEB、RPM、Arch、Nix |

## 系统要求

- macOS 13+，Apple Silicon arm64；
- 或 Linux：Debian、Ubuntu、Fedora、Arch等主流发行版，使用 Fcitx 5或 IBus；
- 最低 4 GB内存，推荐 8 GB；
- 无需 GPU；
- 从源码构建需要 C++20、CMake与对应系统开发库；日常运行不需要 Python。

## 文档与支持

- [macOS安装、升级与Gatekeeper](docs/getting-started/macos.md)
- [Linux安装与首次配置](docs/getting-started/installation.md)
- [图形设置中心](docs/guides/settings-center.md)
- [用户词典与原生热词](docs/guides/terms.md)
- [Fcitx 5安装与排障](docs/integrations/fcitx5.md)
- [IBus安装与排障](docs/integrations/ibus.md)
- [语音快捷键](docs/guides/shortcuts.md)
- [语音编辑兼容性与局限](docs/guides/voice-editing.md)
- [常见问题](docs/troubleshooting/faq.md)
- [版本记录](CHANGELOG.md)

## 项目说明

VoCoType-linux基于 [VoCoType / vocotype-cli](https://github.com/233stone/vocotype-cli) 的语音输入核心发展，并使用 [FunASR](https://github.com/modelscope/FunASR) 等开源项目。V5将本仓库自己的桌面交互、原生 Core、Linux输入法集成和设置体系进一步移植到 macOS，但项目名称与仓库地址保持不变。

第三方依赖与模型分别受其自身许可证约束，详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。项目许可证见 [LICENSE](LICENSE)。

## Star History

<a href="https://www.star-history.com/?repos=LeonardNJU%2FVocoType-linux&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=LeonardNJU/VocoType-linux&type=date&theme=dark&legend=top-left&sealed_token=9qVwhB2qAFbyd-LfOlImyauJ8e-jHky6BFgdd0DX22zJt_sGU4tQSzqRnqdw21QbPGsjB0yiVov7pW8nazHO3vGR-jAXu4z7OHPZviKfz8AqEYV1unLwXg" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=LeonardNJU/VocoType-linux&type=date&legend=top-left&sealed_token=9qVwhB2qAFbyd-LfOlImyauJ8e-jHky6BFgdd0DX22zJt_sGU4tQSzqRnqdw21QbPGsjB0yiVov7pW8nazHO3vGR-jAXu4z7OHPZviKfz8AqEYV1unLwXg" />
   <img alt="VoCoType Linux Star History Chart" src="https://api.star-history.com/chart?repos=LeonardNJU/VocoType-linux&type=date&legend=top-left&sealed_token=9qVwhB2qAFbyd-LfOlImyauJ8e-jHky6BFgdd0DX22zJt_sGU4tQSzqRnqdw21QbPGsjB0yiVov7pW8nazHO3vGR-jAXu4z7OHPZviKfz8AqEYV1unLwXg" />
 </picture>
</a>

维护者：**Leonard Li** · [leo@lsamc.website](mailto:leo@lsamc.website)
