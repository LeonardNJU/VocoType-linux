#!/bin/bash
# 运行打包系统测试

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

echo "=== VoCoType 打包系统测试 ==="
echo ""

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo "错误: Python3 未安装"
    exit 1
fi

# 运行验证脚本
echo "1. 运行打包配置验证..."
python3 "$SCRIPT_DIR/packaging/validate_packaging.py"
VALIDATE_EXIT=$?

echo ""
echo "2. 运行单元测试..."
python3 -m pytest "$SCRIPT_DIR/packaging/test_packaging.py" -v || \
    python3 "$SCRIPT_DIR/packaging/test_packaging.py"
TEST_EXIT=$?

# 汇总结果
echo ""
echo "=== 测试结果汇总 ==="
if [ $VALIDATE_EXIT -eq 0 ]; then
    echo "✓ 打包配置验证通过"
else
    echo "✗ 打包配置验证失败"
fi

if [ $TEST_EXIT -eq 0 ]; then
    echo "✓ 单元测试通过"
else
    echo "✗ 单元测试失败"
fi

# 总体结果
if [ $VALIDATE_EXIT -eq 0 ] && [ $TEST_EXIT -eq 0 ]; then
    echo ""
    echo "所有测试通过！"
    exit 0
else
    echo ""
    echo "部分测试失败"
    exit 1
fi
