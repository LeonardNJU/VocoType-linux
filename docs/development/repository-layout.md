# 仓库目录规范

VoCoType 按职责划分目录。不要重新创建一个容纳所有用途的根级 `scripts/`。

```text
app/                 共享语音识别、标准化、术语和 AI 运行时
settings_center/     GTK 设置、安装、卸载、Doctor 与反馈 UI
ibus/                IBus 引擎、数据和该 integration 的生命周期入口
fcitx5/              Fcitx 5 module、legacy Python fallback 与 IPC 入口
installers/          两套 integration 共用的安装/卸载实现和运行时辅助
packaging/
  manifests/         原生包运行时源码清单
  tools/             Release、DEB、RPM、Arch 构建工具
  tests/             安装包安装/卸载 smoke
  arch|debian|rpm/   发行版配方
tools/
  diagnostics/       开发和排障工具
  benchmarks/        benchmark 与性能实验
tests/               自动化行为测试
docs/
  guides/            用户功能指南
  troubleshooting/  排障文档
  development/       维护者文档
```

## Integration 生命周期

IBus 与 Fcitx 5 必须保持相同入口：

```text
<integration>/scripts/install.sh          交互式 CLI 安装
<integration>/scripts/install-gui.sh      设置中心调用的非交互安装 worker
<integration>/scripts/uninstall.sh        交互式 CLI 卸载
<integration>/scripts/uninstall-gui.sh    设置中心调用的非交互卸载 worker
```

通用实现放在 `installers/`，integration 目录中的入口只负责确定框架和交互模式。不要把 IBus 专用脚本放回共享目录，也不要在 IBus/Fcitx 中复制完全相同的辅助函数。

## 文件所有权

- 用户级 installer 只管理 `$HOME` 下的运行时、launcher、component、module 和配置。
- DEB、RPM、Arch 安装到 `/usr` 的文件只由系统包管理器移除。
- GUI 可以通过 Polkit 管理旧版安装器留下的非托管系统文件，但不得绕过原生软件包所有权。

## 新文件放置规则

- 新的发行构建器：`packaging/tools/`
- 新的安装包 smoke：`packaging/tests/`
- 新的用户指南：`docs/guides/`
- 新的排障工具：`tools/diagnostics/`
- 新的行为测试：`tests/`

CI 会自动对上述职责目录中的所有 Shell 脚本执行 `bash -n`，并通过原生包构建与安装 smoke 验证目录清单。
