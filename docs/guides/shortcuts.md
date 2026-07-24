# 语音快捷键

VoCoType 默认使用：

| 动作 | 默认快捷键 |
|---|---|
| 普通识别 | `F9` |
| AI 润色 | `Shift+F9` |
| 语音编辑 | `Ctrl+F9` |

三个动作都可以独立修改，并同时作用于 Fcitx 5 与 IBus。

## 录制快捷键

打开 **VoCoType 设置 → 通用设置 → 语音快捷键**：

1. 点击需要修改的动作；
2. 直接按下新快捷键，不需要输入按键名称；
3. 使用单独的右 Alt、右 Ctrl 或右 Super 时，按下后松开；
4. 看到绿色校验结果后保存设置。

按 `Esc` 可取消本次录制。

## 不允许的快捷键

VoCoType 会拒绝明显不适合作为按住说话键的组合，包括：

- 裸字母、数字、标点；
- 只加 Shift 的可打印字符；
- 空格、回车、Tab、退格、删除、方向键、Home/End、PageUp/PageDown；
- 单独的左 Alt、左 Ctrl、左 Super 或 Shift；
- `Ctrl+C`、`Ctrl+V`、`Ctrl+S`、`Alt+Tab`、`Alt+F4`、`Super+L` 等常用系统或应用快捷键；
- 与另外两个 VoCoType 动作重复的组合。

功能键、右侧修饰键以及带 Ctrl、Alt 或 Super 的非保留组合通常可用，例如 `F8`、`Alt_R`、`Ctrl+Shift+F8`。

## 冲突检测

保存前会尽力检查：

- Fcitx 5 全局配置与当前仍已安装的 addon 配置（已卸载 addon 遗留的配置文件不会阻止录制）；
- KDE `kglobalshortcutsrc`；
- GNOME/Mutter 与媒体键 GSettings；
- GNOME 自定义快捷键；
- X11 会话中对 root window 的实际 `XGrabKey` 占用情况。

检测到占用时不会接受该按键。Wayland 不提供一个可以枚举其他应用全部全局快捷键的通用接口，因此配置扫描属于尽力检测；桌面环境中新注册但尚未写入配置的冲突仍可能需要用户更换组合。

## 配置格式

快捷键不再存放在共享 `config.json` 中。配置职责如下：

- `~/.config/vocotype/config.json`：音频、ASR、AI、ITN 与界面等共享设置，不含快捷键；
- `~/.config/vocotype/ibus.json`：仅保存 IBus 的三个语音快捷键；
- `~/.config/fcitx5/conf/vocotype.conf`：保存 Fcitx 5 Module 的快捷键与 Fcitx 专属选项。

设置中心保存快捷键时会分别更新 IBus 配置和 Fcitx 配置。Fcitx 通过 `Controller1.SetConfig` 更新，随后读回持久配置及运行实例；运行实例只是 `vocotype.conf` 的已加载状态，不是第三份配置源。

旧版 `fcitx5-backend.json` 会在首次启动新版设置中心或 IBus 引擎时迁移：共享字段进入 `config.json`，快捷键进入 `ibus.json`，原文件归档为 `fcitx5-backend.json.migrated`。已有 `vocotype.conf` 始终优先，不会再被旧 JSON 覆盖。
