# VoCoType Fcitx 5 全局模块

VoCoType 在 Fcitx 5 下以 **全局 Module** 运行，而不是一个独立输入法。
安装后继续使用你原来的 Rime、拼音、Mozc、键盘布局或其他 Fcitx 5 输入法；
VoCoType 只拦截配置的 PTT 热键并把语音识别结果直接提交到当前输入框。

## 主要能力

- `F9`：按住录音，松开执行本地 ASR 并提交。
- `Shift+F9`：长句模式；远程 SLM 生成期间在输入面板实时显示可见预览。
- 在所有 Fcitx 5 输入法下生效，不再代理普通键盘事件。
- 不依赖 `pyrime`，不创建独立 Rime session，不复制候选词或 preedit。
- 按下热键时记录原始 `InputContext`；焦点变化后取消或丢弃结果，避免误输到其他窗口。
- 当前输入法存在未提交的 preedit/candidate 时默认不启动录音，避免破坏正在进行的组合输入。

## 架构

```text
应用输入框
    ↕
当前原生输入法（fcitx5-rime / pinyin / mozc / keyboard / ...）
    ↕
Fcitx 5 event pipeline
    └── VoCoType Module
          ├── 监听 F9 / Shift+F9
          ├── 启动录音进程
          ├── 通过 Unix Socket 调用 Python backend
          └── InputContext::commitString() 提交结果
```

代码位置：

```text
fcitx5/module/                 Fcitx 5 全局 C++ module
fcitx5/backend/                Python ASR/SLM backend
fcitx5/backend/audio_recorder.py
fcitx5/data/vocotype.conf      Category=Module 的 addon 元数据
```


## 系统要求

- Linux 与 Fcitx 5。
- Python 3.11 或 3.12。
- CMake、C++20 编译器、pkg-config。
- `libfcitx5-dev` / `fcitx5-devel`。
- `nlohmann-json3-dev` / `json-devel`。

Fcitx 版本不需要 `pyrime`。用户需要 Rime 时直接安装和使用发行版提供的
`fcitx5-rime`，VoCoType 会在它处于活动状态时照常工作。

## 图形安装（推荐）

```bash
git clone https://github.com/LeonardNJU/VocoType-linux.git
cd VocoType-linux
bash installers/launch-settings.sh
```

在“概览与安装”点击 **安装 / 修复 VoCoType（Fcitx 5）** 或 **卸载 VoCoType（Fcitx 5）**。已安装用户可直接从应用菜单打开 **VoCoType 设置**。

## 命令行安装

```bash
bash fcitx5/scripts/install.sh
systemctl --user enable --now vocotype-fcitx5-backend.service
fcitx5 -r
```

设置中心中的 Fcitx 安装器会在窗口内执行；缺少系统包时通过 Polkit 授权自动安装。安装后端会：

1. 编译并安装 `vocotype.so` 全局 module。
2. 安装 addon 元数据到 `~/.local/share/fcitx5/addon/vocotype.conf`。
3. 删除旧版 `~/.local/share/fcitx5/inputmethod/vocotype.conf` 输入法条目。
4. 安装 Python 后端和录音启动器。
5. 创建并启动 systemd 用户服务所需文件。
6. 配置音频设备和可选 SLM。
7. 安装 `vocotype-settings`、桌面入口和 Doctor。

无需在“输入法列表”中添加 VoCoType。可在 `fcitx5-configtool` 的附加组件页面
确认 **VoCoType Voice Input** 已启用。


## 图形设置、Doctor 与支持包

```bash
vocotype-settings
vocotype-doctor
```

设置中心可配置 AI endpoint/API Key、编辑术语、预览 ITN、安装/修复、重启服务并生成脱敏支持包。完整说明见 [`docs/guides/settings-center.md`](../docs/guides/settings-center.md)。

## Module 配置

打开 `fcitx5-configtool`，在附加组件中选择 VoCoType 进行配置。配置保存在：

```text
~/.config/fcitx5/conf/vocotype.conf
```

可用选项：

