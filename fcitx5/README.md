# VoCoType Fcitx 5 全局模块

VoCoType 以 Fcitx 5 全局 Module 工作。用户继续使用现有的 Rime、拼音、Mozc 或键盘布局；模块只拦截语音热键。

## 功能

- `F9`：原生 PortAudio 录音和本地 ASR
- `Shift+F9`：SSE 流式润色
- `Ctrl+F9`：surrounding-text 语音编辑
- 实时 preedit 预览
- 焦点变化、取消和任务生命周期保护

```text
Fcitx 5 event pipeline
  └─ vocotype.so
       ├─ vocotype-audio-recorder
       └─ vocotype-core
```

模块、录音器、core 和 worker 均为编译后的本地程序。提交文本只使用 Fcitx 官方 `InputContext::commitString()` / surrounding-text API，不使用剪贴板注入。

## 安装

发行包安装后，可在 **VoCoType 设置 → 概览** 点击“安装 / 修复 Fcitx 5”，或运行：

```bash
bash fcitx5/scripts/install.sh --download-models
```

无需把 VoCoType 添加到输入法列表。可在 `fcitx5-configtool` 的附加组件页面确认 **VoCoType Voice Input** 已启用。

源码构建需要 CMake、C++20、Fcitx 5 开发包、GTK、PortAudio、yaml-cpp、libcurl、OpenSSL 和 nlohmann-json。安装后的运行环境不需要编译器或 Python。

## Socket

```text
/tmp/vocotype-fcitx5.sock
```

健康检查：

```bash
echo '{"type":"ping"}' | nc -U /tmp/vocotype-fcitx5.sock
```

## 用户配置

- `~/.config/vocotype/fcitx5-backend.json`
- `~/.config/vocotype/audio.conf`
- `~/.config/vocotype/terms.yaml`
- `~/.config/fcitx5/conf/vocotype.conf`

打开 `vocotype-settings` 可配置设备、ITN、术语、SLM，并在 Playground 中做真实录音和识别测试。
