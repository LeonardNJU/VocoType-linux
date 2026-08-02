# FAQ

## 语音快捷键没有反应

普通识别默认使用 `F9`，但用户可以重新录制。先在 **VoCoType 设置 → 通用设置 → 语音快捷键** 确认当前组合，再运行：

```bash
scripts/diagnostics/native-doctor.sh
```

Fcitx 5 用户应确认 `/tmp/vocotype-fcitx5.sock` 由当前用户的 `vocotype-core` 持有，并确认没有旧版 VoCoType Python 进程残留。

## Fcitx 没有候选窗口或状态提示

检查 Fcitx 进程环境：

```bash
pid=$(pgrep -x fcitx5 | head -1)
tr '\0' '\n' < /proc/$pid/environ | grep -E 'DISPLAY|WAYLAND_DISPLAY'
```

两项都不存在时，应通过桌面会话重新启动 Fcitx，而不是从 SSH 或无图形服务中启动。

## 语音编辑删除了文字但没有插入替换内容

当前原生版本会先验证 surrounding snapshot，再在同一次输入法事务中调用删除接口和正常文本提交接口，不再把延迟更新的 surrounding-text 缓存当作删除确认。若仍出现旧故障，请重新安装最新 Fcitx Module，并在设置中心运行 Doctor。

## 如何选择麦克风或扬声器？

在 **VoCoType 设置 → 通用设置** 选择输入设备，在 **Playground** 选择回放设备。录音、波形显示、回放和重采样都使用原生 PortAudio 实现。

## 如何校验模型？

使用 **VoCoType 设置 → 概览与安装 → 校验并下载模型**。原生模型管理器固定每个文件的 ModelScope revision，并在接受前检查 SHA-256。

## 如何收集诊断信息？

打开 **VoCoType 设置 → 诊断**。该页可验证 ELF 完整性、创建脱敏支持包、打开支持目录并创建 GitHub Issue。


## macOS首次打开被阻止

V5 正式版仍采用 ad-hoc签名且未经过 Apple公证。先尝试打开一次，然后进入 **系统设置 → 隐私与安全性 → 仍要打开**。ad-hoc没有一张可导入并信任的证书；该操作为具体 App建立本机例外。

## macOS录音过短提示不消失

V5 正式版中警告约 2秒后自动关闭。任何时候都可以点击整个状态浮层或按 `Esc`取消并清理；即使录音已经结束、没有活动录音器，点击也会关闭残留提示。

## macOS第一次F9松开后没有上屏

确认 App与输入法组件版本均为 `5.0.0`或更高。该版本除保留文本客户端与热键生命周期修复外，还会在按下快捷键时准备最终离线模型与当前热词图。旧版本需要用新 App覆盖 `/Applications`后启动一次，让它自动更新 `~/Library/Input Methods`中的组件。
