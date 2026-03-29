# VoCoType 构建入口
# 所有构建产物将输出到 build/ 目录

.PHONY: all deb clean test help

all: deb

# 构建 Debian 包
deb:
	@./scripts/build-deb.sh deb

# 清理构建产物
clean:
	@./scripts/build-deb.sh clean

# 运行测试
test:
	@./scripts/build-deb.sh test

# 显示帮助
help:
	@echo "VoCoType 构建系统"
	@echo ""
	@echo "用法: make [目标]"
	@echo ""
	@echo "目标:"
	@echo "  make deb    - 构建 Debian 包 (输出到 build/output/)"
	@echo "  make clean  - 清理所有构建产物"
	@echo "  make test   - 运行测试套件"
	@echo "  make help   - 显示帮助信息"
	@echo ""
	@echo "或直接运行: ./scripts/build-deb.sh [命令]"
