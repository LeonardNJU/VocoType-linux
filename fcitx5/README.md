# VoCoType Fcitx 5 版本

VoCoType 离线语音输入法的 Fcitx 5 版本实现。

## 功能特性

- **语音输入** - 默认按住 F9 说话，松开自动识别并输入；`Shift+F9` 为长句模式（支持配置热键）
- **Rime 拼音** - 完整的 Rime 拼音输入支持
- **完全离线** - 所有识别在本地完成，零网络依赖
- **轻量高效** - 纯 CPU 推理，仅需 700MB 内存
- **快速响应** - 0.1 秒级识别速度

## 架构设计

```
Fcitx 5 Framework
    ↓ (C++ API)
C++ Addon (fcitx5/addon/)
    ├─ 监听 F9/Shift+F9 按键（语音）
    ├─ 监听其他按键（Rime）
    └─ 更新 UI
    ↓ (Unix Socket IPC)
Python Backend (fcitx5/backend/)
    ├─ 语音识别（FunASR）
    └─ Rime 拼音处理（pyrime）
```

## 系统要求

### 必需依赖

- **Fcitx 5** - 输入法框架
- **Python 3.11-3.12** - 后端运行环境（onnxruntime 暂不支持 3.13+）
- **编译工具**:
  - CMake 3.10+
  - C++17 编译器
  - pkg-config
- **开发库**:
  - `fcitx5-devel` (或 `libfcitx5-dev`)
  - `nlohmann-json-devel` (或 `nlohmann-json3-dev`)

### 可选依赖（推荐）

- **pyrime** - Rime 拼音输入支持（完整版）
- **fcitx5-rime** - 共享 Rime 配置（如果已安装）
- **rime-ice** - 现代词库和配置方案

## 安装

### 快速安装

```bash
cd VocoType-linux
bash fcitx5/scripts/install-fcitx5.sh
```

安装脚本会自动完成：
1. 检查 Fcitx 5 和编译依赖
2. 编译 C++ Addon
3. 安装 Python 后端
4. 配置 Python 环境（项目虚拟环境、用户级虚拟环境、系统 Python 或手动指定解释器）
5. 配置音频设备（可选）
6. 创建 systemd 服务

安装脚本还会询问是否启用 `Shift+F9` 长句 SLM 润色：
- 不启用（默认）：不安装/拉取 SLM 模型，`Shift+F9` 不会触发润色
- 启用：写入 `~/.config/vocotype/fcitx5-backend.json` 的 `slm` 配置，并可选择：
  - 本地模型（`local_ephemeral`）：按下预热，润色后释放
  - 远程 API（`remote`）：配置 `model`、`endpoint`、`api_key`

### 自定义快捷键

优先使用 Fcitx5 配置面板调整：

1. 打开 Fcitx5 配置
2. 进入输入法列表里的 VoCoType
3. 点击“配置”

Fcitx5 会将这些设置持久化到 `~/.config/fcitx5/inputmethod/vocotype.conf`。

- `PTTKey`：按住说话的主键，默认 `F9`
- `PTTHoldThresholdMs`：可选项，按住超过多少毫秒才开始录音，默认 `0`；小于等于 `0` 时不启用定时器，保持“按下立即录音”
- `LongModeModifier`：长句模式修饰键，默认 `Shift`，可选 `Shift` / `Ctrl` / `Alt` / `Super`
- `StripTrailingPeriodOnCommit`：默认关闭；开启后会在提交前移除文本末尾的 `。` 或 `.`
- `Fn` 通常不会作为独立按键事件上报给 Linux/Fcitx5，因此一般不能直接配置成 `PTTKey`
- 修改后需要重启 Fcitx5 或重新登录输入法会话

如果你想区分“长按开始录音”和“短按不录音”，就把 `PTTHoldThresholdMs` 调成大于 `0` 的值，例如 `180`。

### 手动安装

#### 1. 编译 C++ Addon

```bash
cd fcitx5/addon
mkdir build && cd build
cmake .. -DCMAKE_INSTALL_PREFIX=$HOME/.local
make -j$(nproc)
make install
```

#### 2. 安装配置文件

```bash
mkdir -p ~/.local/share/fcitx5/addon
mkdir -p ~/.local/share/fcitx5/inputmethod
cp fcitx5/data/vocotype.conf ~/.local/share/fcitx5/addon/
cp fcitx5/data/vocotype.conf.in ~/.local/share/fcitx5/inputmethod/vocotype.conf
```

#### 3. 安装 Python 后端

