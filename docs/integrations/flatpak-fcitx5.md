# Flatpak Fcitx5 实验接入

Fcitx5 官方 Flatpak org.fcitx.Fcitx5 支持 org.fcitx.Fcitx5.Addon.* 扩展。
VoCoType 现在提供初步的扩展构建方案，不需要更换已有的 Flatpak Fcitx5。
**还没有在真实 KDE neon + Flatpak Fcitx5 中完成端到端验证。**

## 结构

- Flatpak 内安装 /app/addons/VocoType/lib/fcitx5/vocotype.so，处理 F9 等快捷键，并通过桥接打开宿主设置中心。
- 宿主机运行录音器和 Core（FunASR），不在 Fcitx 沙盒中加载 AI 模型。
- Core 使用独立的 $XDG_RUNTIME_DIR/vocotype-flatpak/core.sock，避免影响原有宿主 Fcitx/IBus。
- 插件使用 flatpak-spawn --host 调用宿主的固定录音器命令以及用户级 Core 服务。

## 试用步骤

需要安装 Flatpak、flatpak-builder 和 Fcitx5 对应的 KDE SDK。上游当前使用 org.kde.Sdk//6.11；若上游更改 SDK，需调整 packaging/flatpak 中的 manifest。

1. 在宿主机安装原生 Core、录音器和模型，不安装宿主 Fcitx addon：

~~~bash
bash scripts/install/common/install-native-user.sh --framework flatpak-fcitx5 --download-models
systemctl --user status vocotype-flatpak-backend.service
~~~

2. 构建并安装 Flatpak 扩展：

~~~bash
flatpak install flathub org.fcitx.Fcitx5
flatpak install flathub org.kde.Sdk//6.11
bash packaging/flatpak/build-extension.sh --install
~~~

扩展名为 org.fcitx.Fcitx5.Addon.VocoType，构建使用本地源码。

3. 只有在**理解权限影响并确认信任整个 Fcitx5 Flatpak 及其他 addon**之后，显式授予宿主机执行权限：

~~~bash
bash packaging/flatpak/enable-host-bridge.sh --accept-host-spawn
~~~

脚本只挂载 xdg-run/vocotype-flatpak 运行时目录，不需要完整的 home、/tmp、宿主根目录或 system bus 权限。
**但**授予 org.freedesktop.Flatpak D-Bus 权限可以让 Flatpak 内任意代码执行宿主机命令（不限于 VoCoType 的两个桥接脚本），风险较高，不能视为细粒度授权。

4. **完全退出并重新启动** Flatpak Fcitx5，然后在输入框中测试 F9。
仅执行 Fcitx5 的普通重载可能无法重新挂载刚安装的扩展。

## 诊断

~~~bash
flatpak info org.fcitx.Fcitx5
flatpak --user info org.fcitx.Fcitx5.Addon.VocoType
flatpak info --show-permissions org.fcitx.Fcitx5
systemctl --user status vocotype-flatpak-backend.service
journalctl --user -u vocotype-flatpak-backend.service -b --no-pager
flatpak run --command=sh org.fcitx.Fcitx5 -c 'ls /app/addons/VocoType/lib/fcitx5; printenv VOCOTYPE_FCITX5_SOCKET'
~~~

## 移除

~~~bash
flatpak --user uninstall org.fcitx.Fcitx5.Addon.VocoType
bash scripts/install/common/uninstall-native-user.sh --framework flatpak-fcitx5
~~~

手动使用 flatpak override --user --show org.fcitx.Fcitx5 检查并逐项撤销附加权限。
不要直接执行 flatpak override --user --reset，因为那会移除该应用的全部用户覆盖设置。

## 尚未验证的部分

- 真实 Flatpak Fcitx5 插件加载、麦克风录制、KDE Wayland 热键及中文上屏。
- 宿主机录音器和 Flatpak 插件之间的文件路径/Socket 生命周期、系统重启和休眠恢复。
- 从 Flatpak 附加组件菜单启动宿主设置中心的实际体验；如果桥接失败，可在宿主机运行 ~/.local/bin/vocotype-settings。
- 上游 Fcitx5 扩展分支或 SDK 版本变化后的兼容性。

这是**实验性实现**，不是已发布的正式 Flatpak 安装包。
