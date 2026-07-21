# 安装与首次配置

推荐流程是：**安装发行包 → 打开 VoCoType 设置 → 选择输入法框架 → 完成模型与麦克风测试**。正常桌面用户不需要手工编辑 JSON，也不需要在终端中逐项配置依赖。

## 安装发行包

从 [GitHub Releases](https://github.com/LeonardNJU/VocoType-linux/releases) 下载与你的发行版匹配的软件包。

=== "Debian / Ubuntu"

    ```bash
    sudo apt install ./vocotype-linux_*.deb
    ```

=== "Fedora / RHEL 系"

    ```bash
    sudo dnf install ./vocotype-linux-*.rpm
    ```

=== "Arch Linux"

    ```bash
    sudo pacman -U ./vocotype-linux-*.pkg.tar.zst
    ```

v3 Release 同时提供通用版、IBus 专用版和 Fcitx 5 专用版。三者都包含预编译 2-pass native runtime 和发行版兼容 Python wheelhouse；专用版只安装所选输入法 integration 及其系统依赖。包管理器事务不下载模型或修改用户配置，设置中心只创建 Python 3.12 用户环境并按需下载模型。AI 功能只调用用户配置的 OpenAI-compatible API，不在本机启动模型。

## 在图形界面中完成初始化

1. 从应用菜单打开 **VoCoType 设置**；
2. 在“概览与安装”中选择 **Fcitx 5** 或 **IBus**；
3. 点击安装 / 修复；
4. 等待 Python 运行时和语音模型准备完成；
5. 打开 **Playground**，选择麦克风并完成录音、回放和 ASR 测试；
6. 回到任意输入框，按住 `F9` 说话，松开后提交文字。

需要系统权限时，桌面会显示标准 Polkit 授权窗口。VoCoType 不读取或保存管理员密码。

## 从源码启动图形安装器

尚无原生软件包，或者需要测试最新代码时：

```bash
git clone https://github.com/LeonardNJU/VocoType-linux.git
cd VocoType-linux
bash installers/launch-settings.sh
```

后续步骤与发行包安装一致，均在 **VoCoType 设置** 中完成。

## 无图形桌面或兼容旧版 CLI

图形界面不可用时，仍可调用原有安装脚本：

| 集成 | 安装 | 卸载 |
|---|---|---|
| IBus | `bash ibus/scripts/install.sh` | `bash ibus/scripts/uninstall.sh` |
| Fcitx 5 | `bash fcitx5/scripts/install.sh` | `bash fcitx5/scripts/uninstall.sh` |

CLI 是兼容入口，不再是普通桌面用户的首选安装方式。

## 应该选择哪个输入法框架？

| 你的环境 | 推荐 |
|---|---|
| KDE、已经使用 Fcitx 5 / Rime / Mozc | [Fcitx 5](../integrations/fcitx5.md) |
| GNOME、发行版默认使用 IBus | [IBus](../integrations/ibus.md) |
| 希望使用当前稳定的语音编辑 | IBus |
| 确实需要在两套桌面环境间切换 | 可以同时安装；两者读取同一份 VoCoType 配置 |

## 安装后没有反应

打开 **VoCoType 设置 → 诊断** 运行 Doctor。它会检查运行时、模型、输入法集成、服务、IPC、麦克风和配置文件。仍无法解决时，生成脱敏支持包并提交 [GitHub Issue](https://github.com/LeonardNJU/VocoType-linux/issues)。
