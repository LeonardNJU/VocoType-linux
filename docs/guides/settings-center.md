# VoCoType 图形设置中心

VoCoType 设置中心管理统一的 VoCoType 配置，并提供 IBus 与 Fcitx 5 integration 的安装、卸载、预览、诊断、日志打包和反馈入口。

## 启动

已安装用户可以从应用菜单打开 **VoCoType 设置**，或运行：

```bash
vocotype-settings
```

从源码首次安装时运行：

```bash
bash installers/launch-settings.sh
```

通过 DEB、RPM 或 Arch 包安装时，系统包已经提供 Fcitx module、IBus component、桌面入口和安装源码。设置中心会识别 `/usr/share/vocotype/.system-package`，复用这些由包管理器维护的系统文件，只创建或修复用户级 Python 运行时；已有且内容一致的系统 IBus component 不会再次请求管理员授权。

Fcitx 5 与 IBus 的安装和卸载都使用同目录下的非交互 GUI worker：选项、下载进度、编译输出、清理进度和错误均显示在设置中心。缺少系统依赖、需要注册 IBus component，或清理旧版非托管系统 component 时，设置中心调用 `pkexec`，由桌面 Polkit 代理弹出密码、指纹或其他管理员授权框。VoCoType 不读取或保存管理员凭据，也不会打开终端。

## 页面

### 概览与安装

顶部使用 `IBus` 与 `Fcitx 5` 两个等宽页签，用户只需进入当前桌面实际使用的输入法框架。设置中心会立即记住最后选择的框架，下次打开仍停留在该页签。每个页签独立提供：

- 安装 / 修复对应的 VoCoType integration；
- 卸载对应 integration；
- 重启该框架的 VoCoType 后台；
- 重启 IBus 或 Fcitx 5 本体。

页签上方统一检查源码目录、Polkit 与原生软件包；下方保留快速 Doctor。卸载时仍可选择是否删除虚拟环境、模型缓存和共享用户数据。原生软件包存在时显示对应的包管理器卸载命令，不直接删除 `/usr` 文件。

### 逆文本标准化（ITN）

术语 canonicalization 始终启用；数字与 ITN 可以整体关闭。以下书写风格可独立控制：

```text
二零二六年五月十一号 → 2026/05/11
下午三点二十分       → 15:20
三百二十米           → 320m
一百二十八元         → ¥128
```

页面提供实时文本预览，不需要实际录音。

### Playground

Playground 与安装状态相互独立，用于真实体验验证：

- 枚举并选择输入设备，录制固定 3 秒 WAV；录音期间实时显示滚动波形；
- 枚举 PipeWire/PulseAudio 输出 sink，并将录音明确回放到所选扬声器或耳机，避免误落到无声 HDMI；
- 可选择 F9 极简或动画状态样式；默认极简，松开 F9 会立即从 `🎤 录音中...` 切换为 `⏳ 识别中`；
- 将同一段录音发送给当前 VoCoType ASR 后台，显示可编辑的转录结果；
- 输入文本测试 AI 润色，或填写编辑指令测试 AI 编辑；输出保持可编辑。

AI 区域默认置灰。必须先在“AI 润色”页启用功能、配置 endpoint/模型，并点击“测活 AI 端点 / 模型”成功后，当前配置才会在本次设置中心会话中解锁。修改 Provider、endpoint、模型或凭据后会重新锁定，要求再次测活。

录音文件保存在 `~/.cache/vocotype/playground/last-recording.wav`，权限为 `0600`，不会进入支持包。

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
- 固定快捷键语义：F9 直出识别，Shift+F9 润色；
- reasoning/thinking；
- 直接 API Key 或环境变量凭据；
- 端点 / 本地模型测活；测活结果用于解锁 Playground 的 AI 试用。

VoCoType 配置保存在 `~/.config/vocotype/`，文件权限为 `0600`。底层 integration 适配文件属于实现细节，设置中心不会把它们表述为安装了另一套输入法框架。直接 API Key 留空时保留旧值；可通过专门选项清除。

### Doctor

Doctor 会继续执行所有检查，而不是在第一个错误处停止。当前检查包括：

- Python 版本与核心依赖；
- Fcitx 5、全局 module 和旧版输入法条目；
- systemd 用户服务与 Unix socket ping；
- VoCoType 运行配置；
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
