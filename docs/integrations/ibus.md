# IBus 集成

VoCoType 在 IBus 中作为独立输入法引擎运行，适合 GNOME 和默认使用 IBus 的发行版。除普通语音输入外，IBus 版本还支持读取输入框上下文并执行语音编辑。

## 安装后如何使用

1. 在 **VoCoType 设置** 中安装 / 修复 IBus 集成；
2. 在系统输入源中添加或选择 VoCoType；
3. 在任意输入框中按住 `F9` 录音；
4. 需要修改已有文字时，按住 `Ctrl+F9` 说出编辑命令。

## 快捷键

| 快捷键 | 功能 |
|---|---|
| `F9` | 本地 ASR，完成后提交文字 |
| `Shift+F9` | ASR 后执行可选 AI 润色 |
| `Ctrl+F9` | 读取 surrounding text 并执行语音编辑 |
| `Ctrl+Shift+F9` | surrounding-text 兼容性诊断探针 |

## 语音编辑示例

- “把 A 改成 B”
- “删除上一句”
- “在结尾插入……”
- “移动到开头”
- “撤销修改”

编辑要求先启用并测活 AI，同时依赖当前应用对 IBus surrounding text 的支持。所有示例都由模型结合上下文理解，并非本地硬编码命令。部分应用、沙箱环境或自绘输入框可能只能提供有限上下文；VoCoType 会在能力不足时拒绝危险修改，而不是盲目覆盖文本。

完整限制、选区语义和失败行为见 [语音编辑兼容性与局限](../guides/voice-editing.md)。

## Rime

需要拼音输入时，优先让系统的 `ibus-rime` 负责普通键盘输入，让 VoCoType 负责语音。历史上的内嵌 Rime 兼容路径只用于旧安装迁移。详见 [Rime 配置](../guides/rime.md)。

## 诊断

优先运行设置中心 Doctor，再查看 [常见问题](../troubleshooting/faq.md)。更完整的 surrounding-text 行为、编辑命令和开发参数见仓库的 [IBus 技术文档](https://github.com/LeonardNJU/VocoType-linux/blob/master/ibus/README.md)。