- `PTTKey`：主热键，默认 `F9`。
- `PTTHoldThresholdMs`：超过指定时长才开始录音；默认 `0`，即按下立即开始。
- `LongModeModifier`：润色模式临时反转修饰键，默认 `Shift`。
- `PolishByDefault`：普通 F9 是否默认润色，默认关闭。
- `PolishMinChars`：ASR 文本达到多少字符才调用 SLM，默认 `8`。
- `PolishTimeoutMs`：流式输出空闲超时，默认 `20000` 毫秒。
- `EnableThinking`：是否允许模型 reasoning；预览和最终提交仍会过滤 thinking。
- `BlockWhenComposing`：当前输入法存在未提交组合时不启动录音，默认开启。
- `StripTrailingPeriodOnCommit`：提交前移除末尾 `。` 或 `.`，默认关闭。

`Fn` 通常不会作为普通 Fcitx key event 上报，因此一般不能直接作为 PTT 热键。

## 手动构建 module

```bash
cmake -S fcitx5/module -B fcitx5/module/build \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX="$HOME/.local"
cmake --build fcitx5/module/build -j"$(nproc)"
cmake --install fcitx5/module/build

mkdir -p ~/.local/share/fcitx5/addon
cp fcitx5/data/vocotype.conf ~/.local/share/fcitx5/addon/
rm -f ~/.local/share/fcitx5/inputmethod/vocotype.conf
fcitx5 -r
```

## Python backend

Backend socket：

```text
/tmp/vocotype-fcitx5.sock
```

健康检查：

```bash
echo '{"type":"ping"}' | nc -U /tmp/vocotype-fcitx5.sock
```

查看日志：

```bash
journalctl --user -u vocotype-fcitx5-backend.service -f
```

Backend 只处理语音和 SLM，不再处理普通键盘事件或 Rime session。

## 术语库与原生热词

Fcitx backend 与 IBus 共用 `~/.config/vocotype/terms.yaml`。默认 ASR 是官方
Contextual Paraformer ONNX，术语可以同时进入原生 hotword 编码器和转录后的
确定性标准化层。安装器只在没有新旧术语文件时创建模板。

完整格式见 [`docs/guides/terms.md`](../docs/guides/terms.md)。

## ITN 与数字格式

设置中心可整体开关数字/ITN，并分别控制 `2026/05/11`、`15:20`、`320m` 与 `¥128` 等紧凑书写风格。详见 [`docs/guides/itn.md`](../docs/guides/itn.md)。

## AI 润色与实时预览

SLM 默认关闭，在 `~/.config/vocotype/fcitx5-backend.json` 中配置。
`PolishByDefault=false` 时，`F9` 只做 ASR，`Shift+F9` 才润色；设为 `true` 后两者反转。

润色模式通过异步任务执行：module 先获得 `task_id`，随后每 100 ms 拉取
`status / heartbeat / delta / final / error` 事件。远程模型的可见增量会显示在输入面板，
同时保留 ASR 原文；thinking/reasoning 不会进入预览。

### 本地按需模型

```json
{
  "slm": {
    "enabled": true,
    "provider": "local_ephemeral",
    "model": "Qwen/Qwen3.5-0.8B",
    "local_model": "Qwen/Qwen3.5-0.8B",
    "warmup_timeout_ms": 90000,
    "ready_wait_ms": 2000,
    "keepalive_ms": 60000,
    "timeout_ms": 12000,
    "min_chars": 8,
    "max_tokens": 96,
    "enable_thinking": false
  }
}
```

本地 worker 暂时返回完整结果，但使用同一任务协议；录音时预热，完成后按既有保活策略释放。

### 远程 OpenAI-compatible SSE

```json
{
  "slm": {
    "enabled": true,
    "provider": "remote",
    "model": "gpt-4o-mini",
    "endpoint": "https://example.com/v1/chat/completions",
    "api_key": "sk-***",
    "remote_stream": true,
    "stream_idle_timeout_ms": 20000,
    "transport_timeout_ms": 0,
    "remote_max_tokens": 0,
    "min_chars": 8,
    "enable_thinking": false,
    "retry_without_proxy": true,
    "extra_headers": {},
    "extra_body": {}
  }
}
```

