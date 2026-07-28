# Linux安装与首次配置

> macOS用户请阅读 [macOS安装、升级与Gatekeeper](macos.md)。本页只描述 Linux发行包、Nix和源码安装。

推荐流程是：**只下载一种发行包 → 安装 → 打开 VoCoType 设置 → 完成输入法、模型和麦克风测试**。正常桌面用户不需要手工编辑 JSON，也不需要在终端中逐项配置依赖。

## 选择一种 flavor

| 你的环境 | 推荐包 | 文件名前缀 |
|---|---|---|
| KDE、正在使用 Fcitx 5 / Rime / Mozc | Fcitx 5 专用版 | `vocotype-linux-fcitx5` |
| GNOME、正在使用 IBus | IBus 专用版 | `vocotype-linux-ibus` |
| 确实需要同时安装两套 integration | Universal | `vocotype-linux` |

三种 flavor 互相冲突，**只安装其中一种**。不要在同时存放多个 VoCoType 包的目录里使用过宽的 `vocotype-linux-*` 通配符。

从 [GitHub Releases](https://github.com/LeonardNJU/VocoType-linux/releases) 下载与你的发行版和输入法框架匹配的软件包。

### Debian / Ubuntu

Fcitx 5：

```bash
sudo apt install ./vocotype-linux-fcitx5_*.deb
```

IBus：

```bash
sudo apt install ./vocotype-linux-ibus_*.deb
```

Universal：

```bash
sudo apt install ./vocotype-linux_*.deb
```

### Fedora / RHEL 系

Fcitx 5：

```bash
sudo dnf install ./vocotype-linux-fcitx5-*.rpm
```

IBus：

```bash
sudo dnf install ./vocotype-linux-ibus-*.rpm
```

Universal：

```bash
sudo dnf install ./vocotype-linux-[0-9]*.rpm
```

### Arch Linux

Fcitx 5：

```bash
sudo pacman -U ./vocotype-linux-fcitx5-*.pkg.tar.zst
```

IBus：

```bash
sudo pacman -U ./vocotype-linux-ibus-*.pkg.tar.zst
```

Universal：

```bash
sudo pacman -U ./vocotype-linux-[0-9]*.pkg.tar.zst
```

发行包包含 native C++ core、最终与实时 FunASR worker、原生录音器、模型管理器和 GTK 设置中心。专用版只安装所选输入法 integration 及其系统依赖；软件包不包含 Python runtime、venv 或 wheelhouse。AI 功能只调用用户配置的 OpenAI-compatible API，不在本机启动模型。

## 在图形界面中完成初始化

1. 从应用菜单打开 **VoCoType 设置**；
2. 在“概览与安装”中选择 **Fcitx 5** 或 **IBus**；
3. 点击安装 / 修复；
4. 点击“校验并下载模型”，等待原生模型管理器完成 SHA-256 校验；
5. 打开 **Playground**，选择麦克风并完成录音和 ASR 测试；
6. 在“通用设置 → 语音快捷键”确认或录制三个动作。默认普通识别为 `F9`，但可以改成 `Alt_R`、功能键或其他通过校验的组合。

需要系统权限时，桌面会显示标准 Polkit 授权窗口。VoCoType 不读取或保存管理员密码。

## 从源码构建并安装

没有适用发行包，或者需要测试最新代码时：

```bash
git clone https://github.com/LeonardNJU/VocoType-linux.git
cd VocoType-linux
bash scripts/install/fcitx5/install.sh --install-system-deps --download-models
# 或：bash scripts/install/ibus/install.sh --install-system-deps --download-models
```

源码安装会编译 C++ 组件。完成后可运行 `vocotype-settings`；安装后的日常运行不需要编译器或 Python。

## Nix / NixOS

仓库提供锁定的 source-built flake，支持 universal、IBus-only 与 Fcitx5-only。安装命令和 NixOS input-method 配置见 [Nix 与 NixOS](nix.md)。

## 无图形桌面或兼容 CLI

图形界面不可用时，仍可调用安装脚本：

| 集成 | 安装 | 卸载 |
|---|---|---|
| IBus | `bash scripts/install/ibus/install.sh` | `bash scripts/install/ibus/uninstall.sh` |
| Fcitx 5 | `bash scripts/install/fcitx5/install.sh` | `bash scripts/install/fcitx5/uninstall.sh` |

CLI 是兼容入口，不再是普通桌面用户的首选安装方式。

## 安装后没有反应

打开 **VoCoType 设置 → 诊断** 运行 Doctor。它会检查运行时、模型、输入法集成、服务、IPC、麦克风和配置文件。仍无法解决时，生成脱敏支持包并提交 [GitHub Issue](https://github.com/LeonardNJU/VocoType-linux/issues)。
