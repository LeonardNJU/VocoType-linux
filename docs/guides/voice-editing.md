# 语音编辑：能力、兼容性与局限

`Ctrl+F9` 语音编辑会先读取当前输入框提供的上下文，再识别编辑指令。所有自然语言理解都交给已配置并测活的 SLM：模型结合全文、光标、选区和 ASR 可能产生的同音词错误，返回受限 JSON 编辑计划；IBus 与 Fcitx 5 的本地适配器只负责校验并执行 `replace`、`key_actions` 或 `no_op`。两者的主要差异来自应用向输入法框架暴露上下文的方式。

## 什么是 surrounding text

输入法收到的不是任意应用全文，而是一份由当前输入控件主动提供的快照：

- `text`：控件愿意提供的光标周边文本；
- `cursor`：光标在这段文本中的位置；
- `anchor`：选区另一端的位置；
- `selected text`：由 `cursor` 与 `anchor` 之间的文本计算得到。

因此，**选中文本不是绕过 surrounding text 的独立通道**。应用没有提供有效 surrounding text 时，VoCoType 也无法通过输入法接口可靠获取选区。

应用可以只提供当前输入框、当前段落或光标附近的一部分文本，并不保证提供整个文档。密码框和敏感输入通常会故意禁用该能力。

## SLM 编辑计划

语音编辑不再维护中文命令枚举，也不会先做字面字符串替换。模型必须返回以下三种计划之一：

- `replace`：返回编辑后的完整 surrounding text；
- `key_actions`：返回白名单内的导航、选区、撤销、复制、剪切或粘贴按键；
- `no_op`：无法安全判断或无需修改。

这使模型能够根据上下文把 ASR 的同音目标映射到正文中真正存在的词。例如指令里识别成近音词时，模型仍可从当前句定位用户想替换的术语。模型输出不会被直接执行：本地校验器会拒绝未知模式、非法按键、非法修饰键、过多动作和非字符串替换文本，并把可选的 `null` 字段规范化为空字符串。

`Ctrl+F9` 因而要求 AI 功能处于已测活状态。配置的 OpenAI-compatible API 会收到 ASR 指令和当前控件提供的 surrounding text、光标与选区；端点可以位于本机或远端。若不希望这些内容离开设备，应自行在本机运行兼容服务，或不要启用语音编辑。

## VoCoType 的安全边界

VoCoType 只使用输入法框架的正式接口：

- 录音前保存文本、光标、选区和输入上下文标识；
- 编辑结果返回后再次确认输入框和内容没有变化；
- 使用框架的 surrounding-text 删除接口和正常文本提交接口完成修改；
- 上下文无效、焦点变化或正文已变化时取消本次编辑。

VoCoType **不会**通过以下方式猜测或抓取上下文：

- 模拟 `Ctrl+A`、`Ctrl+C`、`Ctrl+V`；
- 读取或改写系统剪贴板；
- OCR、屏幕截图或无障碍树抓取；
- 在不掌握当前文本时盲目全选覆盖。

因此，不支持 surrounding text 的应用会显示“不支持获取输入内容”，而不是进入不安全的兼容模式。

## IBus 下的局限

IBus 引擎可以声明自己需要 surrounding text，并从应用收到文本、光标与选区位置。但最终是否提供、提供多少内容，完全由当前应用或输入控件决定。

常见限制：

1. **上下文范围有限**：应用可能只返回当前段落或当前输入框，针对文档其他位置的命令无法执行。
2. **自绘控件兼容性不稳定**：自定义编辑器、游戏、远程桌面、沙箱应用或特殊浏览器控件可能不实现 IBus surrounding text。
3. **选区依赖 anchor**：应用未提供不同于 cursor 的 anchor 时，VoCoType 无法知道当前选区。
4. **删除能力可能不完整**：应用即使提供文本，也可能拒绝或部分执行 delete-surrounding 请求。
5. **结果式界面**：IBus 当前以阶段状态和最终结果为主，不提供 Fcitx 5 普通润色使用的逐 token 流式预览。

可使用 `Ctrl+Shift+F9` surrounding-text 探针查看当前应用实际提供的能力、文本范围、光标和选区。

