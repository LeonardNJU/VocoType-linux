# VoCoType Linux

<h2 align="center">Linux 全平台离线语音输入法</h2>

**VoCoType Linux** 是基于 [VoCoType](https://github.com/233stone/vocotype-cli) 核心引擎开发的 **Linux 离线语音输入法**，同时支持 IBus 和 Fcitx 5 两大输入法框架。

> **Windows / macOS 用户**：VoCoType 原作者已实现桌面版，请访问 [vocotype.com](https://vocotype.com/)

---

## 核心特性

- **100% 离线，隐私无忧** - 所有语音识别在本地完成，不上传任何数据
- **旗舰级识别引擎** - 基于 FunASR Paraformer 模型，中英混合输入精准
- **PTT 按键说话** - 按住 F9 说话，松开自动识别并输入；`Shift+F9` 支持长句润色模式
- **语音编辑（IBus）** - `Ctrl+F9` 进入编辑指令模式，可改写/替换/插入/删除/导航/撤销重做
- **轻量化设计** - 仅需 700MB 内存，纯 CPU 推理，无需显卡
- **0.1 秒级响应** - 感受所言即所得的畅快体验
- **可选 Rime 集成** - 需要拼音时可启用 Rime，无需切换输入法

## Demo
https://github.com/user-attachments/assets/94772920-0f9e-4dff-8da5-c9026eb23256
### 新功能! 语音编辑(v2.2.1)
[Kooha-2026-03-29-08-50-00.webm](https://github.com/user-attachments/assets/4b936014-9477-4794-8d04-aa31d34577a0)


## 支持平台

| 输入法框架 | 状态 | 说明 |
|-----------|------|------|
| **IBus** | ✅ 完整支持 | 适用于 GNOME、大多数发行版默认 |
| **Fcitx 5** | ✅ 完整支持 | 适用于 KDE、偏好 Fcitx 的用户 |

两个版本**可以同时安装**，共享 VoCoType 核心引擎，各自独立运行。

---

## 快速开始

### IBus 版本

```bash
git clone https://github.com/LeonardNJU/VocoType-linux.git
cd vocotype-cli
./scripts/install-ibus.sh
ibus restart
```

安装脚本会询问是否启用 `Shift+F9` 长句 SLM 润色：
- 不启用（默认）：不安装/拉取 SLM 模型，`Shift+F9` 不会触发润色
- 启用：可选择
  - 本地模型（`local_ephemeral`）：按下预热，润色后释放
  - 远程 API（`remote`）：交互配置 `model`、`endpoint`、`api_key`

详细安装说明：[ibus/README.md](ibus/README.md)

### Fcitx 5 版本

```bash
git clone https://github.com/LeonardNJU/VocoType-linux.git
cd vocotype-cli
bash fcitx5/scripts/install-fcitx5.sh
fcitx5 -r
```

安装脚本会询问是否启用 `Shift+F9` 长句 SLM 润色：
- 不启用（默认）：不安装/拉取 SLM 模型，`Shift+F9` 不会触发润色
- 启用：可选择
  - 本地模型（`local_ephemeral`）：按下预热，润色后释放
  - 远程 API（`remote`）：交互配置 `model`、`endpoint`、`api_key`

安装脚本还会询问 Python 环境：项目虚拟环境、用户级虚拟环境、系统 Python 或手动指定解释器。若安装后希望删除当前仓库，请选择用户级虚拟环境或系统 Python。

详细安装说明：[fcitx5/README.md](fcitx5/README.md)

---

## SLM 后处理配置（通用）

`F9` 为极速模式（不走 SLM），`Shift+F9` 为长句模式（可选 SLM/LLM 润色）。

### 本地模型（推荐，按需加载）

```json
{
  "slm": {
    "enabled": true,
    "provider": "local_ephemeral",
    "model": "Qwen/Qwen3.5-0.8B",
    "local_model": "Qwen/Qwen3.5-0.8B",
    "timeout_ms": 12000,
    "warmup_timeout_ms": 90000,
    "ready_wait_ms": 2000,
    "keepalive_ms": 60000,
    "min_chars": 8,
    "max_tokens": 96,
    "edit_enabled": true,
    "edit_max_tokens": 256,
    "enable_thinking": false
  }
}
```

### 远程 API（OpenAI 兼容）

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
    "edit_enabled": true,
    "edit_max_tokens": 256,
    "retry_without_proxy": true
  }
}
```

关键参数说明：
- `provider`：`local_ephemeral` / `remote`
- `min_chars`：长句触发阈值（默认 `8`）
- `max_tokens`：润色输出预算
- `edit_enabled`：是否启用 `Ctrl+F9` 语音编辑（默认 `true`，仅 IBus）
- `edit_max_tokens`：语音编辑模式下的输出预算（默认 `256`）
- `enable_thinking`：是否允许思考输出（默认关闭）
- `retry_without_proxy`：远程请求失败时尝试绕过代理直连重试

---

## IBus 语音编辑（Ctrl+F9）

> 说明：该功能目前由 IBus 引擎提供，Fcitx5 暂未接入同等编辑链路。

### 快捷键

- `Ctrl+F9`：语音编辑模式（先读取 surrounding text，再录音识别编辑指令）
- `Ctrl+Shift+F9`：surrounding 探针（回填 `[VT-SURR ...]` 调试信息）

### 常用语音编辑能力

- 文本修改：`把 A 改成 B`、`删除当前句`、`删除上一句`、`删除选中内容`
- 插入生成：`输入一段对海底捞商家的好评`、`输入一段关于天气的描写`
- 选择与导航：`全选`、`移动到开头`、`移动到结尾`、`左移三次`、`下一个词`
- 历史操作：`撤销/撤销修改`、`重做`
- 诊断命令：`显示上下文信息`（输出当前 `cap/del/cursor/anchor/prev/cur/sel/all`）

### 行为说明

- 若当前输入框不支持 surrounding 能力（`cap=0`），`Ctrl+F9` 会直接提示并停止。
- 若录音期间输入框内容已变化，会提示 `输入框内容已变化，请重试`，避免误改错位文本。
- 撤销策略采用“智能分流”：
  - 最近一次是语音编辑且状态匹配：走内部撤销栈
  - 否则：下发应用级撤销/重做（`Ctrl+Z` / `Ctrl+Shift+Z`）

---

## 重新安装与卸载

### 重新安装

安装脚本支持重复运行，无论是：
- 安装失败需要重试
- 升级到新版本
- 变更安装参数

直接重新运行安装脚本即可，会自动覆盖之前的安装，不会有残留。

### 卸载

**IBus 版本**：
```bash
./scripts/uninstall-ibus.sh
```

卸载时可选择：
- **快速卸载**（选项 1）：保留 .venv 和模型文件，方便下次安装
- **完全卸载**（选项 2）：删除所有内容

---

## 架构设计

```
VoCoType Linux
├── app/                    # 核心引擎（共享）
│   ├── funasr_server.py    # 语音识别（FunASR）
│   └── ...
├── ibus/                   # IBus 版本
│   ├── engine.py           # IBus 引擎
│   └── README.md
└── fcitx5/                 # Fcitx 5 版本
    ├── addon/              # C++ Addon
    ├── backend/            # Python 后端
    └── README.md
```

IBus 和 Fcitx 5 是**并列独立**的实现，共享 VoCoType 核心（语音识别、音频采集）。

---

## 版本对比

| 特性 | IBus 版本 | Fcitx 5 版本 |
|-----|----------|-------------|
| 输入法框架 | IBus | Fcitx 5 |
| 实现语言 | 纯 Python | C++ + Python (IPC) |
| 安装位置 | `~/.local/share/vocotype/` | `~/.local/share/vocotype-fcitx5/` |
| 适用桌面 | GNOME 等 | KDE 等 |

---

## 使用场景

### 日常应用
- 聊天通讯：微信、QQ、Telegram、Slack、Discord
- 文档撰写：文章、报告、邮件、日记、笔记
- 网页浏览：搜索、表单、评论

### 开发场景
- 编写代码注释和文档
- Git Commit Message
- 与 AI 工具对话（ChatGPT、Claude、Cursor）
- Issue & PR 描述

---

## 核心优势

| 特性 | VoCoType Linux | 云端输入法 |
|------|---------------|-----------|
| **隐私安全** | 本地离线，绝不上传 | 数据上传云端 |
| **网络依赖** | 完全无需联网 | 必须联网 |
| **响应速度** | 0.1 秒级 | 受网速影响 |
| **数据安全** | 100% 本地 | 存在泄密风险 |

---

## 系统要求

- **操作系统**: Linux (Fedora, Ubuntu, Debian, Arch 等)
- **Python**: 3.11-3.12（onnxruntime 暂不支持 3.13+）
- **内存**: 最低 4GB，推荐 8GB
- **CPU**: 双核以上，无需 GPU

### 资源占用

| 状态 | 内存 | CPU |
|------|------|-----|
| 待机 | 200-300MB | ~0% |
| 录音 | - | 5-10% |
| 识别 | ~700MB | 100-200%（0.1-0.5秒）|

### SLM 开销基准测试（ASR vs ASR+SLM）

新增脚本：`scripts/benchmark_slm_pipeline.py`，用于对比：
- `ASR-only`（对应 F9 快速模式）
- `ASR+SLM`（对应 Shift+F9 长句模式）

示例（以 `Qwen/Qwen3.5-0.8B` 为例）：

```bash
python scripts/benchmark_slm_pipeline.py ./samples \
  --pattern "*.wav" \
  --repeat 5 \
  --warmup 1 \
  --slm-model Qwen/Qwen3.5-0.8B \
  --slm-endpoint http://127.0.0.1:18080/v1/chat/completions \
  --output-json /tmp/vocotype-benchmark.json
```

可选参数：
- `--slm-pid <PID>`：统计 SLM 服务进程的 CPU/RSS 增量
- `--disable-slm`：只测 ASR 基线，不测对照组

---

## 文档

- [IBus 版本安装指南](ibus/README.md)
- [Fcitx 5 版本安装指南](fcitx5/README.md)
- [Rime 拼音配置指南](RIME_CONFIG_GUIDE.md)（可选功能）

---

## 作者

**Leonard Li** - 开发与维护

📧 联系邮箱: [leo@lsamc.website](mailto:leo@lsamc.website)

## 联系我们

- **Bug 与建议**：请使用 GitHub Issues
- **原项目**：[VoCoType](https://github.com/233stone/vocotype-cli)

---

## 致谢

本项目基于以下优秀的开源项目：

- **[VoCoType](https://github.com/233stone/vocotype-cli)** - 原始项目，提供了强大的离线语音识别核心引擎
- **[FunASR](https://github.com/modelscope/FunASR)** - 阿里巴巴达摩院开源的语音识别框架
- **[QuQu](https://github.com/yan5xu/ququ)** - 优秀的开源项目，提供了重要的技术参考

---

## 第三方依赖与模型许可

本项目依赖的第三方库与模型均受各自许可证约束。详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

使用的模型：
- `iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-onnx`
- `iic/speech_fsmn_vad_zh-cn-16k-common-onnx`
- `iic/punc_ct-transformer_zh-cn-common-vocab272727-onnx`

## 📄 许可证

本项目继承原 VoCoType 项目的许可证。请查看 [LICENSE](LICENSE) 文件了解详情。

## Star History

<a href="https://www.star-history.com/?repos=LeonardNJU%VocoType-linux&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=LeonardNJU/VocoType-linux&type=date&theme=dark&legend=top-left&sealed_token=PxMzEEMfOA3R97IHVMG0zQqC1xVjuoA3cMbkfPzjtmhwAbL9n3oQVqHhhzNgKOwqwD0CShQpk_ostzzb2_m-8qA_U5IFphEK5su_nHoI1HKzcVq3-8oq8HZGL79TUmv5WtGBpBCqOtdrgrF8_KKxta_MCbS9IscnCEtMju92w8_qG8TtIZw_zZ68xYli" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=LeonardNJU/VocoType-linux&type=date&legend=top-left&sealed_token=PxMzEEMfOA3R97IHVMG0zQqC1xVjuoA3cMbkfPzjtmhwAbL9n3oQVqHhhzNgKOwqwD0CShQpk_ostzzb2_m-8qA_U5IFphEK5su_nHoI1HKzcVq3-8oq8HZGL79TUmv5WtGBpBCqOtdrgrF8_KKxta_MCbS9IscnCEtMju92w8_qG8TtIZw_zZ68xYli" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=LeonardNJU/VocoType-linux&type=date&legend=top-left&sealed_token=PxMzEEMfOA3R97IHVMG0zQqC1xVjuoA3cMbkfPzjtmhwAbL9n3oQVqHhhzNgKOwqwD0CShQpk_ostzzb2_m-8qA_U5IFphEK5su_nHoI1HKzcVq3-8oq8HZGL79TUmv5WtGBpBCqOtdrgrF8_KKxta_MCbS9IscnCEtMju92w8_qG8TtIZw_zZ68xYli" />
 </picture>
</a>
