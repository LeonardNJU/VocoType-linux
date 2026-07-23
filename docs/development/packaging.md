# 打包与分发

VoCoType Linux 发布源码归档，以及面向 Debian/Ubuntu、Fedora/RHEL 和 Arch 的原生软件包。发布流程不生成 wheel、sdist 或私有虚拟环境。

## 发布原则

- `universal`、`ibus`、`fcitx5` 三种 flavor 共用同一 native staging 实现；
- 每个软件包只包含 ELF、共享库、输入法元数据、桌面资源和 shell lifecycle；
- 软件包事务不下载模型、不修改用户配置、不调用编译器；
- 用户首次初始化只校验/下载模型并注册所选输入法集成；
- 三个平台消费同一个经过审计的 FunASR/ONNX portable bundle；
- 最终 Release 只发布九个安装包与统一 `SHA256SUMS`；源码归档单独验证；
- 软件包内禁止 `.py`、`.pyc`、`.whl` 和 Python 依赖。

## 工具

`packaging/scripts/` 中的版本映射、flavor 元数据、模板渲染、源码归档、资产收集和审计全部由 shell/CMake 实现。

```bash
make test
make release
make package-deb
make package-rpm
make package-arch
```

## CI

`.github/workflows/ci.yml` 运行 CTest、native architecture contracts、Fcitx module build、feedback service tests、静态文档构建和三平台 package smoke tests。`.github/workflows/release.yml` 复用相同 native bundle，构建并验证最终安装资产。

## 目录职责

```text
packaging/
├── arch/ debian/ rpm/  # 发行版模板
├── nix/                # flake 调用的 Nix 实现
├── common/             # 各平台共享的运行时 wrapper 与 systemd unit
├── scripts/            # 构建、staging、版本与资产工具
└── tests/              # 安装包审计和安装/卸载 smoke tests
```

`flake.nix` 与 `flake.lock` 留在仓库根目录，作为 `nix build .`、`nix run .` 和 GitHub flake URL 的标准入口；具体 derivation 与其他发行版实现同处 `packaging/nix/`。

Universal 包安装 `vocotype-core`、两个 FunASR worker、录音器、模型管理器、设置中心、IBus engine 与 Fcitx Module。模型属于用户缓存，不在软件包事务中下载。
