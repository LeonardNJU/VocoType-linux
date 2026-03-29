# VoCoType Linux 多平台打包指南

本文档说明如何构建 DEB、RPM 和 PKGBUILD 三种格式的安装包。

## 目录结构

```
packaging/
├── debian/          # DEB 包配置 (Debian/Ubuntu)
├── rpm/             # RPM 包配置 (Fedora/RHEL/SUSE)
├── arch/            # PKGBUILD (Arch Linux)
├── systemd/         # Systemd 服务文件
└── README.md        # 本文件
```

## 打包前的准备

确保源码目录结构正确：

```bash
# 项目根目录需要有这些文件
pyproject.toml
setup.py  # 如果没有，需要从 pyproject.toml 生成
vocotype_version.py
app/
ibus/
fcitx5/
scripts/
```

## 1. DEB 包构建 (Debian/Ubuntu)

### 环境准备

```bash
# Ubuntu/Debian
sudo apt-get update
sudo apt-get install -y \
    build-essential \
    debhelper \
    cmake \
    pkg-config \
    libfcitx5core-dev \
    nlohmann-json3-dev \
    python3-build \
    python3-venv \
    python3-pip \
    python3-wheel
```

### 构建步骤

```bash
# 1. 复制 debian 目录到项目根目录
cp -r packaging/debian .

# 2. 更新 changelog（使用当前日期）
debchange --create --package vocotype -v 2.1.3-1 "Initial release"

# 3. 构建源码包
dpkg-buildpackage -S -us -uc

# 4. 构建二进制包
dpkg-buildpackage -b -us -uc

# 或者在干净环境中构建（推荐）
pbuilder create  # 首次运行
cd ..
pdebuild
```

### 输出文件

```
../vocotype_2.1.3-1_amd64.deb
../vocotype_2.1.3-1.dsc
../vocotype_2.1.3-1.tar.xz
```

## 2. RPM 包构建 (Fedora/RHEL/SUSE)

### 环境准备

```bash
# Fedora
sudo dnf install -y \
    rpm-build \
    rpmdevtools \
    cmake \
    gcc-c++ \
    fcitx5-devel \
    nlohmann-json-devel \
    python3-devel \
    python3-pip \
    python3-wheel \
    portaudio-devel

# 设置 rpmbuild 目录
rpmdev-setuptree
```

### 构建步骤

```bash
# 1. 复制 spec 文件
cp packaging/rpm/vocotype.spec ~/rpmbuild/SPECS/

# 2. 准备源码 tarball
cd ..
tar czf ~/rpmbuild/SOURCES/vocotype-2.1.3.tar.gz \
    --exclude=.git \
    --exclude=__pycache__ \
    --exclude=*.pyc \
    VocoType-linux/

# 3. 构建 RPM
cd ~/rpmbuild
rpmbuild -ba SPECS/vocotype.spec

# 或者使用 mock（干净环境）
mock -r fedora-40-x86_64 ~/rpmbuild/SRPMS/vocotype-2.1.3-1.src.rpm
```

### 输出文件

```
~/rpmbuild/RPMS/x86_64/vocotype-2.1.3-1.fc40.x86_64.rpm
~/rpmbuild/SRPMS/vocotype-2.1.3-1.fc40.src.rpm
```

## 3. PKGBUILD 构建 (Arch Linux)

### 环境准备

```bash
# Arch Linux
sudo pacman -S --needed \
    base-devel \
    cmake \
    python-build \
    python-installer \
    python-wheel \
    fcitx5 \
    nlohmann-json
```

### 构建步骤

```bash
# 1. 创建构建目录
mkdir -p ~/builds/vocotype
cd ~/builds/vocotype

# 2. 复制 PKGBUILD
cp /path/to/packaging/arch/PKGBUILD .

# 3. 生成 checksums
updpkgsums

# 4. 构建
makepkg -s

# 5. 验证
namcap vocotype-2.1.3-1-x86_64.pkg.tar.zst
```

### 安装

```bash
sudo pacman -U vocotype-2.1.3-1-x86_64.pkg.tar.zst
```

## 自动化构建脚本

项目根目录提供 `scripts/build-packages.sh`：

