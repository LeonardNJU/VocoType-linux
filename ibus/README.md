# VoCoType IBus 版本

VoCoType 离线语音输入法的 IBus 版本实现。

## ITN 与数字格式

图形设置中心可以整体开关数字/ITN，并分别选择日期、时间、路程单位和金额的紧凑书写风格。详见 [`docs/guides/itn.md`](../docs/guides/itn.md)。

## 术语库与原生热词

IBus 与 Fcitx 5 共用 `~/.config/vocotype/terms.yaml`。默认 Contextual Paraformer
会使用其中的原生 hotword，识别后再执行确定性术语标准化和数字保护。
详见 [`docs/guides/terms.md`](../docs/guides/terms.md)。

## 功能特性

- **语音输入** - 按住 F9 说话，松开自动识别并输入；`Shift+F9` 为长句模式（可选 SLM 润色）
- **语音编辑** - `Ctrl+F9` 读取输入框上下文并执行语音编辑指令（替换/删除/插入/导航/撤销重做）
- **上下文探针** - `Ctrl+Shift+F9` 输出 surrounding 调试信息，便于验证不同应用兼容性
- **Rime 拼音** - 完整版支持 Rime 拼音输入
- **完全离线** - 所有识别在本地完成，零网络依赖
- **轻量高效** - 纯 CPU 推理，仅需 700MB 内存
- **快速响应** - 0.1 秒级识别速度

## 系统要求

- **Linux 发行版**: 支持 IBus 的任何发行版 (Fedora, Ubuntu, Debian, Arch 等)
- **Python**: 3.11-3.12（onnxruntime 暂不支持 3.13+）
- **IBus**: 系统已安装并启用 IBus 输入法框架

### 可选依赖（完整版）

- **librime-devel** - Rime 开发库
- **ibus-rime** - 共享 Rime 配置（如果已安装）
- **rime-ice** - 现代词库和配置方案

## 版本选择

| 版本 | 功能 | 适用场景 |
|------|------|---------|
| **纯语音版** | F9 极速语音输入 + Shift+F9 长句模式 | 只需要语音输入，使用其他拼音输入法 |
| **完整版** | F9 语音 + Shift+F9 长句 + Rime 拼音 | 一个输入法同时支持语音和拼音 |

## 图形安装

从源码目录运行 `bash installers/launch-settings.sh`，在“概览与安装”选择 **安装 / 修复 VoCoType（IBus）** 或 **卸载 VoCoType（IBus）**。Python 环境、Rime、component 位置和系统依赖安装均在 GUI 中选择。需要系统权限时会弹出 Polkit 授权框，不会打开终端。

传统交互脚本仍保留用于兼容和开发排障。

## 安装

图形设置中心会随 IBus 安装器一同安装。安装后运行 `vocotype-settings` 可管理 IBus 与 Fcitx 共用配置、Doctor 和反馈。

### 纯语音版（推荐新手）

```bash
cd VocoType-linux

# 安装 VoCoType IBus 引擎
./ibus/scripts/install.sh

# 重启 IBus
ibus restart
```

安装脚本会询问使用的 Python 环境：
- 项目虚拟环境（推荐）
- 用户级虚拟环境 (`~/.local/share/vocotype/.venv`)
- 系统 Python（省空间，需自行安装依赖）
- 手动指定 Python 解释器（例如 conda 环境的 `python`）

安装脚本还会询问是否启用 `Shift+F9` 长句 SLM 润色：
- 不启用（默认）：不安装/拉取 SLM 模型，`Shift+F9` 不会触发润色
- 启用：写入 `~/.config/vocotype/ibus.json` 的 `slm` 配置，并可选择：
  - 本地模型（`local_ephemeral`）：按下预热，润色后释放
  - 远程 API（`remote`）：配置 `model`、`endpoint`、`api_key`

> **模型下载**：首次运行时，程序会自动下载约 1GB 的模型文件。

### 完整版（语音 + Rime 拼音）

#### 1. 安装系统依赖

```bash
# Fedora / RHEL / CentOS
sudo dnf install librime-devel ibus-rime

# Ubuntu / Debian
sudo apt install librime-dev ibus-rime

# Arch Linux
sudo pacman -S librime ibus-rime
```

#### 2. 安装 VoCoType

```bash
cd VocoType-linux

# 安装 VoCoType IBus 引擎
./ibus/scripts/install.sh

# 安装 pyrime（根据脚本提示的虚拟环境路径）
# 例如：
~/.local/share/vocotype/.venv/bin/pip install pyrime

# 重启 IBus
ibus restart
```

