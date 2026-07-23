# IBus 集成

VoCoType 在 IBus 中作为独立输入法引擎运行，适合 GNOME 和默认使用 IBus 的发行版。除普通语音输入外，IBus 版本还支持读取输入框上下文并执行语音编辑。

## 安装后如何使用

1. 在 **VoCoType 设置** 中安装 / 修复 IBus 集成；
2. 在系统输入源中添加或选择 VoCoType；
3. 在任意输入框中按住普通识别快捷键录音，默认是 `F9`；
4. 需要修改已有文字时，按住语音编辑快捷键说出编辑命令，默认是 `Ctrl+F9`。

## 默认快捷键

| 动作 | 默认快捷键 |
|---|---|
| 普通识别 | `F9` |
| AI 润色 | `Shift+F9` |
| 语音编辑 | `Ctrl+F9` |
| surrounding-text 诊断探针 | `Ctrl+Shift+F9` |

前三个动作可以在 **VoCoType 设置 → 通用设置 → 语音快捷键** 中独立录制；诊断探针保持固定组合。

## 语音编辑示例

- “把 A 改成 B”
- “删除上一句”
- “在结尾插入……”
- “移动到开头”
- “撤销修改”

编辑要求先启用并测活 AI，同时依赖当前应用对 IBus surrounding text 的支持。所有示例都由模型结合上下文理解，并非本地硬编码命令。部分应用、沙箱环境或自绘输入框可能只能提供有限上下文；VoCoType 会在能力不足时拒绝危险修改，而不是盲目覆盖文本。

完整限制、选区语义和失败行为见 [语音编辑兼容性与局限](../guides/voice-editing.md)。

## Rime

需要拼音输入时，VoCoType IBus 的原生 C++ engine 通过 `rime_get_api()` 直接调用系统 `librime`。不存在 Python binding 或 `ctypes` 适配层。普通键盘输入与语音快捷键共存，配置使用独立的 `~/.config/vocotype/rime`，不会修改其他 IBus 输入法。详见 [Rime 配置](../guides/rime.md)。

## 诊断

优先运行设置中心 Doctor，再查看 [常见问题](../troubleshooting/faq.md)。更完整的 surrounding-text 行为、编辑命令和开发参数见仓库的 [IBus 技术文档](https://github.com/LeonardNJU/VocoType-linux/blob/master/docs/integrations/ibus.md)。

## 实现与源码位置

```text
IBus daemon
  └─ vocotype-ibus-engine
       ├─ librime
       ├─ vocotype-audio-recorder
       └─ vocotype-core
```

IBus metadata 位于 `src/integrations/ibus/`；原生 engine 入口与共享桌面运行时位于 `src/desktop/`。Rime 用户数据保存在 `~/.config/vocotype/rime`，不会修改其他 IBus 输入法的数据目录。

需要重新部署 Rime 时运行：

```bash
vocotype-ibus-engine --deploy-rime
```

源码构建入口是 `scripts/install/ibus/install.sh`；发行包用户不需要编译器。
