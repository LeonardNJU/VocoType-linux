# VocoType Fcitx5 配置和日志路径说明

## 配置文件位置

### 1. 后端服务配置
**路径**: `~/.config/vocotype/fcitx5-backend.json`

- 可通过环境变量 `VOCOTYPE_FCITX5_CONFIG` 自定义路径
- 默认不存在，使用内置默认配置
- 用于配置日志级别、日志文件等

**示例配置**（可选创建）:
```json
{
  "logging": {
    "file": true,
    "dir": "logs",
    "level": "INFO"
  }
}
```

### 2. 音频设备配置
**路径**: `~/.config/vocotype/audio.conf`

- 安装时运行 `setup-audio.py` 创建
- 存储麦克风设备信息和采样率

**示例内容**:
```ini
[audio]
device_name = USB Composite Device
sample_rate = 48000
```

或旧格式：
```ini
[audio]
device_id = 2
sample_rate = 44100
```

### 3. Rime 输入方案配置
**路径**: `~/.config/vocotype/rime/user.yaml`

- 安装时选择的 Rime 输入方案
- 可手动编辑切换方案

**示例内容**:
```yaml
# VoCoType RIME 用户配置
var:
  previously_selected_schema: "luna_pinyin"
```

---

## 日志文件位置

### 默认日志方式：systemd journal

**Fcitx5 版本默认将日志输出到 stderr**，由 systemd 捕获。

**查看日志的方式**:
```bash
# 查看最近 50 条日志
journalctl --user -u vocotype-fcitx5-backend.service -n 50

# 实时监控日志
journalctl --user -u vocotype-fcitx5-backend.service -f

# 查看特定时间段的日志
journalctl --user -u vocotype-fcitx5-backend.service --since "10 minutes ago"

# 查看启动以来的全部日志
journalctl --user -u vocotype-fcitx5-backend.service --no-pager
```

### 启用文件日志（可选）

如果需要将日志写入文件，需要创建配置文件：

1. **创建配置文件** `~/.config/vocotype/fcitx5-backend.json`:
   ```json
   {
     "logging": {
       "file": true,
       "dir": "logs",
       "level": "INFO"
     }
   }
   ```

2. **重启服务**:
   ```bash
   systemctl --user restart vocotype-fcitx5-backend.service
   ```

3. **日志文件路径**: `~/.local/share/vocotype-fcitx5/logs/`
   ```bash
   # 查看日志文件
   ls -lh ~/.local/share/vocotype-fcitx5/logs/

   # 查看最新日志
   tail -f ~/.local/share/vocotype-fcitx5/logs/*.log
   ```

---

## 其他重要路径

### 安装目录
**路径**: `~/.local/share/vocotype-fcitx5/`

包含：
- `backend/`: Python 后端代码
- `app/`: 语音识别核心代码
- `.venv/`: Python 虚拟环境
- `logs/`: 日志文件（如果启用）

### systemd 服务文件
**路径**: `~/.config/systemd/user/vocotype-fcitx5-backend.service`

- 定义后端服务的启动方式
- 修改后需要运行 `systemctl --user daemon-reload`

### C++ Addon
**路径**: `~/.local/lib64/fcitx5/vocotype.so`（或 `~/.local/lib/fcitx5/vocotype.so`）

- Fcitx5 加载的插件文件
- Addon 配置：`~/.local/share/fcitx5/addon/vocotype.conf`

### Unix Socket
**路径**: `/tmp/vocotype-fcitx5.sock`

- 运行时通信文件
- 如果丢失说明后端服务未运行

---

## 快速检查清单

```bash
# 1. 检查配置文件
ls -lh ~/.config/vocotype/

# 2. 检查安装目录
ls -lh ~/.local/share/vocotype-fcitx5/

# 3. 检查服务状态
systemctl --user status vocotype-fcitx5-backend.service

# 4. 查看日志
journalctl --user -u vocotype-fcitx5-backend.service -n 20

# 5. 检查 socket 文件
ls -lh /tmp/vocotype-fcitx5.sock
```

---

## 故障排查时需要的文件

当遇到问题需要报告时，请提供：

1. **服务日志**:
   ```bash
   journalctl --user -u vocotype-fcitx5-backend.service -n 100 > vocotype-logs.txt
   ```

2. **配置文件**（如果存在）:
   ```bash
   cat ~/.config/vocotype/fcitx5-backend.json
   cat ~/.config/vocotype/audio.conf
   ```

3. **服务状态**:
   ```bash
   systemctl --user status vocotype-fcitx5-backend.service
   ```

4. **诊断脚本输出**:
   ```bash
   ./tools/diagnostics/diagnose-fcitx5-backend.sh > diagnosis.txt
   ```