`remote_max_tokens=0` 表示不发送固定输出上限，避免长文本被旧的 128-token 默认值截断。
`stream_idle_timeout_ms` 从最后一次 SSE 事件开始计时。OpenRouter 会自动映射 reasoning 参数和
项目标识 header，用户显式配置的 `extra_headers` / `extra_body` 优先。

任务中按 `Escape` 可取消；开始普通键盘输入也会取消润色并把该按键继续交给当前输入法。
调用失败时不会丢失已识别文字：输入面板保留 ASR 原文，按 `1`、空格或回车提交，
按 `Escape` 放弃。

完整协议与参数见 [`docs/guides/slm-streaming.md`](../docs/guides/slm-streaming.md)。

## 行为边界

### 正在输入拼音时按 F9

默认 `BlockWhenComposing=true`。当当前输入法已有 preedit 或候选列表时，VoCoType
会消耗本次 PTT 热键但不开始录音，不会清空 Rime composition。

完成或取消当前组合后再按 F9 即可。可关闭该选项，但不建议这样做。

### 录音中切换窗口

VoCoType 在按下 PTT 时保存当前 `InputContext`。若焦点在录音中离开该输入框，录音会被取消；
若焦点在异步识别期间离开，识别结果会被丢弃，不会提交到新窗口。

### 没有文本输入框时

Module 的“全局”范围是 Fcitx 5 的输入上下文，不是桌面 compositor 级全局热键。
桌面、锁屏、游戏或没有活动文本输入框的场景不保证收到 F9。

## 故障排查

### Module 未加载

```bash
find /usr/lib /usr/lib64 -path '*/fcitx5/vocotype.so' -type f -print
ls /usr/share/fcitx5/addon/vocotype.conf
busctl --user --json=short call org.fcitx.Fcitx5 /controller \
  org.fcitx.Fcitx.Controller1 GetAddons | grep -o 'vocotype'
```

文件存在只表示 addon 被发现；当前 Fcitx 实例的 `GetAddons` 返回中包含 `vocotype`，才表示 module 已实际创建成功。设置中心的 Doctor 会执行同一运行态检查。

不要设置 `FCITX_ADDON_DIRS`。该变量会覆盖 Fcitx 的标准 addon 搜索路径，可能导致
D-Bus、Rime 和界面 addon 无法加载。若旧版本曾写入该变量，先清理并重启：

```bash
rm -f ~/.config/environment.d/fcitx5-vocotype.conf
env -u FCITX_ADDON_DIRS fcitx5 -r -d
```

### 按 F9 无响应

```bash
systemctl --user status vocotype-fcitx5-backend.service
journalctl --user -u vocotype-fcitx5-backend.service -b --no-pager | tail -n 200
```

同时检查当前输入法是否仍有未提交 preedit；默认情况下这会阻止录音启动。

### Rime 自身无法输入

VoCoType module 不处理 Rime 普通按键。请直接按 `fcitx5-rime` 的方式排查和配置。
停用 VoCoType module 后问题仍存在时，问题不在 VoCoType 的 Rime 兼容层，因为该兼容层已不存在。

## 卸载

图形设置中心提供 **卸载 VoCoType（Fcitx 5）**。源码安装时，它会停止用户服务、清理用户运行代码与 launcher，并通过 Polkit 删除源码安装器写入 `/usr` 的 module、addon 元数据和 ownership marker。命令行使用：

```bash
bash fcitx5/scripts/uninstall.sh
```

默认保留 `~/.local/share/vocotype-fcitx5/.venv`、共享 ModelScope 模型缓存和 `~/.config/vocotype/`。使用 `--purge-runtime` 删除 Fcitx 的虚拟环境与运行缓存；只有明确使用 `--remove-user-data` 时才会删除 IBus 与 Fcitx 共用的术语、hotword、音频和 AI 配置。使用 `--keep-system-integration` 才会显式保留源码安装器管理的系统 addon。

若 module 来自 DEB、RPM 或 Arch 包，卸载脚本不会直接删除 `/usr/lib*/fcitx5/vocotype.so`；请按设置中心显示的命令卸载 `vocotype-linux` 软件包。
