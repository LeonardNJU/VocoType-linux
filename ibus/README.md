# VoCoType IBus

IBus 集成是一个编译后的原生 `IBusEngine`，不使用 Python/GObject 壳。

## 功能

- `F9`：本地离线语音输入
- `Shift+F9`：识别后调用 OpenAI-compatible API 流式润色
- `Ctrl+F9`：基于 surrounding text 的语音编辑
- 普通按键：直接链接系统 librime，显示 preedit 和候选词

```text
IBus daemon
  └─ vocotype-ibus-engine
       ├─ librime
       ├─ vocotype-audio-recorder
       └─ vocotype-core
```

原生引擎通过 `rime_get_api()` 函数表调用 librime，适配当前发行版 ABI。Rime 用户数据位于 `~/.config/vocotype/rime`，不会修改系统 IBus Rime 用户目录。

## 安装

发行包安装后，可在 **VoCoType 设置 → 概览** 点击“安装 / 修复 IBus”，或运行：

```bash
bash ibus/scripts/install.sh --download-models
```

源码构建需要 IBus、librime、GTK、PortAudio、yaml-cpp、libcurl、OpenSSL、nlohmann-json 和 C++20 工具链。安装后的运行环境不需要编译器或 Python。

安装完成后，在桌面输入源中添加 **VoCoType Voice Input**。

## Rime 部署

```bash
vocotype-ibus-engine --deploy-rime
```

该命令使用 librime 自身的部署 API准备 `~/.config/vocotype/rime/build`。

## 诊断

打开 `vocotype-settings` 的 Doctor 页面。Doctor 会检查 IBus ELF、librime、模型、音频设备、native core socket，并确认没有 VoCoType Python 进程。
