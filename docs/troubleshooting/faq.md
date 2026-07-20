# VoCoType 常见问题与故障排除

本文档总结了VoCoType使用过程中的常见问题及解决方案。

## 目录

- [安装问题](#安装问题)
- [Rime拼音输入问题](#rime拼音输入问题)
- [音频和识别问题](#音频和识别问题)
- [如何查看日志](#如何查看日志)

---

## 安装问题

### Python版本必须是3.11-3.12
### Python版本必须是3.11-3.12

**重要**：VoCoType要求Python版本必须是3.11或3.12，**不支持Python 3.13及以上版本以及3.10及以下版本**。

**为什么有这个限制**：
VoCoType依赖的onnxruntime库目前只支持Python 3.11-3.12，不支持3.13+。  
python 3.10版本的pyrime包版本太低了，没法成功调用librime，所以可用的最低版本是3.11

**常见错误**：
```
ERROR: Could not find a version that satisfies the requirement onnxruntime
ERROR: No matching distribution found for onnxruntime
```

**检查当前Python版本**：
```bash
python --version
# 或
python3 --version
```

**解决方案**：

**方法1：使用uv自动管理（推荐）**
```bash
# 安装uv
pip install uv

# uv会自动下载并使用正确的Python版本
# 运行安装脚本时会自动处理
./ibus/scripts/install.sh
```

**方法2：手动安装正确的Python版本**

**Debian/Ubuntu**：
```bash
# 安装Python 3.12
sudo apt install python3.12 python3.12-venv python3.12-dev

# 使用Python 3.12创建虚拟环境
python3.12 -m venv .venv
source .venv/bin/activate
```

**Fedora**：
```bash
# 安装Python 3.12
sudo dnf install python3.12 python3.12-devel

# 使用Python 3.12创建虚拟环境
python3.12 -m venv .venv
source .venv/bin/activate
```

**验证**：
```bash
# 激活虚拟环境后检查版本
python --version
# 应该显示 Python 3.11.x 或 3.12.x
# 应该显示 Python 3.11.x 或 3.12.x
```

---

### 缺少系统依赖

**错误**：编译失败，缺少系统库

**解决方案**：

**Debian/Ubuntu**：
```bash
sudo apt install build-essential pkg-config libcairo2-dev \
  libgirepository1.0-dev libportaudio2
```

**Fedora/RHEL**：
```bash
sudo dnf install gcc-c++ pkgconfig cairo-devel \
  gobject-introspection-devel portaudio
```

---

### Rime开发库缺失（完整版）

**错误**：pyrime安装失败

**解决方案**：

**Fedora/RHEL**：
```bash
sudo dnf install librime-devel ibus-rime
```

**Debian/Ubuntu**：
```bash
sudo apt install librime-dev ibus-rime
```
Ubuntu 22.04 官方源的 librime-dev/ibus-rime 版本偏旧，必要时请卸载后手动编译安装。

然后重新安装：
```bash
pip install --force-reinstall pyrime
```

---

### IBus组件未识别（GNOME）

**症状**：安装后在输入法列表中找不到VoCoType

**原因**：GNOME要求组件安装在系统目录

**解决方案**：
```bash
sudo cp ~/.local/share/ibus/component/vocotype.xml \
  /usr/share/ibus/component/
ibus restart
```

---

## Rime拼音输入问题

### 输入后直接出字母，无法使用拼音

**症状**：
- 按键后直接输出字母，没有候选词
- 无法使用拼音输入中文

**可能原因**：
1. Rime配置未正确加载
2. Rime服务未正常启动
3. 输入方案未正确选择

**排查步骤**：

#### 1. 查看日志
```bash
# 使用日志分析工具
./tools/diagnostics/analyze-rime-logs.sh

# 或直接查看日志
tail -100 ~/.local/share/vocotype/ibus.log
```

#### 2. 检查关键日志信息

查看是否有以下错误：
```bash
# 查看Rime初始化日志
grep -i "rime" ~/.local/share/vocotype/ibus.log | tail -20

# 查看错误信息
grep -i "error\|failed" ~/.local/share/vocotype/ibus.log | tail -20
```

**正常情况应该看到**：
```
Rime API 创建 (addr=...), 初始化中...
Session ID: XXX
尝试使用用户配置的方案: double_pinyin_flypy
```

**如果看到错误**：
- `找不到可用的 Rime 配置目录` → 安装ibus-rime
- `pyrime 未安装` → 安装librime-devel和pyrime
- `初始化失败` → 检查Rime配置文件

#### 3. 验证Rime配置

```bash
# 检查Rime配置目录
ls ~/.config/ibus/rime/

# 应该看到这些文件
# - default.yaml
# - build/ 目录
```

如果配置缺失，重新部署Rime：
```bash
ibus-daemon -drx
```

---

## 音频和识别问题

### 麦克风无法使用

**错误**：无法创建音频输入流

**解决方案**：

1. 安装PortAudio：
   ```bash
   # Debian/Ubuntu
   sudo apt install libportaudio2

   # Fedora
   sudo dnf install portaudio
   ```

2. 重新配置音频设备：
   ```bash
   python installers/setup-audio.py
   ```

3. 检查可用设备：
   ```bash
   python -c "import sounddevice; print(sounddevice.query_devices())"
   ```

---

### 模型加载失败

**错误**：ASR模型加载失败

**原因**：
- 首次下载模型需要时间（~1GB）
- 网络连接问题
- 磁盘空间不足

**解决方案**：

1. 检查磁盘空间：
   ```bash
   df -h ~/.cache/
   ```

2. 清除缓存重试：
   ```bash
   rm -rf ~/.cache/modelscope/
   ```

3. 查看下载进度：
   ```bash
   tail -f ~/.local/share/vocotype/ibus.log
   ```

---

## 如何查看日志

### 日志位置

**IBus版本**：
```bash
~/.local/share/vocotype/ibus.log
```

**Fcitx 5版本**：
```bash
~/.local/share/vocotype-fcitx5/logs/
```

### 使用日志分析工具

VoCoType提供了交互式日志分析工具：

```bash
./tools/diagnostics/analyze-rime-logs.sh
```

**功能菜单**：
1. 查看session生命周期
2. 查看错误日志
3. 查看按键处理日志
4. 查看编码问题
5. Session统计
6. 实时监控
7. 查看完整日志

### 常用日志查看命令

**查看最近的错误**：
```bash
grep -i "error\|failed" ~/.local/share/vocotype/ibus.log | tail -20
```

**查看Rime相关日志**：
```bash
grep -i "rime" ~/.local/share/vocotype/ibus.log | tail -30
```

**实时监控日志**：
```bash
tail -f ~/.local/share/vocotype/ibus.log
```

**查看按键处理**：
```bash
grep "Key event:\|handled=" ~/.local/share/vocotype/ibus.log | tail -20
```

---

## 获取帮助

如果以上方案无法解决问题：

1. **查看完整日志**：
   ```bash
   less ~/.local/share/vocotype/ibus.log
   ```

2. **使用日志分析脚本**：
   ```bash
   ./tools/diagnostics/analyze-rime-logs.sh
   ```

3. **提交Issue**：
   - 包含错误信息
   - 包含相关日志片段
   - 说明操作系统和Python版本

---

**最后更新**：2026-01-09
- 每个实例有独立的输入上下文
- 在 `do_disable()` 时释放session

**验证**：
```bash
grep "handled=" ~/.local/share/vocotype/ibus.log | tail -20
```
应该看到大部分按键 `handled=True`。

---

### 3. Session内存泄漏

**症状**：
- Session数量不断增加
- 内存占用持续上升
- 日志显示：`Engine #7 created`（编号不断增长）

**原因**：IBus不会调用 `do_destroy()`，engine实例被缓存不销毁，导致session累积。

**解决方案**：
在 `do_disable()` 中释放session（`ibus/engine.py:321-332`）：
- 当用户切换到其他输入法时释放session
- 使用 `api.destroy_session(session_id)` 释放资源
- 从全局跟踪集合中移除session ID

**验证**：
```bash
grep "active sessions:" ~/.local/share/vocotype/ibus.log | tail -10
```
应该看到session被正确释放，活跃数量保持在较低水平。

---

## 安装和依赖问题

### 4. Python 3.13+不兼容

**错误**：安装失败，onnxruntime无法安装

**原因**：onnxruntime官方构建仅支持Python 3.11-3.12

**解决方案**：
1. 使用Python 3.11或3.12
2. 安装 `uv` 工具自动管理Python版本：
   ```bash
   pip install uv
   ```
3. Debian/Ubuntu用户需安装对应版本的venv包：
   ```bash
   sudo apt install python3.12-venv
   ```

**参考**：https://github.com/microsoft/onnxruntime/issues/21292

---

### 5. 缺少编译依赖

**错误**：
```
缺少编译 IBus 引擎依赖所需的系统库
```

**原因**：缺少build-essential、pkg-config、cairo、gobject-introspection等开发库

**解决方案**：

**Debian/Ubuntu**：
```bash
sudo apt install build-essential pkg-config libcairo2-dev libgirepository1.0-dev
```

**Fedora/RHEL**：
```bash
sudo dnf install gcc-c++ pkgconfig cairo-devel gobject-introspection-devel
```

**Arch**：
```bash
sudo pacman -S base-devel pkg-config cairo gobject-introspection
```

---

### 6. Rime开发库缺失

**错误**：
```
pyrime 安装失败
找不到可用的 Rime 配置目录
```

**原因**：缺少 `librime-devel` 或 `librime-dev` 系统包

**解决方案**：

**Fedora/RHEL**：
```bash
sudo dnf install librime-devel ibus-rime
```

**Debian/Ubuntu**：
```bash
sudo apt install librime-dev ibus-rime
```
Ubuntu 22.04 官方源的 librime-dev/ibus-rime 版本偏旧，必要时请卸载后手动编译安装。

**Arch**：
```bash
sudo pacman -S librime ibus-rime
```

然后重新安装pyrime：
```bash
pip install --force-reinstall pyrime
```

---

### 7. IBus组件安装问题（GNOME）

**症状**：安装后在输入法列表中找不到VoCoType

**原因**：GNOME要求IBus组件安装在系统目录 `/usr/share/ibus/component/`，用户级安装不被识别

**解决方案**：
使用sudo安装到系统目录：
```bash
sudo cp ~/.local/share/ibus/component/vocotype.xml /usr/share/ibus/component/
```

然后重启IBus：
```bash
ibus restart
```

---

### 8. Fcitx 5编译失败

**错误**：
```
CMake Error: Could not find Fcitx5Core
nlohmann-json not found
```

**原因**：缺少Fcitx 5开发库或nlohmann-json

**解决方案**：

**Debian/Ubuntu**：
```bash
sudo apt install cmake pkg-config libfcitx5-dev nlohmann-json3-dev
```

**Fedora**：
```bash
sudo dnf install cmake pkgconfig fcitx5-devel json-devel
```

**Arch**：
```bash
sudo pacman -S cmake pkgconfig fcitx5 nlohmann-json
```

验证：
```bash
pkg-config --exists Fcitx5Core && echo "OK"
pkg-config --exists nlohmann_json && echo "OK"
```

---

### 9. Fcitx 5后端服务无法启动

**错误**：
```
Backend 无法启动
/tmp/vocotype-fcitx5.sock not found
```

**原因**：systemd服务未启动或Python依赖缺失

**解决方案**：

1. 检查服务状态：
   ```bash
   systemctl --user status vocotype-fcitx5-backend.service
   journalctl --user -u vocotype-fcitx5-backend.service -f
   ```

2. 验证依赖：
   ```bash
   ~/.local/share/vocotype-fcitx5/.venv/bin/python -c "import pyrime; print('OK')"
   ```

3. 启用并启动服务：
   ```bash
   systemctl --user enable vocotype-fcitx5-backend.service
   systemctl --user start vocotype-fcitx5-backend.service
   ```

---

## 音频和识别问题

### 10. 音频设备未检测到

**错误**：
```
无法创建音频输入流
```

**原因**：
- PortAudio库未安装
- 音频设备ID配置错误
- 麦克风权限问题

**解决方案**：

1. 安装PortAudio：
   ```bash
   # Debian/Ubuntu
   sudo apt install libportaudio2

   # Fedora
   sudo dnf install portaudio
   ```

2. 重新配置音频设备：
   ```bash
   python installers/setup-audio.py
   ```

3. 检查可用设备：
   ```bash
   python -c "import sounddevice; print(sounddevice.query_devices())"
   ```

4. 验证麦克风权限（系统设置）

---

### 11. FunASR模型加载失败

**错误**：
```
ASR模型加载失败
ASR 模型目录缺少 model.onnx
```

**原因**：
- 网络连接慢，首次下载模型超时（~1GB）
- 磁盘空间不足
- 模型缓存损坏

**解决方案**：

1. 检查磁盘空间：
   ```bash
   df -h ~/.cache/
   ```

2. 清除模型缓存并重试：
   ```bash
   rm -rf ~/.cache/modelscope/
   ```

3. 检查日志：
   ```bash
   tail -f ~/.local/share/vocotype/ibus.log
   ```

4. 确保网络连接稳定，模型下载可能需要几分钟

---

### 12. 音频队列溢出

**警告**：
```
音频队列已满，丢弃音频帧
```

**原因**：系统负载高，音频缓冲区处理不及时

**解决方案**：
- 这是正常的保护机制，防止内存溢出
- 如果频繁出现，考虑：
  - 关闭其他占用CPU的程序
  - 增加系统内存（推荐8GB+）
  - 检查是否有后台进程占用资源

---

## 调试指南

### 日志位置

**默认位置**：
- IBus版本：`~/.local/share/vocotype/ibus.log`
- Fcitx 5版本：默认仅输出到 stderr；启用文件日志后写入 `~/.local/share/vocotype-fcitx5/logs/`

**Fcitx 5 启用文件日志**：
在 `~/.config/vocotype/fcitx5-backend.json` 添加：
```json
{
  "logging": {
    "file": true,
    "dir": "logs",
    "level": "INFO"
  }
}
```

**自定义位置（IBus）**：
```bash
export VOCOTYPE_LOG_FILE="/path/to/custom.log"
```

### 关键日志模式

#### Session生命周期
```
Session ID: <id> created, active sessions: <count>
Rime session <id> released on disable, active sessions: <count>
```

#### 按键处理
```
Key event: keyval=<val>, keycode=<code>, state=<state>
Rime process_key: keyval=<val> mask=<mask> handled=<bool>
```

#### 错误信息
```
ERROR - <error message>
Failed to <operation>
```

### 使用日志分析脚本

VoCoType提供了交互式日志分析工具：

```bash
./tools/diagnostics/analyze-rime-logs.sh
```

**功能**：
1. 查看session生命周期
2. 查看错误日志
3. 查看按键处理日志
4. 查看编码问题
5. Session统计
6. 实时监控
7. 查看完整日志

**实时监控示例**：
```bash
./tools/diagnostics/analyze-rime-logs.sh
# 选择选项 6 进入实时监控模式
```

### 验证步骤

#### 1. 检查session管理
```bash
grep "active sessions:" ~/.local/share/vocotype/ibus.log | tail -10
```
应该看到session被正确创建和释放。

#### 2. 检查按键处理
```bash
grep "handled=" ~/.local/share/vocotype/ibus.log | tail -20
```
应该看到大部分按键 `handled=True`。

#### 3. 检查编码问题
```bash
grep -i "decode\|schema" ~/.local/share/vocotype/ibus.log
```
不应该看到decode错误。

#### 4. 检查错误日志
```bash
grep -E "ERROR|Failed" ~/.local/share/vocotype/ibus.log
```
查看是否有未解决的错误。

---

## 其他问题

### 13. 内存占用高

**症状**：识别时内存占用~700MB

**原因**：
- FunASR模型较大
- 音频缓冲区占用
- librosa初始化开销

**解决方案**：
- 这是正常现象，推荐8GB+内存
- 模型已在初始化时预热，避免首次使用延迟
- 监控内存：`free -h` 或 `top`

---

### 14. 配置文件问题

**错误**：
```
音频配置文件不存在
```

**原因**：首次设置未完成或配置目录权限问题

**解决方案**：
1. 重新运行音频设置：
   ```bash
   python installers/setup-audio.py
   ```

2. 检查配置目录权限：
   ```bash
   ls -la ~/.config/vocotype/
   ```

3. 手动创建目录（如需要）：
   ```bash
   mkdir -p ~/.config/vocotype/
   ```

**提示**：
`~/.config/vocotype/audio.conf` 现在支持 `device_name`，用于避免设备编号变化带来的失效：
```ini
[audio]
device_name = USB Composite Device
sample_rate = 48000
```
如果仍使用旧的 `device_id`，也会兼容读取。

---

## 获取帮助

如果以上方案无法解决问题：

1. **查看完整日志**：
   ```bash
   less ~/.local/share/vocotype/ibus.log
   ```

2. **使用日志分析脚本**：
   ```bash
   ./tools/diagnostics/analyze-rime-logs.sh
   ```

3. **提交Issue**：
   - 包含错误信息
   - 包含相关日志片段
   - 说明操作系统和Python版本

---

**最后更新**：2026-01-09


---

## 图形 Doctor、支持包与反馈

打开 `vocotype-settings` → **诊断**，先运行 Doctor。它会检查 Python、依赖、Fcitx module、旧版输入法条目、后台服务、IPC socket、配置、术语 YAML、麦克风和 ITN。

仍有问题时点击 **导出支持包**。生成的 `~/Downloads/vocotype-support-*.tar.gz` 不含录音、API Key 或用户词典正文，可直接附到 issue。

反馈页面在未配置 endpoint 时打开预填 GitHub issue；只有明确填写反馈 endpoint 后才会直接发送数据。
