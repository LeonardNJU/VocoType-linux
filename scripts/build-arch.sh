#!/bin/bash
# 构建 Arch Linux PKGBUILD

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

echo "=== Building Arch package for VoCoType ==="

# 检查环境
if ! command -v makepkg &> /dev/null; then
    echo "Error: makepkg not found. This script must run on Arch Linux."
    exit 1
fi

VERSION=$(python3 -c "import vocotype_version; print(__version__)")

# 创建构建目录
BUILD_DIR="/tmp/vocotype-build"
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"
cd "$BUILD_DIR"

# 复制 PKGBUILD
cp "$PROJECT_DIR/packaging/arch/PKGBUILD" .

# 更新版本
sed -i "s/^pkgver=.*/pkgver=${VERSION}/" PKGBUILD

# 生成 checksums
echo "Generating checksums..."
updpkgsums 2>/dev/null || {
    echo "Installing checksums manually..."
    # 创建源码 tarball 并计算 checksum
    cd "$PROJECT_DIR/.."
    tar czf "$BUILD_DIR/vocotype-${VERSION}.tar.gz" \
        --exclude=.git \
        --exclude=__pycache__ \
        --exclude="*.pyc" \
        --transform "s|^VocoType-linux|vocotype-${VERSION}|" \
        VocoType-linux/
    cd "$BUILD_DIR"
    SHA256=$(sha256sum "vocotype-${VERSION}.tar.gz" | cut -d' ' -f1)
    sed -i "s/sha256sums=('SKIP')/sha256sums=('${SHA256}')/" PKGBUILD
}

# 构建
echo "Building package..."
makepkg -s --noconfirm

echo "=== Build complete ==="
echo "Package location:"
ls -la "$BUILD_DIR"/vocotype-${VERSION}*.pkg.tar.zst
