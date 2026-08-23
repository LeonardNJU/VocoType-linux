# Fcitx 5 集成

VoCoType 在 Fcitx 5 中是一个**全局 Module**，不是需要切换到的独立输入法。你可以继续使用原有的 Rime、拼音、Mozc 或键盘布局，通过已配置的语音快捷键调用 VoCoType；普通识别默认是 `F9`。

## 安装后如何使用

1. 在 **VoCoType 设置** 中安装 / 修复 Fcitx 5 集成；
2. 重启 Fcitx 5，或注销后重新登录；
3. 保持当前常用输入法不变；
4. 在任意输入框中按住普通识别快捷键录音，松开后识别并提交。

> **注意：不要把 VoCoType 添加到输入法列表。** 当前架构使用 `Category=Module` 全局插件。旧教程中“添加 VoCoType 输入法”的做法已经过时。

## 默认快捷键

| 动作 | 默认快捷键 |
|---|---|
| 普通识别 | `F9` |
| AI 润色 | `Shift+F9` |
| 语音编辑 | `Ctrl+F9` |

三个动作都可以在 **VoCoType 设置 → 通用设置 → 语音快捷键** 中独立录制。Fcitx Module 使用保存后的按键，不要求主键仍然是 F9。

当前输入法仍有未提交的 preedit 或候选列表时，VoCoType 默认不会开始录音，以免破坏正在进行的拼音组合。

## 与现有输入法共存

Fcitx 5 Module 不代理普通按键，也不创建自己的 Rime session。因此：

- 原有候选框、词库和快捷键保持不变；
- `fcitx5-rime`、拼音和 Mozc 可以照常使用；
- VoCoType 只在触发语音快捷键时介入。

## 诊断

优先在 **VoCoType 设置 → 诊断** 中运行 Doctor。针对插件路径、用户服务和 IPC 问题，可继续阅读：

- [Fcitx 5 路径与诊断](../troubleshooting/fcitx5-paths.md)
- [休眠或唤醒后的恢复](../troubleshooting/hibernate.md)
- [常见问题](../troubleshooting/faq.md)

更完整的实现参数与开发说明保留在仓库的 [Fcitx 5 技术文档](https://github.com/LeonardNJU/VocoType-linux/blob/master/docs/integrations/fcitx5.md)。

## 语音编辑兼容性

Fcitx 5 与 IBus 共用语音编辑的 SLM 计划和本地安全执行器；默认编辑快捷键为 `Ctrl+F9`。Module 只使用 Fcitx 正式的 surrounding-text capability、文本快照和删除接口；AI 未启用或应用不提供上下文时会安全拒绝。

标准 GTK/Qt 控件通常可以使用。Chrome、Chromium、Electron 和 VSCode 的 X11 输入法桥目前不提供 surrounding text，因此这些应用中的语音编辑不受支持；普通语音输入不受影响。

完整原因、能力矩阵和 Wayland 边界见 [语音编辑兼容性与局限](../guides/voice-editing.md)。

## 实现与源码位置

```text
Fcitx 5 event pipeline
  └─ vocotype.so
       ├─ vocotype-audio-recorder
       └─ vocotype-core
```

Module 源码位于 `src/integrations/fcitx5/`，只拦截已配置的语音快捷键。文本提交仅使用 Fcitx 官方 `InputContext::commitString()` 与 surrounding-text 接口，不使用剪贴板注入。

本地 backend socket：

```text
$XDG_RUNTIME_DIR/vocotype-fcitx5.sock
```

若桌面会话没有提供 `XDG_RUNTIME_DIR`，则回退到带 UID 的
`/tmp/vocotype-fcitx5-<uid>.sock`，避免不同登录用户互相抢占 socket。

主要用户配置：

```text
~/.config/vocotype/config.json
~/.config/vocotype/audio.conf
~/.config/vocotype/terms.yaml
~/.config/fcitx5/conf/vocotype.conf
```

源码构建入口是 `scripts/install/fcitx5/install.sh`；发行包用户不需要编译器。