```bash
INSTALL_DIR=$HOME/.local/share/vocotype-fcitx5
mkdir -p "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR/scripts"
cp -r app "$INSTALL_DIR/"
cp -r fcitx5/backend "$INSTALL_DIR/"
cp vocotype_version.py "$INSTALL_DIR/"
cp scripts/setup-audio.py "$INSTALL_DIR/scripts/"

# 创建虚拟环境
python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install -r requirements.txt

# 安装 Rime 依赖
"$INSTALL_DIR/.venv/bin/pip" install pyrime
```

#### 4. 配置音频

```bash
"$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/scripts/setup-audio.py"
```

## 使用方法

### 1. 启动后台服务

**方式 A：手动启动（临时）**

```bash
vocotype-fcitx5-backend &
```

**方式 B：systemd 自动启动（推荐）**

```bash
# 启用并启动服务
systemctl --user enable vocotype-fcitx5-backend.service
systemctl --user start vocotype-fcitx5-backend.service

# 查看服务状态
systemctl --user status vocotype-fcitx5-backend.service

# 查看日志
journalctl --user -u vocotype-fcitx5-backend.service -f
```

### 日志（可选）

默认只输出到 stderr，不会生成日志文件。需要文件日志时，在
`~/.config/vocotype/fcitx5-backend.json` 添加：

```json
{
  "logging": {
    "file": true,
    "dir": "logs",
    "level": "INFO"
  }
}
```

日志目录默认是 `~/.local/share/vocotype-fcitx5/logs/`。

### 长句模式（Shift+F9）配置

在 `~/.config/vocotype/fcitx5-backend.json` 中配置 `slm`（默认关闭）。

推荐使用本地一次性加载（按下 `Shift+F9` 预加载，润色后立即释放）：

```json
{
  "slm": {
    "enabled": true,
    "provider": "local_ephemeral",
    "model": "Qwen/Qwen3.5-0.8B",
    "local_model": "Qwen/Qwen3.5-0.8B",
    "warmup_timeout_ms": 90000,
    "keepalive_ms": 60000,
    "ready_wait_ms": 2000,
    "timeout_ms": 12000,
    "min_chars": 8,
    "max_tokens": 96,
    "enable_thinking": false
  }
}
```

- `F9` 不会触发 SLM，保持低延迟
- `Shift+F9` 才会触发 SLM
- `local_ephemeral` 会在每次长句流程结束后释放模型内存
- 若 SLM 调用失败，会显示错误提示，不提交回退原文

远程 API（OpenAI 兼容）示例：

```json
{
  "slm": {
    "enabled": true,
    "provider": "remote",
    "model": "gpt-4o",
    "endpoint": "http://<host>:<port>/v1/chat/completions",
    "api_key": "sk-***",
    "timeout_ms": 20000,
    "min_chars": 8,
    "max_tokens": 128,
    "retry_without_proxy": true
  }
}
```

常用参数：
- `provider`：`local_ephemeral` / `remote`
- `min_chars`：长句触发阈值（默认 `8`）
- `max_tokens`：润色输出预算
- `enable_thinking`：是否允许思考输出（默认 `false`）
- `retry_without_proxy`：远程请求失败时绕过代理直连重试（默认 `true`）

### 2. 重启 Fcitx 5

```bash
fcitx5 -r
```

### 3. 添加输入法

1. 打开 Fcitx 5 配置工具：
   ```bash
   fcitx5-configtool
   ```

2. 在"输入法"标签中，点击"添加输入法"

3. 搜索"VoCoType"，添加到当前输入法列表

4. 切换到 VoCoType 输入法

### 4. 开始使用

- **语音输入**:
  - 按住 `F9`：极速模式（仅 ASR + 标点）
  - 按住 `Shift+F9`：长句模式（ASR + 标点 + 可选 SLM 润色）
- **拼音输入**: 正常打字，使用 Rime 拼音输入

## Rime 配置

VoCoType Fcitx 5 版本使用**独立的 Rime 配置目录**：

```
~/.local/share/fcitx5/rime/
```

