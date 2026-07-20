# VocoType Fcitx5 休眠后失效问题 - 解决方案

## 问题描述

**症状**：
- 首次安装后输入法工作正常
- 系统休眠/待机后，输入法无法使用
- 错误提示：`Failed to connect to backend: /tmp/vocotype-fcitx5.sock`

**根本原因**：
systemd 服务配置使用 `Restart=on-failure`，仅在进程异常退出时重启。休眠时系统正常终止进程（exit code = 0），不会触发自动重启。

---

## 一键修复（推荐）

在 VocoType 项目目录下运行：

```bash
cd /path/to/VocoType-linux
./tools/diagnostics/fix-hibernate-issue.sh
```

**脚本会自动**：
1. 备份当前服务配置
2. 更新重启策略 `Restart=on-failure` → `Restart=always`
3. 重新加载并重启服务
4. 验证修复结果

---

## 手动修复步骤

如果你想手动修复或了解细节：

### 1. 编辑服务配置文件

```bash
nano ~/.config/systemd/user/vocotype-fcitx5-backend.service
```

### 2. 修改 `[Service]` 部分

**修改前**：
```ini
[Service]
Type=simple
ExecStart=%h/.local/bin/vocotype-fcitx5-backend
Restart=on-failure        # ⚠️ 只在异常退出时重启
RestartSec=5s
```

**修改后**：
```ini
[Service]
Type=simple
ExecStart=%h/.local/bin/vocotype-fcitx5-backend
Restart=always            # ✅ 任何情况下都重启
RestartSec=5s
```

### 3. 重新加载并重启服务

```bash
systemctl --user daemon-reload
systemctl --user restart vocotype-fcitx5-backend.service
```

### 4. 重启 Fcitx5

```bash
fcitx5 -r
```

---

## 验证修复

### 方法 1：检查服务配置

```bash
systemctl --user cat vocotype-fcitx5-backend.service | grep Restart
```

应该看到：
```
Restart=always
```

### 方法 2：模拟休眠测试

1. 使用输入法确认当前正常工作
2. 手动终止服务：
   ```bash
   systemctl --user kill vocotype-fcitx5-backend.service
   ```
3. 等待 5 秒（RestartSec）
4. 检查服务是否自动重启：
   ```bash
   systemctl --user status vocotype-fcitx5-backend.service
   ```
   应该显示 `Active: active (running)`

5. 测试输入法是否恢复正常

### 方法 3：实际休眠测试

1. 进入系统休眠/待机
2. 唤醒后测试输入法
3. 如果仍然失效，运行诊断：
   ```bash
   ./tools/diagnostics/diagnose-fcitx5-backend.sh
   ```

---

## 临时快速恢复

如果还没修复配置，休眠后临时恢复使用：

```bash
# 方法 1：重启服务
systemctl --user restart vocotype-fcitx5-backend.service

# 方法 2：重启 Fcitx5
fcitx5 -r

# 两个命令都执行更保险
```

---

## 技术细节

### systemd Restart 选项说明

| 选项 | 含义 | 休眠后表现 |
|------|------|-----------|
| `no` | 不自动重启 | ❌ 服务停止 |
| `on-failure` | 仅异常退出时重启 | ❌ 服务停止（正常终止） |
| `on-abnormal` | 信号或超时时重启 | ⚠️ 取决于终止信号 |
| `on-abort` | 仅core dump时重启 | ❌ 服务停止 |
| `always` | 任何情况都重启 | ✅ 自动恢复 |

**休眠时的行为**：
- 系统发送 SIGTERM 或 SIGKILL 终止进程
- 进程正常退出（exit code = 0）
- `on-failure` 不会触发重启
- `always` 会触发重启

### 为什么不用 `WantedBy=sleep.target`

尝试过监听 `After=sleep.target`，但：
- 用户服务不一定能可靠接收到休眠事件
- 不同桌面环境行为不一致
- `Restart=always` 更简单可靠

---

## 新安装用户

从 2026-01-25 版本开始，安装脚本已包含此修复。新安装用户不需要手动操作。

如果你是在此日期后安装的，可以验证：

```bash
grep "Restart=" ~/.config/systemd/user/vocotype-fcitx5-backend.service
```

应该看到 `Restart=always`。

---

## 常见问题

### Q: 修复后内存占用会增加吗？

A: 不会。`Restart=always` 只是改变重启策略，不影响运行时行为。内存占用主要由 FunASR 模型决定（约 700MB-1.5GB）。

### Q: 服务会无限重启吗？

A: 不会。systemd 有内置的重启限制（默认 5 秒内重启 5 次会进入失败状态）。正常情况下服务会稳定运行。

### Q: 可以改回 `on-failure` 吗？

A: 可以，但会恢复休眠后失效的问题。建议保持 `always`。

### Q: 需要重新安装吗？

A: 不需要。运行修复脚本或手动修改配置文件即可。

---

## 报告问题

如果修复后仍有问题，请提供：

1. **诊断输出**：
   ```bash
   ./tools/diagnostics/diagnose-fcitx5-backend.sh > diagnosis.txt
   ```

2. **服务配置**：
   ```bash
   systemctl --user cat vocotype-fcitx5-backend.service > service-config.txt
   ```

3. **详细日志**：
   ```bash
   journalctl --user -u vocotype-fcitx5-backend.service -n 100 > service-logs.txt
   ```

提交到 GitHub Issues 并附上这些文件。

---

**最后更新**: 2026-01-24
**影响版本**: 2.1.2 及之前
**修复版本**: 2.1.3（计划）
