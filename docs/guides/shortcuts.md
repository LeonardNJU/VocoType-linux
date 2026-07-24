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

设置中心将规范化后的值写入 `~/.config/vocotype/config.json`：

```json
{
  "hotkeys": {
    "transcribe": "F9",
    "polish": "Shift+F9",
    "edit": "Ctrl+F9"
  }
}
```

Fcitx 5 的 `PTTKey`、`PolishKey` 与 `EditKey` 也会同步更新。不要手工写入普通字母等无效组合；输入法进程检测到无效或重复配置时会恢复默认值。
