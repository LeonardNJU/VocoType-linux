#!/bin/bash
# 构建 RPM 包

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

echo "=== Building RPM package for VoCoType ==="

# 检查环境
if ! command -v rpmbuild &> /dev/null; then
    echo "Installing rpm-build tools..."
    if command -v dnf &> /dev/null; then
        sudo dnf install -y rpm-build rpmdevtools
    elif command -v yum &> /dev/null; then
        sudo yum install -y rpm-build rpmdevtools
    else
        echo "Cannot find package manager. Please install rpm-build manually."
        exit 1
    fi
fi

# 设置 rpmbuild 目录
if [ ! -d ~/rpmbuild ]; then
    rpmdev-setuptree
fi

VERSION=$(python3 -c "import vocotype_version; print(__version__)")

# 安装构建依赖
echo "Installing build dependencies..."
if command -v dnf &> /dev/null; then
    sudo dnf builddep -y packaging/rpm/vocotype.spec 2>/dev/null || {
        echo "Installing dependencies manually..."
        sudo dnf install -y \
            cmake gcc-c++ fcitx5-devel nlohmann-json-devel \
            python3-devel python3-pip python3-wheel python3-build \
            portaudio-devel gobject-introspection-devel
    }
fi

# 准备源码 tarball
echo "Preparing source tarball..."
cd ..
tar czf ~/rpmbuild/SOURCES/vocotype-${VERSION}.tar.gz \
    --exclude=.git \
    --exclude=__pycache__ \
    --exclude="*.pyc" \
    --exclude=build \
    --exclude=dist \
    --transform "s|^VocoType-linux|vocotype-${VERSION}|" \
    VocoType-linux/

cd "$PROJECT_DIR"

# 复制 spec 文件
cp packaging/rpm/vocotype.spec ~/rpmbuild/SPECS/

# 构建
echo "Building RPM..."
cd ~/rpmbuild
rpmbuild -ba SPECS/vocotype.spec

echo "=== Build complete ==="
echo "RPM packages:"
ls -la ~/rpmbuild/RPMS/*/vocotype-${VERSION}*.rpm
ls -la ~/rpmbuild/SRPMS/vocotype-${VERSION}*.src.rpm 2>/dev/null || true