## 使用方法

### 1. 添加输入法

1. 打开系统设置 → 键盘 → 输入源
2. 点击 "+" 添加输入源
3. 滑到最底下点三个点 (⋮)
4. 搜索 "voco"，选择 "中文"
5. 选择 "VoCoType Voice Input" 并添加

### 2. 开始使用

1. 切换到 VoCoType 输入法 (通常是 `Super + Space` 或 `Ctrl + Space`)
2. **语音输入**:
   - 按住 `F9`：极速模式（仅 ASR + 标点）
   - 按住 `Shift+F9`：长句模式（ASR + 标点 + 可选 SLM 润色）
   - 按住 `Ctrl+F9`：语音编辑模式（读取 surrounding + 识别编辑指令）
   - 按住 `Ctrl+Shift+F9`：输出 surrounding 探针信息
3. **拼音输入**（完整版）: 直接打字，Rime 处理并显示候选词

## Rime 配置

完整版使用以下配置目录：

```
~/.config/ibus/rime/
```

这意味着：
- 与 ibus-rime 共享配置目录
- 推荐使用 [rime-ice（雾凇拼音）](https://github.com/iDvel/rime-ice) 获得更好体验
- 所有 Rime 自定义配置都适用

> 提示：安装脚本会把选择的方案记录在 `~/.config/vocotype/rime/user.yaml`，
> 启动时优先使用该方案。

详见：[docs/guides/rime.md](../docs/guides/rime.md)

## 高级配置

### 重新配置音频设备

```bash
.venv/bin/python installers/setup-audio.py
```

### 自定义快捷键

默认使用 F9 作为 PTT (Push-to-Talk) 键。如需修改，编辑 `ibus/engine.py`:

```python
PTT_KEYVAL = IBus.KEY_F9  # 修改为其他按键
```

可选按键：`IBus.KEY_F8`, `IBus.KEY_F10`, `IBus.KEY_Control_L` 等

### 长句模式（Shift+F9）配置

在 `~/.config/vocotype/ibus.json` 中配置 `slm`（默认关闭）。

推荐使用本地一次性加载（按下 `Shift+F9` 预加载，润色后立即释放）：

```json
{
  "slm": {
    "enabled": true,
    "provider": "local_ephemeral",
    "model": "Qwen/Qwen3.5-0.8B",
    "local_model": "Qwen/Qwen3.5-0.8B",
    "warmup_timeout_ms": 90000,
    "keepalive_ms": 60000,
    "ready_wait_ms": 2000,
    "timeout_ms": 12000,
    "min_chars": 8,
    "max_tokens": 96,
    "edit_enabled": true,
    "edit_max_tokens": 256,
    "enable_thinking": false
  }
}
```

- `F9` 不会触发 SLM，保持低延迟
- `Shift+F9` 才会触发 SLM
- `local_ephemeral` 会在长句完成后按 `keepalive_ms` 保活并最终释放模型内存
- 若 SLM 调用失败，会显示错误提示，不提交回退原文

远程 API（OpenAI-compatible）示例：

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
    "edit_enabled": true,
    "edit_max_tokens": 256,
    "retry_without_proxy": true,
    "extra_headers": {},
    "extra_body": {}
  }
}
```

常用参数：

- `provider`：`local_ephemeral` / `remote`。
- `min_chars`：长句触发阈值，默认 `8`。
- `max_tokens`：本地模型生成预算。
- `remote_max_tokens`：远程输出上限；默认 `0`，不发送固定限制。
- `stream_idle_timeout_ms`：远程流式事件空闲超时。
- `enable_thinking`：是否允许模型 reasoning；最终提交会过滤 thinking。
- `retry_without_proxy`：代理请求失败时尝试直连。
- `edit_enabled` / `edit_max_tokens`：`Ctrl+F9` 编辑链路配置。

IBus 继续采用最终结果式 UI，不显示逐 token 面板预览；远程 SSE 仍可用于更合理的空闲超时和
取消固定 token 上限。Fcitx 5 的实时预览协议见
[`docs/guides/slm-streaming.md`](../docs/guides/slm-streaming.md)。

### 语音编辑（Ctrl+F9）详解

#### 触发流程

1. 按下 `Ctrl+F9` 后先检测 surrounding 能力。
2. 若不支持（`cap=0`），立即提示并结束，不启动录音。
3. 若支持，录音并识别编辑指令。
4. 优先执行确定性命令；未命中时交给 SLM 根据上下文完成整段编辑。

#### 常用指令示例

- 替换/删除：`把刚才那句话改成更正式一点`、`删除当前句`、`删除上一句`
- 插入生成：`输入一段对海底捞商家的好评`、`输入一段关于天气的描写`
- 导航/选择：`移动到开头`、`左移三次`、`下一个词`、`选中下一个词`、`全选`
- 历史操作：`撤销`、`撤销修改`、`重做`
- 诊断：`显示上下文信息`（输出 `[VT-SURR ...]`）

#### 撤销与重做策略

- 如果最近一次改动来自语音编辑，且文本状态匹配内部记录，执行内部撤销/重做。
- 否则自动下发应用级快捷键：
  - 撤销：`Ctrl+Z`
  - 重做：`Ctrl+Shift+Z`

#### 兼容性说明

- 文本替换依赖 `delete_surrounding_text` 能力，状态会显示为 `del=ok/no/?`。
- 导航命令通过 `forward_key_event` 下发；不同应用/Wayland 客户端可能有拦截差异。
- 为避免错位编辑，录音结束执行前会校验输入框内容是否仍与快照一致；不一致会报 `输入框内容已变化，请重试`。

### surrounding 探针脚本

仓库提供 `tools/diagnostics/test-surrounding-probe.sh`，用于快速验证不同应用中的 surrounding 抽取能力：

```bash
bash tools/diagnostics/test-surrounding-probe.sh
```

## 安装后验证

需要检查当前用户的真实 IBus 安装、Python 依赖、component，以及可选的 Rime/pyrime 集成时运行：

```bash
python tools/diagnostics/validate-ibus-install.py
```

该命令读取真实的 `~/.local` 与用户 Rime 配置，因此属于显式安装后诊断，不作为普通 pytest 用例运行。DEB、RPM、Arch 的可重复安装契约由 `packaging/tests/` 和 GitHub CI 验证。

## 常见问题 (FAQ)

### Q: 我的数据安全吗？

**100% 安全**。所有语音识别均在本地离线完成，您的音频数据不会上传到任何服务器。

### Q: 需要 GPU/显卡吗？资源占用如何？

**不需要 GPU，只使用 CPU**。资源占用非常轻量：

| 状态 | 内存 | CPU |
|------|------|-----|
| 待机 | 200-300MB | ~0% |
| 录音 | - | 5-10%（单核）|
| 识别 | ~700MB | 100-200%（多核，0.1-0.5秒）|

**推荐配置**：
- 最低：4GB RAM + 双核 CPU
- 推荐：8GB RAM + 四核 CPU

### Q: 识别准确率如何？

基于 FunASR Paraformer 模型，中文普通话场景下准确率超过 95%，支持中英混合输入。

### Q: 可以在哪些应用中使用？

任何支持文本输入的应用：
- 终端、代码编辑器 (VS Code, Vim, Emacs)
- 浏览器 (Chrome, Firefox)
- 办公软件 (LibreOffice, WPS)
- 聊天工具 (Telegram, Slack, Discord)

## 卸载

图形设置中心提供 **卸载 VoCoType（IBus）**，并在窗口内显示清理进度。命令行使用：

```bash
bash ibus/scripts/uninstall.sh
```

默认删除 IBus 程序代码、用户 component 和 launcher，保留 `.venv`、模型缓存以及 `~/.config/vocotype`。使用 `--purge-runtime` 删除运行环境；只有明确使用 `--remove-user-data` 时才会删除共享术语、音频和 AI 配置。

旧版安装器可能把 component 写入 `/usr/share/ibus/component`。GUI 可通过 Polkit 清理不受软件包管理的旧文件；若检测到 `vocotype-linux` 原生软件包，则必须使用系统包管理器卸载，脚本不会直接删除 `/usr` 中的软件包文件。

## 与 Fcitx 5 版本的区别

| 特性 | IBus 版本 | Fcitx 5 版本 |
|-----|----------|-------------|
| 输入法框架 | IBus | Fcitx 5 |
| 实现语言 | 纯 Python | C++ + Python (IPC) |
| Rime 配置 | `~/.config/ibus/rime/` | `~/.local/share/fcitx5/rime/` |
| 安装位置 | `~/.local/share/vocotype/` | `~/.local/share/vocotype-fcitx5/` |

两个版本**可以同时安装**，互不干扰。

---

**相关文档**:
- [项目主页](../README.md)
- [Fcitx 5 版本](../fcitx5/README.md)
- [Rime 配置指南](../docs/guides/rime.md)
