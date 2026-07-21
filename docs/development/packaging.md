# 打包与分发

VoCoType Linux 同时维护源码包、Python wheel / sdist，以及面向 Debian、Fedora 和 Arch 的原生软件包。

## 发布原则

- 原生包按 `universal`、`ibus`、`fcitx5` 三种 flavor 安装对应 integration、桌面入口、native streaming runtime 和锁定 Python 运行闭包；三种包共用同一 staging 代码并互相冲突，专用包不得夹带另一套 integration；
- 用户运行时由设置中心使用包内 wheels 创建；仅 Python 运行环境、模型与个人配置在首次启动后准备；
- 软件包安装过程不联网下载模型，也不交互式询问用户选项；用户初始化不得调用编译器或从 sdist 构建依赖；
- DEB、RPM 和 Arch 包应共享同一套 staged system tree；
- RC 标签自动生成公开 Pre-release；正式标签先生成 Draft，下载并测试最终资产后再原样发布；
- GitHub Release 附带全局资产清单和 SHA-256 校验和。

## 本地验证

发布工具和平台构建命令集中在 `packaging/tools/`，安装后 smoke tests 位于 `packaging/tests/`。详细命令、目录契约和维护流程见仓库的 [打包技术文档](https://github.com/LeonardNJU/VocoType-linux/blob/master/packaging/README.md)。

## CI

`.github/workflows/ci.yml` 会运行 Python 测试，构建一次 portable native bundle，并验证包含 native runtime 与 wheelhouse 的 DEB、RPM 和 Arch 软件包。`.github/workflows/release.yml` 在版本标签上执行相同完整流程；三个包消费同一份 native artifact，并分别构建与 Ubuntu、Fedora、Arch 系统库兼容的 Python wheelhouse。
