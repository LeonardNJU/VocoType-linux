# IBus Rime 配置

> Fcitx 5 版本是全局 Module，不内嵌 Rime。Fcitx 用户继续使用自己的 `fcitx5-rime`、Mozc 或其他输入法；VoCoType 只处理 F9 系列快捷键。

## IBus 的实现方式

VoCoType IBus 通过项目内 `ibus/rime_runtime.py` 使用 Python 标准库 `ctypes` 直接调用系统 `librime`。没有额外 Python binding，也没有本地编译步骤。

支持两类发行版 ABI：

- Ubuntu 22.04 librime 1.7：传统 `RimeSetup`、`RimeProcessKey` 等直接符号；
- 当前 Fedora/Arch：`rime_get_api()` 函数表。

## 独立用户目录

VoCoType 不再复用或修改 `~/.config/ibus/rime`，而是使用：

```text
~/.config/vocotype/rime/
├── default.custom.yaml
├── user.yaml
└── build/
```

安装器生成最小配置：

```yaml
patch:
  schema_list:
    - schema: luna_pinyin
```

然后运行：

```bash
rime_deployer --build \
  ~/.config/vocotype/rime \
  /usr/share/rime-data \
  ~/.config/vocotype/rime/build
```

这样只部署所选 schema，不要求安装默认配置中列出的所有无关方案。

## 发行版依赖

```bash
# Ubuntu / Debian
sudo apt install librime1 librime-bin librime-data rime-data-luna-pinyin

# Fedora
sudo dnf install librime librime-tools brise

# Arch
sudo pacman -S --needed librime librime-data
```

其中部署工具分别来自：

- Debian/Ubuntu：`librime-bin`
- Fedora：`librime-tools`
- Arch：`librime`

Fedora 的官方 schema 仓库包名是 `brise`。

## 修改 schema

推荐在设置中心填写 schema ID 并执行“安装 / 修复”。安装器会重新生成 `default.custom.yaml`、部署数据并执行真实按键测试。

手工修改后也必须重新运行 `rime_deployer --build`。仅编辑 `user.yaml` 不会生成所需的 prism、table 和 schema build 文件。

## 验证

```bash
python tools/diagnostics/debug-rime.py
```

成功结果应包含：

```text
preedit: 'n'
候选: 你, 那, 呢, 能, 年
```

原生包 CI 在 Ubuntu、Fedora 和 Arch 上执行同样的端到端检查。
