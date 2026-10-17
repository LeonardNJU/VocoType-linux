# Fcitx 5 休眠或唤醒后的恢复

V4 的 Fcitx 5 安装会部署原生 backend 用户服务，当前 unit 使用 `Restart=always`。Fcitx Module 在发现 `${XDG_RUNTIME_DIR}/vocotype-fcitx5.sock` 不存在时也会请求启动服务，因此旧版“把 `Restart=on-failure` 手工改成 `always`”教程已经不再适用。

## 推荐恢复方式

先打开 **VoCoType 设置 → 概览与安装**，对 Fcitx 5 执行一次“安装 / 修复”，再到 **诊断** 页面运行 Doctor。修复器会重新部署当前 backend unit，并根据桌面会话重新加载 Fcitx、验证 addon。

KDE Plasma + Wayland 下，若当前已选择并启用 Fcitx 5，程序会通过 KDE 配置通知自动完成取消选择、等待生效、恢复原选项，并等待新的 Fcitx 实例。恢复时保留路径类型；操作失败或被正常中断时也会尝试恢复原选项。此流程由 KWin 管理，不直接重启 Fcitx 进程或用户服务。

如果缺少 KDE 工具、当前选择的是其他输入法或输入法未启用，安装器会保留当前设置并提示手动操作。请打开 **系统设置 → 虚拟键盘**，取消当前输入法并应用，再重新选择 **Fcitx 5** 并应用。其他 Wayland 桌面和无法确认的会话也需手动重新应用，必要时注销后重新登录。已确认的 X11 桌面仍支持自动重启。

## 快速恢复

backend 没有恢复时：

```bash
systemctl --user restart vocotype-fcitx5-backend.service
```

这条命令只重启 VoCoType backend，不会重启 Fcitx 框架。若需重新加载 Fcitx，请按上面的桌面会话流程操作，然后重新尝试语音快捷键。

## 检查服务与日志

```bash
systemctl --user status vocotype-fcitx5-backend.service
journalctl --user -u vocotype-fcitx5-backend.service -n 100
```

当前 unit 应包含：

```ini
Restart=always
RestartSec=3
```

如果仍看到 `Restart=on-failure`，说明用户目录里残留了旧版 unit。不要继续手工维护旧文件，直接在设置中心执行 Fcitx 5“安装 / 修复”。

## 检查 socket 与进程

```bash
ls -l "${XDG_RUNTIME_DIR}/vocotype-fcitx5.sock"
pgrep -af 'vocotype-core|vocotype-(offline|streaming)-worker'
```

`vocotype-core` 运行时 socket 应由当前用户持有。ASR worker 会按需启动并在空闲后退出，因此没有常驻 worker 不一定是故障。

## 仍然无法恢复

在 **VoCoType 设置 → 诊断** 中生成脱敏支持包。报告问题时附上 Doctor 结果、用户服务日志和桌面环境信息；不要附带 API key、原始录音或未脱敏配置。
