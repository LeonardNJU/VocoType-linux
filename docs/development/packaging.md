# 打包与分发

VoCoType Linux 同时维护源码包、Python wheel / sdist，以及面向 Debian、Fedora 和 Arch 的原生软件包。

## 发布原则

- 原生包安装系统组件和桌面入口；
- 用户运行时、模型与个人配置由设置中心在首次启动后准备；
- 软件包安装过程不应联网下载模型，也不应交互式询问用户选项；
- DEB、RPM 和 Arch 包应共享同一套 staged system tree；
- GitHub Release 由标签触发并附带校验和。

## 本地验证

发布工具和平台构建命令集中在 `packaging/tools/`，安装后 smoke tests 位于 `packaging/tests/`。详细命令、目录契约和维护流程见仓库的 [打包技术文档](https://github.com/LeonardNJU/VocoType-linux/blob/master/packaging/README.md)。

## CI

`.github/workflows/ci.yml` 会运行 Python 测试、构建发行预览并验证 DEB、RPM 与 Arch 软件包；`.github/workflows/release.yml` 在版本标签上生成 Release assets。
