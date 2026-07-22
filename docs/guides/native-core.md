# 原生 C++ 运行时

VoCoType 的已安装运行时全部由编译后的本地程序组成，不需要 Python、虚拟环境或 Python 包路径。

## 组件

```text
Fcitx 5 Module ─┐
                ├─ vocotype-audio-recorder ── PCM/WAV/实时预览
IBus Engine ────┘
                         │
                         ▼
                  vocotype-core
                   ├─ offline worker
                   ├─ streaming worker
                   ├─ ITN / 术语规范化
                   ├─ OpenAI-compatible SSE
                   └─ 语音编辑计划

vocotype-settings       原生 GTK 设置中心
vocotype-model-manager  原生模型校验与下载器
```

## 已原生化的能力

- PortAudio 麦克风录制、设备选择、重采样和安全 WAV
- Contextual Paraformer 最终识别
- 两遍式实时预览
- VAD、标点、原生热词
- 中文 ITN、日期、时间、距离、货币和固定短语保护
- `terms.yaml` 解析、规范化、保护区间和热重载
- OpenAI-compatible HTTP/SSE 润色
- reasoning/thinking 屏蔽、heartbeat、超时和取消
- surrounding-text 语音编辑和受限按键动作
- 原生 IBus Engine、preedit、候选框和 librime
- 原生 GTK 设置、Playground、Doctor、安装修复与模型下载

ASR 模型由独立 worker 持有。worker 空闲退出后，其 ONNX 内存由操作系统回收。

## 运行时依赖边界

运行时仍依赖发行版提供的系统动态库，例如 GTK、PortAudio、IBus、librime、libcurl 和 Fcitx 5；这些由 DEB/RPM/Arch 包管理器处理。它不依赖 Python。

仓库中的历史 Python 实现可继续用于回归对照和开发测试，但不会进入 native package，也不会被任何已安装 launcher 调用。
