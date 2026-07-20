# VoCoType Linux

<h2 align="center">Linux 全平台离线语音输入法</h2>

**VoCoType Linux** 是基于 [VoCoType](https://github.com/233stone/vocotype-cli) 核心引擎开发的 **Linux 离线语音输入法**，同时支持 IBus 和 Fcitx 5 两大输入法框架。

> **Windows / macOS 用户**：VoCoType 原作者已实现桌面版，请访问 [vocotype.com](https://vocotype.com/)

---

## 核心特性

- **100% 离线，隐私无忧** - 所有语音识别在本地完成，不上传任何数据
- **旗舰级识别引擎** - 基于 FunASR Paraformer 模型，中英混合输入精准
- **PTT 按键说话** - 按住 F9 说话，松开自动识别并输入；`Shift+F9` 支持长句润色模式
- **语音编辑（IBus）** - `Ctrl+F9` 进入编辑指令模式，可改写/替换/插入/删除/导航/撤销重做
- **轻量化设计** - 仅需 700MB 内存，纯 CPU 推理，无需显卡
- **0.1 秒级响应** - 感受所言即所得的畅快体验
- **Fcitx 全局模块** - 在原有 Rime、拼音、Mozc 等任意 Fcitx 5 输入法中直接使用 F9，无需切换到 VoCoType
- **图形设置中心** - 安装/修复、ITN 风格、用户词典、AI endpoint、Doctor、支持包和反馈统一管理

## Demo
https://github.com/user-attachments/assets/94772920-0f9e-4dff-8da5-c9026eb23256
### 新功能! 语音编辑(v2.2.1)
[Kooha-2026-03-29-08-50-00.webm](https://github.com/user-attachments/assets/4b936014-9477-4794-8d04-aa31d34577a0)


## 支持平台

| 输入法框架 | 状态 | 说明 |
|-----------|------|------|
| **IBus** | ✅ 完整支持 | 适用于 GNOME、大多数发行版默认 |
| **Fcitx 5** | ✅ 全局 Module | 增强当前所有 Fcitx 5 输入法，不再内嵌或代理 Rime |

两个版本**可以同时安装**，共享 VoCoType 核心引擎，各自独立运行。

---

## 快速开始

### 发行包安装

GitHub Release 提供完整源码包、Python wheel/sdist，以及 DEB、RPM、Arch 包。原生系统包会预装 Fcitx 全局 module、IBus component、应用菜单入口和用户服务定义；安装事务不会运行 `pip`、下载模型或写入用户配置。安装完成后从应用菜单打开 **VoCoType 设置**，再在 GUI 中选择 Fcitx 5 或 IBus，创建独立的用户级 Python 3.12 环境并完成模型与麦克风配置。

本地构建发行资产：

```bash
make test
make release
make package-deb    # Debian / Ubuntu 构建机
make package-rpm    # Fedora / RPM 构建机
make package-arch   # Arch 构建机
```

详见：[打包与分发](packaging/README.md)。

### 图形安装与设置（推荐）

```bash
git clone https://github.com/LeonardNJU/VocoType-linux.git
cd VocoType-linux
bash installers/launch-settings.sh
```

在 **概览与安装** 页面选择 Fcitx 5 或 IBus。安装、修复和卸载都在设置窗口内完成，进度与错误直接显示在 GUI 中；需要系统权限时由 Polkit 弹出标准授权框，不会打开终端或读取管理员密码。安装后可在 GUI 中选择/测试麦克风，并使用 ITN 预览、用户词典、AI endpoint、Doctor、日志打包和反馈入口。

安装完成后可从应用菜单打开 **VoCoType 设置**，或运行：

```bash
vocotype-settings
```

### 命令行安装（高级/无桌面环境）

| Integration | 安装 | 卸载 |
|---|---|---|
| IBus | `bash ibus/scripts/install.sh` | `bash ibus/scripts/uninstall.sh` |
| Fcitx 5 | `bash fcitx5/scripts/install.sh` | `bash fcitx5/scripts/uninstall.sh` |

交互式 CLI 会询问 Python 环境、配置保留策略和确认信息。设置中心分别调用同目录下的 `install-gui.sh` 与 `uninstall-gui.sh`，两套 integration 的生命周期入口完全对称。

Fcitx 版本安装为全局 Module，无需在输入法列表中添加 VoCoType；继续使用原来的 Rime、拼音或其他输入法即可。

详细说明：[图形设置中心](docs/guides/settings-center.md)、[IBus 安装](ibus/README.md)、[Fcitx 5 安装](fcitx5/README.md)。

---

## 统一术语库与原生热词

默认 ASR 已切换为官方 Contextual Paraformer ONNX。IBus 与 Fcitx 5 共用
`~/.config/vocotype/terms.yaml`：同一条术语既可作为模型原生 hotword，也可配置
ASR 后的确定性 alias → canonical 替换，并保护标准词不被 ITN/数字规则误改。

详见：[术语库与原生热词](docs/guides/terms.md)。

---

## 可配置 ITN 与书写风格

默认启用数字与 WeTextProcessing FST ITN，并采用紧凑书写风格：

```text
二零二六年五月十一号 → 2026/05/11
下午三点二十分       → 15:20
三百二十米           → 320m
一百二十八元         → ¥128
```

设置中心可以整体关闭数字/ITN，也可以分别关闭日期、时间、路程单位和金额符号转换。术语 canonicalization 始终保留，`protect: true` 的词条不会被紧凑格式改写。

详见：[ITN 与数字格式策略](docs/guides/itn.md)。

---

## SLM 后处理配置（通用）

VoCoType 保留 `local_ephemeral` 与 `remote` 两种 provider，以及 IBus 的 `Ctrl+F9`
语音编辑链路。Fcitx 5 的远程润色默认使用 OpenAI-compatible SSE：模型生成期间在
输入面板显示可见预览，thinking/reasoning 内容不会上屏。

默认快捷键仍是：

- `F9`：极速 ASR，不调用 SLM。
- `Shift+F9`：ASR 后尝试润色。

Fcitx 5 可将 `PolishByDefault=true`，此时两者语义反转：`F9` 默认润色，
`Shift+F9` 临时跳过。

### 本地模型（按需加载）

```json
{
  "slm": {
    "enabled": true,
    "provider": "local_ephemeral",
    "model": "Qwen/Qwen3.5-0.8B",
    "local_model": "Qwen/Qwen3.5-0.8B",
    "timeout_ms": 12000,
    "warmup_timeout_ms": 90000,
    "ready_wait_ms": 2000,
    "keepalive_ms": 60000,
    "min_chars": 8,
    "max_tokens": 96,
    "edit_enabled": true,
    "edit_max_tokens": 256,
    "enable_thinking": false
  }
}
```

### 远程 API（OpenAI-compatible SSE）

```json
{
  "slm": {
    "enabled": true,
    "provider": "remote",
    "model": "gpt-4o-mini",
    "endpoint": "https://example.com/v1/chat/completions",
    "api_key": "sk-***",
    "remote_stream": true,
    "stream_idle_timeout_ms": 20000,
    "transport_timeout_ms": 0,
    "remote_max_tokens": 0,
    "min_chars": 8,
    "enable_thinking": false,
    "retry_without_proxy": true,
    "extra_headers": {},
    "extra_body": {},
    "edit_enabled": true,
    "edit_max_tokens": 256
  }
}
```

关键参数：

- `provider`：`local_ephemeral` / `remote`。
- `min_chars`：润色触发阈值，默认 `8`。
- `max_tokens`：仅用于本地模型生成预算。
- `remote_max_tokens`：远程输出上限；默认 `0`，不发送固定限制，避免长文本被截断。
- `stream_idle_timeout_ms`：最后一次 SSE 事件后的空闲超时，而非整个生成过程总时长。
- `extra_headers` / `extra_body`：provider 专属请求扩展。
- `enable_thinking`：是否允许模型 reasoning；最终文本与 Fcitx 预览都会过滤 thinking。
- `edit_enabled` / `edit_max_tokens`：IBus `Ctrl+F9` 语音编辑配置。

OpenRouter endpoint 会自动获得项目标识 header，并按其 API 映射 reasoning 参数。远程失败、
流式超时或只返回 thinking 时，Fcitx 会保留 ASR 原文供用户确认提交。

详见：[流式 AI 润色](docs/guides/slm-streaming.md)。

---

## IBus 语音编辑（Ctrl+F9）

> 说明：该功能目前由 IBus 引擎提供，Fcitx5 暂未接入同等编辑链路。

### 快捷键

- `Ctrl+F9`：语音编辑模式（先读取 surrounding text，再录音识别编辑指令）
- `Ctrl+Shift+F9`：surrounding 探针（回填 `[VT-SURR ...]` 调试信息）

### 常用语音编辑能力

- 文本修改：`把 A 改成 B`、`删除当前句`、`删除上一句`、`删除选中内容`
- 插入生成：`输入一段对海底捞商家的好评`、`输入一段关于天气的描写`
- 选择与导航：`全选`、`移动到开头`、`移动到结尾`、`左移三次`、`下一个词`
- 历史操作：`撤销/撤销修改`、`重做`
- 诊断命令：`显示上下文信息`（输出当前 `cap/del/cursor/anchor/prev/cur/sel/all`）

### 行为说明

- 若当前输入框不支持 surrounding 能力（`cap=0`），`Ctrl+F9` 会直接提示并停止。
- 若录音期间输入框内容已变化，会提示 `输入框内容已变化，请重试`，避免误改错位文本。
- 撤销策略采用“智能分流”：
  - 最近一次是语音编辑且状态匹配：走内部撤销栈
  - 否则：下发应用级撤销/重做（`Ctrl+Z` / `Ctrl+Shift+Z`）

---

## 重新安装与卸载

### 重新安装

安装脚本支持重复运行，无论是：
- 安装失败需要重试
- 升级到新版本
- 变更安装参数

直接重新运行安装脚本即可，会自动覆盖之前的安装，不会有残留。

### 卸载

推荐在 **VoCoType 设置 → 概览与安装** 中点击 **卸载 IBus** 或 **卸载 Fcitx 5**。GUI 默认只清理用户级程序与 integration 文件，并保留虚拟环境、模型以及 `~/.config/vocotype` 中的术语、音频和 AI 配置。

命令行入口：

```bash
bash ibus/scripts/uninstall.sh
bash fcitx5/scripts/uninstall.sh
```

可选参数：

- `--purge-runtime`：同时删除该 integration 的虚拟环境、模型和缓存；
- `--remove-user-data`：同时删除两套 integration 共享的 `~/.config/vocotype`，需谨慎使用；
- IBus 的 `--remove-system-component`：通过 Polkit 删除旧版安装器留下、且不受原生软件包管理的系统 component。

通过 DEB、RPM 或 Arch 安装的 `/usr` 文件始终由包管理器维护。设置中心只清理用户级运行环境，并显示 `pacman`、`dnf` 或 `apt` 的软件包卸载命令。

---

## 架构设计

```text
VoCoType Linux
├── app/                    # 两套 integration 共用的核心运行时
├── settings_center/        # GTK 设置、安装、卸载与诊断
├── ibus/                   # IBus 引擎、数据和生命周期入口
├── fcitx5/                 # Fcitx module、backend、IPC 和生命周期入口
├── installers/             # 两套 integration 共用的安装/卸载实现
├── packaging/              # 原生包配方、构建器、清单与 smoke
├── tools/                  # benchmark 和开发排障工具
├── tests/                  # 自动化行为测试
└── docs/                   # 用户、排障和维护者文档
```

IBus 和 Fcitx 5 是**地位对等、实现独立**的 integration，共享 VoCoType 核心及安装基础设施。完整目录约束见 [仓库目录规范](docs/development/repository-layout.md)。

---

## 版本对比

| 特性 | IBus 版本 | Fcitx 5 版本 |
|-----|----------|-------------|
| 输入法框架 | IBus 输入法引擎 | Fcitx 5 全局 Module |
| 实现语言 | 纯 Python | C++ + Python (IPC) |
| 安装位置 | `~/.local/share/vocotype/` | `~/.local/share/vocotype-fcitx5/` |
| 适用桌面 | GNOME 等 | 任意使用 Fcitx 5 的桌面 |

---

## 使用场景

### 日常应用
- 聊天通讯：微信、QQ、Telegram、Slack、Discord
- 文档撰写：文章、报告、邮件、日记、笔记
- 网页浏览：搜索、表单、评论

### 开发场景
- 编写代码注释和文档
- Git Commit Message
- 与 AI 工具对话（ChatGPT、Claude、Cursor）
- Issue & PR 描述

---

## 核心优势

| 特性 | VoCoType Linux | 云端输入法 |
|------|---------------|-----------|
| **隐私安全** | 本地离线，绝不上传 | 数据上传云端 |
| **网络依赖** | 完全无需联网 | 必须联网 |
| **响应速度** | 0.1 秒级 | 受网速影响 |
| **数据安全** | 100% 本地 | 存在泄密风险 |

---

## 系统要求

- **操作系统**: Linux (Fedora, Ubuntu, Debian, Arch 等)
- **Python**: 3.11-3.12（onnxruntime 暂不支持 3.13+）
- **内存**: 最低 4GB，推荐 8GB
- **CPU**: 双核以上，无需 GPU

### 资源占用

| 状态 | 内存 | CPU |
|------|------|-----|
| 待机 | 200-300MB | ~0% |
| 录音 | - | 5-10% |
| 识别 | ~700MB | 100-200%（0.1-0.5秒）|

### SLM 开销基准测试（ASR vs ASR+SLM）

新增脚本：`tools/benchmarks/slm-pipeline.py`，用于对比：
- `ASR-only`（对应 F9 快速模式）
- `ASR+SLM`（对应 Shift+F9 长句模式）

示例（以 `Qwen/Qwen3.5-0.8B` 为例）：

```bash
python tools/benchmarks/slm-pipeline.py ./samples \
  --pattern "*.wav" \
  --repeat 5 \
  --warmup 1 \
  --slm-model Qwen/Qwen3.5-0.8B \
  --slm-endpoint http://127.0.0.1:18080/v1/chat/completions \
  --output-json /tmp/vocotype-benchmark.json
```

可选参数：
- `--slm-pid <PID>`：统计 SLM 服务进程的 CPU/RSS 增量
- `--disable-slm`：只测 ASR 基线，不测对照组

---

## 文档

- [IBus 版本安装指南](ibus/README.md)
- [Fcitx 5 版本安装指南](fcitx5/README.md)
- [术语库与原生热词](docs/guides/terms.md)
- [ITN 与数字格式策略](docs/guides/itn.md)
- [图形设置中心、Doctor 与反馈](docs/guides/settings-center.md)
- [流式 AI 润色](docs/guides/slm-streaming.md)
- [Rime 拼音配置指南](docs/guides/rime.md)（主要面向 IBus；Fcitx 版本直接使用现有 fcitx5-rime）

---

## 作者

**Leonard Li** - 开发与维护

📧 联系邮箱: [leo@lsamc.website](mailto:leo@lsamc.website)

## 联系我们

- **Bug 与建议**：请使用 GitHub Issues
- **原项目**：[VoCoType](https://github.com/233stone/vocotype-cli)

---

## 致谢

本项目基于以下优秀的开源项目：

- **[VoCoType](https://github.com/233stone/vocotype-cli)** - 原始项目，提供了强大的离线语音识别核心引擎
- **[FunASR](https://github.com/modelscope/FunASR)** - 阿里巴巴达摩院开源的语音识别框架
- **[QuQu](https://github.com/yan5xu/ququ)** - 优秀的开源项目，提供了重要的技术参考

---

## 第三方依赖与模型许可

本项目依赖的第三方库与模型均受各自许可证约束。详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

使用的模型：
- `iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-onnx`
- `iic/speech_fsmn_vad_zh-cn-16k-common-onnx`
- `iic/punc_ct-transformer_zh-cn-common-vocab272727-onnx`

## 📄 许可证

本项目继承原 VoCoType 项目的许可证。请查看 [LICENSE](LICENSE) 文件了解详情。

## Star History

<a href="https://www.star-history.com/?repos=LeonardNJU%VocoType-linux&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=LeonardNJU/VocoType-linux&type=date&theme=dark&legend=top-left&sealed_token=PxMzEEMfOA3R97IHVMG0zQqC1xVjuoA3cMbkfPzjtmhwAbL9n3oQVqHhhzNgKOwqwD0CShQpk_ostzzb2_m-8qA_U5IFphEK5su_nHoI1HKzcVq3-8oq8HZGL79TUmv5WtGBpBCqOtdrgrF8_KKxta_MCbS9IscnCEtMju92w8_qG8TtIZw_zZ68xYli" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=LeonardNJU/VocoType-linux&type=date&legend=top-left&sealed_token=PxMzEEMfOA3R97IHVMG0zQqC1xVjuoA3cMbkfPzjtmhwAbL9n3oQVqHhhzNgKOwqwD0CShQpk_ostzzb2_m-8qA_U5IFphEK5su_nHoI1HKzcVq3-8oq8HZGL79TUmv5WtGBpBCqOtdrgrF8_KKxta_MCbS9IscnCEtMju92w8_qG8TtIZw_zZ68xYli" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=LeonardNJU/VocoType-linux&type=date&legend=top-left&sealed_token=PxMzEEMfOA3R97IHVMG0zQqC1xVjuoA3cMbkfPzjtmhwAbL9n3oQVqHhhzNgKOwqwD0CShQpk_ostzzb2_m-8qA_U5IFphEK5su_nHoI1HKzcVq3-8oq8HZGL79TUmv5WtGBpBCqOtdrgrF8_KKxta_MCbS9IscnCEtMju92w8_qG8TtIZw_zZ68xYli" />
 </picture>
</a>