IBus 的官方接口也明确使用 `text + cursor_pos + anchor_pos` 表示 surrounding text 与选区；当 anchor 与 cursor 相同时，可能表示没有选区，也可能表示客户端不支持返回选区。参见 [IBusEngine API](https://ibus.github.io/docs/ibus-1.5/IBusEngine.html)。

## Fcitx 5 下的局限

Fcitx 5 Module 只在当前 InputContext 声明 `SurroundingText` capability 且缓存有效时启动语音编辑。标准 GTK/Qt 文本控件通常可以在 X11 或 Wayland 下提供这类信息，但自定义控件仍可能不实现。

GTK 官方文档明确说明：控件可响应 `retrieve-surrounding` 并提供最多一个段落的上下文，但没有义务响应；输入法必须能够在没有上下文时工作。参见 [GTK 4 `get_surrounding`](https://docs.gtk.org/gtk4/method.IMContext.get_surrounding.html) 和 [`delete_surrounding`](https://docs.gtk.org/gtk4/method.IMContext.delete_surrounding.html)。

### Chrome、Chromium、Electron 和 VSCode（X11）

截至 2026 年 7 月，Chromium 的 GTK/X11 输入法桥没有实现 surrounding-text 操作：

- `SetSurroundingText(...)` 仍为空实现；
- 源码仍标注需要处理 GTK 的 `retrieve-surrounding` 与 `delete-surrounding` 信号。

参见 Chromium 的 [`InputMethodContextImplGtk`](https://chromium.googlesource.com/chromium/src/+/refs/tags/142.0.7444.134/ui/gtk/input_method_context_impl_gtk.cc)。

Chrome 使用 Chromium；Electron 也基于 Chromium，因此 VSCode 等 Electron 应用在 X11 下继承这一限制。它们可以正常接收输入法提交的文字，但不会通过该桥接层把编辑器正文、光标和选区可靠交给 Fcitx。

因此，当前不保证以下位置的 `Ctrl+F9` 语音编辑：

- Chrome/Chromium 网页输入框和地址栏；
- VSCode 编辑器；
- 其他使用 Chromium GTK/X11 backend 的 Electron 应用。

这是 Chromium X11 输入法桥的能力缺口，不是 Fcitx 5 或 X11 普遍缺少 surrounding text。Kate、标准 Qt 编辑器和标准 GTK 文本控件在 X11 下仍可提供该能力。

### Chromium 原生 Wayland

Chromium 的原生 Wayland/Ozone backend 已包含 `SetSurroundingText` 和处理删除 surrounding text 的实现。参见 Chromium 的 [`WaylandInputMethodContext`](https://chromium.googlesource.com/chromium/src/+/bef7010a6eee47bc65dbacc167141ea007a7e133/ui/ozone/platform/wayland/host/wayland_input_method_context.cc)。

当应用**实际运行在原生 Wayland**而非 XWayland 时，理论上存在可用路径。但它仍受以下因素影响：

- Chromium/Electron 版本；
- 是否真正使用 `--ozone-platform=wayland`；
- 桌面 compositor 的 text-input 协议实现；
- Fcitx Wayland frontend 配置；
- 应用控件实际提供的文本范围。

在完成跨发行版和桌面环境验收前，VoCoType 不把 Chromium/Electron Wayland 声明为稳定兼容目标。

## 能力矩阵

| 场景 | 读取上下文 | 读取选区 | 替换文本 | 说明 |
|---|---:|---:|---:|---|
| 标准 GTK 输入框 | 通常可以 | 取决于控件 | 通常可以 | 上下文范围由控件决定 |
| 标准 Qt 输入框 / 编辑器 | 通常可以 | 取决于控件 | 通常可以 | Kate 等原生 Qt 应用通常可用 |
| 自定义 GTK/Qt 编辑器 | 不确定 | 不确定 | 不确定 | 必须由应用正确实现输入法查询 |
| Chrome/Chromium X11 | 不支持 | 不支持 | 不支持 | Chromium GTK/X11 bridge 缺少实现 |
| Electron/VSCode X11 | 不支持 | 不支持 | 不支持 | 继承 Chromium 限制 |
| Chromium/Electron 原生 Wayland | 可能支持 | 可能支持 | 可能支持 | 尚未作为稳定兼容目标验收 |
| 密码框 / 敏感输入 | 禁止 | 禁止 | 禁止 | 主动保护敏感内容 |

## 失败时会发生什么

VoCoType 不会因为应用兼容性不足而修改未知文本：

- 无 surrounding-text capability：录音不会开始；
- surrounding snapshot 无效：提示当前输入框没有可用上下文；
- 录音期间内容或焦点变化：取消编辑；
- 应用未执行删除请求：不提交替换文本；
- ASR 未识别到指令：显示“未识别到编辑指令”；
- AI 未启用或未测活：不执行任何编辑；
- AI 返回非 JSON、非法模式、`null` 替换文本或越权按键：拒绝计划并保留原文；
- AI 编辑超过 30 秒：终止等待并提示超时。

普通 `F9` 语音输入不依赖 surrounding text，因此即使 `Ctrl+F9` 编辑不可用，应用通常仍可使用普通语音输入。
