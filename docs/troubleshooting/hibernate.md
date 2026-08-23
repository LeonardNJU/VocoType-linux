# Fcitx 5 休眠或唤醒后的恢复

V4 的 Fcitx 5 安装会部署原生 backend 用户服务，当前 unit 使用 `Restart=always`。Fcitx Module 在发现 `${XDG_RUNTIME_DIR}/vocotype-fcitx5.sock` 不存在时也会请求启动服务，因此旧版“把 `Restart=on-failure` 手工改成 `always`”教程已经不再适用。

## 推荐恢复方式

先打开 **VoCoType 设置 → 概览与安装**，对 Fcitx 5 执行一次“安装 / 修复”，再到 **诊断** 页面运行 Doctor。修复器会重新部署当前 unit、启用 addon，并在桌面会话中安全重启 Fcitx。

## 快速恢复

backend 没有恢复时：

```bash
systemctl --user restart vocotype-fcitx5-backend.service
fcitx5 -r
```

然后重新尝试语音快捷键。

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