```bash
#!/bin/bash
# 构建所有格式的包

VERSION=$(python3 -c "import vocotype_version; print(__version__)")
BUILD_DIR="build/packages"

mkdir -p "$BUILD_DIR"

echo "=== Building DEB package ==="
./scripts/build-deb.sh
mv ../vocotype_${VERSION}*.deb "$BUILD_DIR/"

echo "=== Building RPM package ==="
./scripts/build-rpm.sh
mv ~/rpmbuild/RPMS/x86_64/vocotype-${VERSION}*.rpm "$BUILD_DIR/"

echo "=== Building Arch package ==="
./scripts/build-arch.sh
mv /tmp/vocotype-build/vocotype-${VERSION}*.pkg.tar.zst "$BUILD_DIR/"

echo "=== Packages built ==="
ls -la "$BUILD_DIR/"
```

## CI/CD 集成

### GitHub Actions

```yaml
name: Build Packages

on:
  push:
    tags:
      - 'v*'

jobs:
  build-deb:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install dependencies
        run: |
          sudo apt-get update
          sudo apt-get install -y build-essential debhelper cmake ...

      - name: Build DEB
        run: |
          cp -r packaging/debian .
          dpkg-buildpackage -b -us -uc

      - name: Upload artifact
        uses: actions/upload-artifact@v4
        with:
          name: deb-package
          path: ../vocotype_*.deb

  build-rpm:
    runs-on: ubuntu-latest
    container: fedora:latest
    steps:
      - uses: actions/checkout@v4

      - name: Install dependencies
        run: |
          dnf install -y rpm-build rpmdevtools cmake ...
          rpmdev-setuptree

      - name: Build RPM
        run: |
          cp packaging/rpm/vocotype.spec ~/rpmbuild/SPECS/
          tar czf ~/rpmbuild/SOURCES/vocotype-*.tar.gz .
          rpmbuild -ba ~/rpmbuild/SPECS/vocotype.spec

      - name: Upload artifact
        uses: actions/upload-artifact@v4
        with:
          name: rpm-package
          path: ~/rpmbuild/RPMS/**/*.rpm

  release:
    needs: [build-deb, build-rpm]
    runs-on: ubuntu-latest
    steps:
      - name: Download all artifacts
        uses: actions/download-artifact@v4

      - name: Create Release
        uses: softprops/action-gh-release@v1
        with:
          files: |
            deb-package/*.deb
            rpm-package/*.rpm
```

## 安装包内容说明

### 安装路径

| 组件 | 路径 | 说明 |
|------|------|------|
| Python 包 | `/usr/lib/python3.x/site-packages/` | app, ibus, fcitx5 |
| 可执行文件 | `/usr/bin/` | vocotype-ibus, vocotype-fcitx5-addon |
| Fcitx5 addon | `/usr/lib/fcitx5/vocotype.so` | C++ 插件 |
| Fcitx5 配置 | `/usr/share/fcitx5/` | addon 和 inputmethod 配置 |
| IBus 配置 | `/usr/share/ibus/component/` | vocotype.xml |
| 应用数据 | `/usr/share/vocotype/` | 共享资源 |
| Systemd | `/usr/lib/systemd/user/` | 后端服务 |

### 依赖关系

- **Python**: 3.11-3.12（onnxruntime 不支持 3.13+）
- **系统库**: portaudio, libffi, gobject-introspection
- **输入法框架**: IBus 或 Fcitx5（可同时安装）
- **可选**: librime（拼音支持）

## 注意事项

1. **Python 版本**: 必须严格限制 3.11-3.12，onnxruntime 不支持更高版本
2. **C++ Addon**: Fcitx5 版本需要编译 C++ 代码，依赖 fcitx5 开发库
3. **模型下载**: 首次安装后需要运行 `vocotype-download-models` 下载语音识别模型
4. **权限**: 输入法需要访问音频设备，用户需在 `audio` 组

## 故障排除

### DEB 构建失败

```bash
# 检查依赖是否安装
sudo apt-get build-dep .

# 清理重新构建
dpkg-buildpackage -tc
```

### RPM 构建失败

```bash
# 安装所有构建依赖
sudo dnf builddep packaging/rpm/vocotype.spec

# 检查缺少的文件
rpmlint ~/rpmbuild/RPMS/x86_64/vocotype-*.rpm
```

### Arch 构建失败

```bash
# 更新 checksums
updpkgsums

# 清理并重新构建
makepkg -C
```