这意味着：
- 与 fcitx5-rime 共享配置目录
- 推荐使用 [rime-ice（雾凇拼音）](https://github.com/iDvel/rime-ice) 获得更好体验
- 所有 Rime 自定义配置都适用
- 安装脚本选择的方案会记录在 `~/.config/vocotype/rime/user.yaml`

详见：[RIME_CONFIG_GUIDE.md](../RIME_CONFIG_GUIDE.md)

## 故障排查

### Backend 无法启动

**问题**: `vocotype-fcitx5-backend` 启动失败

**解决方案**:
1. 检查 Python 依赖是否完整安装：
   ```bash
   ~/.local/share/vocotype-fcitx5/.venv/bin/python -c "import pyrime; print('OK')"
   ```

2. 查看详细错误日志：
   ```bash
   ~/.local/share/vocotype-fcitx5/.venv/bin/python \
       ~/.local/share/vocotype-fcitx5/backend/fcitx5_server.py --debug
   ```

### C++ Addon 无法加载

**问题**: Fcitx 5 找不到 VoCoType 插件

**解决方案**:
1. 检查插件是否安装：
   ```bash
   ls ~/.local/lib/fcitx5/vocotype.so
   ls ~/.local/share/fcitx5/addon/vocotype.conf
   ```

2. 检查 Fcitx 5 日志：
   ```bash
   fcitx5 --verbose=10
   ```

### 语音识别无响应

**问题**: F9 按键无反应或识别失败

**解决方案**:
1. 检查 Backend 是否运行：
   ```bash
   pgrep -fa fcitx5_server.py
   ```

2. 测试 IPC 连接：
   ```bash
   echo '{"type":"ping"}' | nc -U /tmp/vocotype-fcitx5.sock
   # 应返回: {"pong":true}
   ```

3. 重新配置音频设备：
   ```bash
   ~/.local/share/vocotype-fcitx5/.venv/bin/python   ~/.local/share/vocotype-fcitx5/scripts/setup-audio.py
   ```

### Rime 拼音不可用

**问题**: 只有语音输入，没有拼音功能

**解决方案**:
1. 检查 pyrime 是否安装：
   ```bash
   ~/.local/share/vocotype-fcitx5/.venv/bin/python -c "import pyrime"
   ```

2. 如果未安装，补装 pyrime：
   ```bash
  ~/.local/share/vocotype-fcitx5/.venv/bin/pip install pyrime
   ```

3. 检查 Rime 数据目录：
   ```bash
   ls /usr/share/rime-data/
   ```

## 与 IBus 版本的区别

| 特性 | IBus 版本 | Fcitx 5 版本 |
|-----|----------|-------------|
| 输入法框架 | IBus | Fcitx 5 |
| 实现语言 | 纯 Python | C++ + Python (IPC) |
| Rime 配置 | `~/.config/ibus/rime/` | `~/.local/share/fcitx5/rime/` |
| 安装位置 | `~/.local/share/vocotype/` | `~/.local/share/vocotype-fcitx5/` |
| 后台服务 | 集成在引擎内 | 独立 Python 进程 |

两个版本**可以同时安装**，互不干扰。

## 卸载

```bash
# 停止并禁用服务
systemctl --user stop vocotype-fcitx5-backend.service
systemctl --user disable vocotype-fcitx5-backend.service

# 删除文件
rm -rf ~/.local/share/vocotype-fcitx5
rm ~/.local/lib/fcitx5/vocotype.so
rm ~/.local/share/fcitx5/addon/vocotype.conf
rm ~/.local/share/fcitx5/inputmethod/vocotype.conf
rm ~/.local/bin/vocotype-fcitx5-backend
rm ~/.config/systemd/user/vocotype-fcitx5-backend.service

# 重启 Fcitx 5
fcitx5 -r
```

## 技术细节

### 代码复用

IBus 和 Fcitx 5 版本是**并列独立**的实现，共享 VoCoType 核心：

- **语音识别**: 共享 `app/funasr_server.py` 核心引擎
- **Rime 集成**: 各自独立实现
  - IBus 版本: `ibus/engine.py` + ibus-rime 配置
  - Fcitx 5 版本: `fcitx5/backend/rime_handler.py` + fcitx5-rime 配置
- **音频采集**: 共享录音逻辑

### IPC 协议

C++ Addon 与 Python Backend 通过 Unix Socket 通信，协议格式为 JSON：

**语音识别请求**:
```json
{"type": "transcribe", "audio_path": "/tmp/xxx.wav"}
```

**Rime 按键请求**:
```json
{"type": "key_event", "keyval": 97, "mask": 0}
```

详见：[fcitx5-with-rime-integration.md](../.claude/plans/fcitx5-with-rime-integration.md)

## 开发

### 重新编译 C++ Addon

```bash
cd fcitx5/addon/build
make -j$(nproc)
make install
fcitx5 -r
```

### 调试 Python Backend

```bash
# 前台运行，查看详细日志
~/.local/share/vocotype-fcitx5/.venv/bin/python \
    ~/.local/share/vocotype-fcitx5/backend/fcitx5_server.py --debug
```

### 测试 IPC 通信

```bash
# Ping 测试
echo '{"type":"ping"}' | nc -U /tmp/vocotype-fcitx5.sock

# Rime 按键测试（'a' 键）
echo '{"type":"key_event","keyval":97,"mask":0}' | nc -U /tmp/vocotype-fcitx5.sock
```

## 许可证

与主项目相同 (GPL)

## 贡献

欢迎提交 Issue 和 Pull Request！

---

**相关文档**:
- [项目主页](../readme.md)
- [IBus 版本](../ibus/README.md)
- [Rime 配置指南](../RIME_CONFIG_GUIDE.md)
