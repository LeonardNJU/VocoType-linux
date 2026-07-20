# VoCoType 图形设置中心

VoCoType 设置中心统一管理 Fcitx 5 与 IBus 的运行配置，并提供安装、预览、诊断、日志打包和反馈入口。

## 启动

已安装用户可以从应用菜单打开 **VoCoType 设置**，或运行：

```bash
vocotype-settings
```

从源码首次安装时运行：

```bash
bash scripts/launch-settings.sh
```

通过 DEB、RPM 或 Arch 包安装时，系统包已经提供 Fcitx module、IBus component、桌面入口和安装源码。设置中心会识别 `/usr/share/vocotype/.system-package`，复用这些由包管理器维护的系统文件，只创建或修复用户级 Python 运行时；已有且内容一致的系统 IBus component 不会再次请求管理员授权。

Fcitx 5 与 IBus 的“安装 / 修复”都使用窗口内非交互后端：选项、下载进度、编译输出和错误均显示在设置中心。缺少系统依赖或需要把 IBus component 注册到 `/usr/share/ibus/component` 时，设置中心调用 `pkexec`，由桌面 Polkit 代理弹出密码、指纹或其他管理员授权框。VoCoType 不读取或保存管理员凭据，也不会打开终端。

## 页面

### 概览与安装

- 检查源码目录、Fcitx 全局 module 和 addon 元数据；
- 执行用户级安装、升级或修复；
- 重启后台服务或 Fcitx 5；
- 快速运行 Doctor。

### 语音识别与 ITN

页面可枚举 PortAudio 输入设备、保存设备名称/ID与原生采样率，并执行不落盘的 2 秒录音电平测试。安装器因此可以跳过旧的终端麦克风向导。

术语 canonicalization 始终启用；数字与 ITN 可以整体关闭。以下书写风格可独立控制：

```text
二零二六年五月十一号 → 2026/05/11
下午三点二十分       → 15:20
三百二十米           → 320m
一百二十八元         → ¥128
```

页面提供实时文本预览，不需要实际录音。

### 用户词典

直接编辑 `~/.config/vocotype/terms.yaml`，保存前进行 YAML 验证。同一条术语可同时用于：

- Contextual Paraformer 原生 hotword；
- alias 到 canonical 的确定性替换；
- 后续 ITN 与紧凑格式保护。

### AI 润色

支持：

- 启用/关闭润色；
- 远程 OpenAI-compatible 或本地按需 provider；
- endpoint、模型、最少字符数和流式空闲超时；
- F9 是否默认润色；
- reasoning/thinking；
- 直接 API Key 或环境变量凭据；
- 实际连接测试。

运行配置同步写入：

```text
~/.config/vocotype/fcitx5-backend.json
~/.config/vocotype/ibus.json
```

文件权限为 `0600`。直接 API Key 留空时保留旧值；可通过专门选项清除。

### Doctor

Doctor 会继续执行所有检查，而不是在第一个错误处停止。当前检查包括：

- Python 版本与核心依赖；
- Fcitx 5、全局 module 和旧版输入法条目；
- systemd 用户服务与 Unix socket ping；
- IBus/Fcitx JSON 配置；
- 用户词典 YAML；
- 麦克风输入设备；
- ITN 实际预览。

命令行版本：

```bash
vocotype-doctor
vocotype-doctor --json
vocotype-doctor --probe-slm
```

### 支持包

支持包默认输出到 `~/Downloads/vocotype-support-*.tar.gz`，包含 Doctor、systemd 日志、Fcitx diagnose 和脱敏配置。

明确不包含：

- 原始录音；
- API Key、Authorization、token 或 password；
- 用户词典正文。

已知包含口述文本的 VoCoType 日志行会被整行脱敏。系统日志仍可能含用户名、主机名、文件路径或其他上下文，支持包内附 `PRIVACY.txt`，发送前应检查。

### 反馈

未配置反馈端点时，按钮会打开预填的 GitHub issue。设置项目运营的 HTTPS endpoint 后，设置中心可以直接 POST JSON；用户可选择附带 Doctor 和不超过 5 MiB 的支持包。

反馈 endpoint 接收格式：

```json
{
  "product": "VoCoType-linux",
  "version": "...",
  "message": "...",
  "platform": "...",
  "doctor": [],
  "bundle_name": "vocotype-support-....tar.gz",
  "bundle_base64": "..."
}
```

endpoint 留空时不会向任何第三方自动发送数据。
