# 原生 C++ Core（实验性）

VoCoType 的 Release 包现在可以同时携带 Python backend 和实验性的
`vocotype-core`。默认仍使用 Python；只有显式设置 `VOCOTYPE_BACKEND=cpp`
时才切换，因此可以随时回退。

## 已原生化的链路

- F9 最终 Contextual Paraformer 识别
- Contextual hotword、VAD、标点与音频重采样
- 两遍式实时预览
- Shift+F9 异步识别与 AI 润色
- 语音编辑的替换、导航、撤销、翻译、LaTeX 和评论计划
- OpenAI-compatible HTTP 调用
- Unix socket、任务轮询、取消和临时录音清理

ASR 模型由独立 worker 持有；空闲退出后，ONNX 内存会由操作系统完整回收。

## 在 Fcitx 5 中试用

先停止正在运行的 backend，然后建立用户级服务覆盖：

```bash
systemctl --user edit vocotype-fcitx5-backend.service
```

写入：

```ini
[Service]
Environment=VOCOTYPE_BACKEND=cpp
```

然后执行：

```bash
systemctl --user daemon-reload
systemctl --user restart vocotype-fcitx5-backend.service
systemctl --user status vocotype-fcitx5-backend.service
```

恢复 Python backend 时，删除该 `Environment` 行，再重新加载并重启服务。

## 当前限制

原生 core 尚未迁移 Python backend 中的完整文本 normalization 与术语规范化；
SLM 已支持最终润色和语音编辑，但尚未向前端发送逐 token SSE 增量；IBus
引擎仍是 Python。因此当前版本用于 A/B 验证，不作为默认 backend。
