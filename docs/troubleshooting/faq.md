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
