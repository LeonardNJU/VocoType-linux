# VoCoType 图形设置中心

VoCoType-linux提供两套原生设置前端：Linux上的 GTK 3/C++ `vocotype-settings`，以及 macOS上的 AppKit `VoCoType-linux.app`。它们直接读写统一配置、调用 native Core、管理平台输入法集成，并提供录音、模型、AI、用户词典、诊断和教程界面。

## 启动

Linux：

```bash
vocotype-settings
```

macOS：从 `/Applications`打开 **VoCoType-linux**。首次启动还会安装或升级内置 InputMethodKit组件。

源码树可先运行对应安装器：

```bash
bash scripts/install/fcitx5/install.sh --install-system-deps --download-models
# 或
bash scripts/install/ibus/install.sh --install-system-deps --download-models
```

## 概览

概览显示配置、术语、Unix socket、最终 ASR、实时预览、SLM 与语音编辑状态，并提供：

- 安装或修复 Fcitx 5；
- 安装或修复 IBus；
- 卸载任一用户集成，同时保留配置和模型；
- 重启 native core；
- 部署 librime 数据；
- 校验并下载固定 revision 的 ASR 模型。

## 识别与输入法

可配置输入设备、采样率、最短录音、实时 preedit、ITN，并管理：

- Fcitx 状态面板的 `minimal` / `animated` 样式；
- 存在未提交预编辑时是否阻止录音；
- 提交时是否移除尾部句号；
- 三套可录制的语音快捷键及冲突校验；
- 当前框架专属的高级选项：Fcitx 组合保护或 IBus Rime schema。

保存后配置写入 `~/.config/vocotype/` 和 `~/.config/fcitx5/conf/vocotype.conf`。

## ITN与术语

ITN页直接调用 C++ Core预览数字、日期、时间、距离和金额格式。用户词典页提供图形化新增热词、aliases与保护词，支持 YAML导入、外部批量修改后的热更新，以及 Finder / 文件管理器定位。所有写入先验证再原子替换；术语同时参与热词、alias canonicalization与后续格式保护。

## AI 润色与语音编辑

AI 页配置 OpenAI-compatible endpoint、model、API key、超时、最短字符数、SSE、thinking 与语音编辑开关。默认编辑快捷键是 `Ctrl+F9`，但可在通用设置中重新录制。测试请求由 C++ libcurl/SSE 客户端执行，不启动或管理模型进程。

## Playground

Playground 使用 native PortAudio/WAV 实现：

- 选择输出设备；
- 录制固定 3 秒音频并实时绘制波形；
- 将录音回放到指定扬声器或耳机；
- 调用当前 native core 进行 ASR；
- 录制一条语音编辑指令并展示受限编辑计划/结果。

默认编辑示例覆盖全文翻译场景，可直接验证 gedit 等 GTK 应用中的 surrounding-text 替换。

## 反馈

反馈页支持：

- 发送至官方 HTTPS 反馈服务；
- 可选附带结构化 Doctor 结果；
- 可选附带最大 5 MiB 的脱敏支持包；
- 打开预填的公开 GitHub Issue。

首次使用时生成随机 installation ID，只用于服务端限流和重复报告合并。

## Doctor 与支持

Doctor 检查：

- core、worker、录音器、模型管理器与设置中心 ELF；
- 配置、术语、输入输出设备与 socket；
- native payload SHA-256；
- 是否仍存在旧 VoCoType Python 进程。

该页还可查询最新 GitHub Release、生成脱敏支持包、打开支持目录和创建 GitHub Issue。
