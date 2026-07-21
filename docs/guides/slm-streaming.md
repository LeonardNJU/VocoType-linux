# OpenAI-compatible AI 润色与语音编辑

VoCoType 只支持一种 AI 接口：OpenAI-compatible `chat/completions` API。

端点可以运行在本机、局域网或云端。例如用户可以自行启动 Ollama、llama.cpp、vLLM，或使用任意兼容云服务；对 VoCoType 来说都只是 `endpoint + model + 可选 API key`。VoCoType 不安装模型框架，不启动、预热、保活或停止模型进程。

IBus 与 Fcitx 5 的 `Ctrl+F9` 都将 ASR 指令、surrounding text、光标和选区发送给该 API，由模型返回受限的 `replace`、`key_actions` 或 `no_op` JSON 计划。本地执行器只校验并执行计划，不解析自然语言命令。

## 配置

推荐在 `vocotype-settings` 的“AI 润色与语音编辑”页面填写并测活：

```json
{
  "slm": {
    "enabled": true,
    "model": "qwen3",
    "endpoint": "http://127.0.0.1:11434/v1/chat/completions",
    "api_key": "",
    "remote_stream": true,
    "stream_idle_timeout_ms": 20000,
    "transport_timeout_ms": 0,
    "remote_max_tokens": 0,
    "min_chars": 8,
    "enable_thinking": false,
    "edit_enabled": true,
    "edit_max_tokens": 1024,
    "retry_without_proxy": true,
    "extra_headers": {},
    "extra_body": {}
  }
}
```

无鉴权的本地服务可以留空 API Key。填写服务根地址或 `/v1` 时，VoCoType 会补全 `/v1/chat/completions`。

## 快捷键与流式行为

- `F9`：只做 ASR。
- `Shift+F9`：ASR 后调用 API 润色。
- `Ctrl+F9`：调用同一 API 生成结构化语音编辑计划。

Fcitx 5 使用异步 `start / poll / cancel` 协议显示 SSE 可见增量；IBus 提交最终结果。`<think>`、reasoning 和分析内容不会进入最终文本。模型失败时保留 ASR 原文，不会静默丢失已识别内容。

## 参数

- `remote_stream`：端点支持 SSE 时显示流式增量。
- `stream_idle_timeout_ms`：最后一次模型事件后的空闲超时。
- `transport_timeout_ms`：连接/读取超时；`0` 时沿用流式空闲超时。
- `remote_max_tokens`：普通润色输出上限；`0` 表示不主动发送固定限制。
- `edit_max_tokens`：结构化编辑计划的最低输出预算；长上下文会自动提高预算。
- `extra_headers` / `extra_body`：兼容服务的附加请求字段。

## 旧配置迁移

旧版 `provider: local_ephemeral` 配置不会再启动 worker。设置中心会关闭 AI 开关并提示用户先自行启动 OpenAI-compatible 服务，再填写 endpoint 和 model 后重新测活。
