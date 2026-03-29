#!/bin/bash
# VoCoType 构建脚本 - 统一的打包入口
# 所有构建产物将输出到 build/output/ 目录

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BUILD_DIR="$PROJECT_ROOT/build"
OUTPUT_DIR="$BUILD_DIR/output"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[1;34m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

show_help() {
    cat << EOF
VoCoType 构建脚本

用法: $0 [命令]

命令:
    deb         构建 Debian 包 (默认)
    clean       清理构建产物
    test        运行测试
    help        显示帮助

构建产物位置:
    build/output/       - 最终的 .deb 包
    build/intermediate/ - 构建中间文件
EOF
}

clean_build() {
    log_info "清理构建产物..."
    rm -rf "$BUILD_DIR"/*
    rm -rf "$PROJECT_ROOT/debian/vocotype" "$PROJECT_ROOT/debian/tmp" "$PROJECT_ROOT/debian/.debhelper"
    rm -f "$PROJECT_ROOT/debian/files" "$PROJECT_ROOT/debian/debhelper-build-stamp" "$PROJECT_ROOT/debian/vocotype.substvars"
    rm -f "$PROJECT_ROOT/"../*.deb "$PROJECT_ROOT/"../*.changes "$PROJECT_ROOT/"../*.buildinfo
    rm -rf "$PROJECT_ROOT/dist" "$PROJECT_ROOT"/*.egg-info
    find "$PROJECT_ROOT" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    log_success "清理完成"
}

build_deb() {
    log_info "开始构建 Debian 包..."
    mkdir -p "$OUTPUT_DIR" "$BUILD_DIR/intermediate"

    if [ ! -f "$PROJECT_ROOT/debian/control" ]; then
        log_error "debian/control 不存在"
        exit 1
    fi

    cd "$PROJECT_ROOT"
    dpkg-buildpackage -us -uc -b 2>&1 | tee "$BUILD_DIR/build.log" | tail -30

    log_info "整理构建产物到 build/output/..."
    mv "$PROJECT_ROOT/"../*.deb "$OUTPUT_DIR/" 2>/dev/null || true
    mv "$PROJECT_ROOT/"../*.changes "$OUTPUT_DIR/" 2>/dev/null || true
    mv "$PROJECT_ROOT/"../*.buildinfo "$OUTPUT_DIR/" 2>/dev/null || true

    log_success "构建完成!"
    ls -lh "$OUTPUT_DIR/"
}

run_tests() {
    log_info "运行测试..."
    cd "$PROJECT_ROOT"
    [ -f "tests/test_install.py" ] && python3 tests/test_install.py
    [ -f "tests/test_postinst.sh" ] && bash tests/test_postinst.sh
    log_success "测试完成"
}

case "${1:-deb}" in
    deb|build) build_deb ;;
    clean) clean_build ;;
    test) run_tests ;;
    help|--help|-h) show_help ;;
    *) log_error "未知命令: $1"; show_help; exit 1 ;;
esac
