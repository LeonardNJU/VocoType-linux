# 原生 C++ Core

Release 包默认使用 `vocotype-core` 承载语音运行链路。Fcitx 5 直接连接
默认 socket；IBus 保留输入法/GObject/Rime 壳，但通过独立的每用户 socket
复用同一个 native core。

## 已原生化的链路

- F9 Contextual Paraformer 最终识别
- Contextual hotword、VAD、标点与音频重采样
- 中文 ITN、日期/时间/距离/货币样式
- `terms.yaml` 术语规范化、保护区间、热重载和 native hotword
- 两遍式实时预览
- Shift+F9 异步识别与 OpenAI-compatible SSE 增量润色
- reasoning/thinking 屏蔽、heartbeat 与流空闲超时
- 语音编辑的替换、导航、撤销、翻译、LaTeX 和评论计划
- 任务轮询、取消、TTL 回收和临时录音清理

ASR 模型由独立 worker 持有；空闲退出后，ONNX 内存由操作系统完整回收。

## 后端选择

默认值是 `auto`：找到 native core 就使用 C++，只有源码环境中没有原生
二进制时才回退 Python。

强制使用原生后端：

```ini
[Service]
Environment=VOCOTYPE_BACKEND=cpp
```

强制回退旧 Python inference：

```ini
[Service]
Environment=VOCOTYPE_BACKEND=python
```

Fcitx 用户可以通过以下命令建立用户级覆盖：

```bash
systemctl --user edit vocotype-fcitx5-backend.service
systemctl --user daemon-reload
systemctl --user restart vocotype-fcitx5-backend.service
```

IBus 由输入法进程按需启动独立 core；重启 VoCoType IBus 引擎即可应用同一
环境变量。由 IBus 启动的 core 设置了 parent-death signal，并在引擎退出时
主动回收。

## 边界

IBus 的 IBus/GObject 事件循环、录音 UI 和可选 Rime 适配仍由 Python 实现；
设置中心也是 GTK Python 应用。这些壳层不再加载 FunASR、ONNX Runtime 或
执行 ITN/SLM 推理。计算密集且需要跨输入法一致的语音链路均在 C++ core 中。
