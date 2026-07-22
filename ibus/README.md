# VoCoType IBus

VoCoType 的 IBus 集成同时提供离线语音输入、语音编辑和普通 Rime 键盘输入。

## 功能

- `F9`：低延迟离线语音输入
- `Shift+F9`：语音识别后通过用户配置的 OpenAI-compatible API 润色
- `Ctrl+F9`：基于 surrounding text 的语音编辑
- 普通字母键：由系统 `librime` 处理并显示预编辑与候选词

VoCoType 不启动或管理本地语言模型。Ollama、llama.cpp、vLLM、局域网服务和云端服务都只作为 OpenAI-compatible API endpoint 使用。

## Rime 架构

IBus 版本使用：

```text
ibus/engine.py
→ ibus/rime_runtime.py
→ Python 标准库 ctypes
→ 系统 librime
```

不需要 `pyrime`，也不会在用户机器上编译任何 Rime Python binding。适配层同时支持 Ubuntu 22.04 的传统直接 C API，以及当前 Fedora/Arch 的 `rime_get_api` 函数表。

VoCoType 使用独立目录：

```text
~/.config/vocotype/rime/
├── default.custom.yaml
├── user.yaml
└── build/
```

安装器只部署用户选择的 schema，不修改 `~/.config/ibus/rime`。

## 系统依赖

原生 DEB/RPM/Arch 包会自动安装正确依赖。源码安装时可使用：

```bash
# Ubuntu / Debian
sudo apt install ibus librime1 librime-bin librime-data rime-data-luna-pinyin

# Fedora
sudo dnf install ibus librime librime-tools brise

# Arch
sudo pacman -S --needed ibus librime librime-data
```

这些都是运行时依赖；不需要 `librime-dev` 或 `librime-devel`。

## 安装

推荐打开图形设置中心：

```bash
bash installers/launch-settings.sh
```

在“概览与安装”中选择 IBus，并保持“集成 Rime 拼音”启用。安装器会：

1. 创建隔离的 Python 3.12 语音运行环境；
2. 从安装包内 wheelhouse 离线安装二进制依赖；
3. 下载并校验 ASR/VAD/标点模型；
4. 部署选择的 Rime schema；
5. 创建真实 librime session，发送普通按键并验证 preedit；
6. 注册 IBus component。

兼容的交互式源码安装入口仍为：

```bash
./ibus/scripts/install.sh
```

## 使用

在桌面输入源中添加 **VoCoType Voice Input**，切换到该输入法后：

- 直接键入拼音：Rime 候选输入；
- 按住 `F9`：语音识别；
- 按住 `Shift+F9`：语音识别与可选 API 润色；
- 按住 `Ctrl+F9`：语音编辑；
- 按住 `Ctrl+Shift+F9`：surrounding-text 调试探针。

## 更换 Rime schema

在设置中心修改 schema ID 后重新运行“安装 / 修复”。安装器会重写最小 `schema_list` 并重新部署。例如：

```text
luna_pinyin
rime_ice
```

自定义 schema 的 YAML 和词库必须先存在于系统共享目录或 VoCoType 用户目录中。

## 安装后诊断

```bash
python tools/diagnostics/validate-ibus-install.py
python tools/diagnostics/debug-rime.py
```

诊断会直接调用系统 librime，并验证普通按键、preedit 和候选词，而不是只检查文件或 import。
