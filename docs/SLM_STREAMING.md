# 流式 AI 润色

VoCoType 保留两种 SLM provider：

- `remote`：OpenAI-compatible HTTP API；默认使用 SSE 流式输出。
- `local_ephemeral`：按需加载本地模型，录音时预热，空闲后释放。

IBus 的 `Ctrl+F9` 语音编辑、远程 provider、本地 provider 和失败回退均保留。
Fcitx 5 在此基础上增加异步任务与输入面板实时预览。

## Fcitx 5 交互

默认配置下：

- `F9`：只做 ASR。
- `Shift+F9`：ASR 后尝试 AI 润色。
- `PolishByDefault=true` 时语义反转：`F9` 默认润色，`Shift+F9` 临时跳过。

松开润色模式热键后，module 不会阻塞等待完整响应：

```text
transcribe_start
    ↓ task_id
polish_poll（每 100 ms）
    ↓
status / heartbeat / delta / final / error
```

输入面板会显示当前状态、模型生成的可见预览和 ASR 原文。模型的 `<think>`、
`Thinking Process:` 等推理内容不会进入预览或最终提交。

在任务进行中按 `Escape` 会取消；开始输入其他按键也会取消任务并把该按键继续交给当前输入法。
焦点离开原始输入框时任务会取消，结果不会提交到新窗口。

## 远程配置

```json
{
  "slm": {
    "enabled": true,
    "provider": "remote",
    "model": "openai/gpt-4o-mini",
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
    "extra_body": {}
  }
}
```

参数：

- `remote_stream`：远程润色使用 SSE；默认 `true`。
- `stream_idle_timeout_ms`：最后一次 SSE 事件之后允许空闲的时长，不是整个生成过程的总时长。
- `transport_timeout_ms`：底层连接/读取超时；`0` 时使用流式空闲超时。
- `remote_max_tokens`：远程输出上限；默认 `0`，不发送固定 `max_tokens`，避免长文本被 128 token 截断。
- `extra_headers`：额外 HTTP header。
- `extra_body`：合并到请求 JSON 的 provider 专属字段。

`max_tokens` 继续用于 `local_ephemeral`；它不再隐式限制远程输出。

## OpenRouter

检测到 OpenRouter endpoint 时，VoCoType 会默认添加项目标识 header，并把 thinking 配置映射为
OpenRouter reasoning 字段：

- thinking 开启：`reasoning.enabled=true`。
- thinking 关闭：请求排除 reasoning 输出。

用户在 `extra_headers` 或 `extra_body` 中显式提供的值优先。

## 本地 provider

本地 worker 的生成协议暂时仍返回完整字符串，但它通过相同任务事件接口工作：Fcitx 先显示
“正在调用大模型”，完成后收到 `final`。原有预热、`ready_wait_ms`、`keepalive_ms`、
thinking-only 重试和释放策略不变。

## 超时与回退

流式任务的空闲计时会在 `status`、SSE heartbeat 或 `delta` 到达时刷新。发生以下情况时，
任务进入 `error`：

- 长时间没有模型事件；
- 远端返回结构化错误；
- SSE/JSON 无法解析；
- 最终内容为空或只有 thinking。

只要 ASR 原文存在，Fcitx 输入面板就保留原文候选。按 `1`、空格或回车提交原文，按
`Escape` 放弃。远端失败不会静默丢失已经识别的文字。

## IBus

IBus 保持最终结果式 UI，不显示逐 token 预览；现有 `F9`、`Shift+F9` 和 `Ctrl+F9`
语音编辑行为不变。共享 `SLMPolisher` 仍保留远程非流式兼容路径，便于旧服务和编辑链路使用。
