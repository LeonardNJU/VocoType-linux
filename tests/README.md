# VoCoType 打包系统测试

本目录包含 VoCoType 项目的打包系统测试代码，用于验证 DEB、RPM 和 Arch 三种打包格式的配置正确性。

## 测试结构

```
tests/
├── packaging/
│   ├── __init__.py              # 测试包初始化
│   ├── test_packaging.py        # 单元测试套件
│   └── validate_packaging.py    # 打包验证工具
└── run_packaging_tests.sh       # 测试运行脚本
```

## 测试内容

### 1. 打包配置验证 (test_packaging.py)

包含以下测试类别：

- **PackagingConfigTest** - 打包配置文件验证
  - Debian control 文件格式
  - Debian rules 文件格式
  - RPM spec 文件格式
  - Arch PKGBUILD 格式
  - Systemd 服务文件格式

- **VersionConsistencyTest** - 版本一致性检查
  - 所有打包配置的版本号与主版本一致
  - pyproject.toml 动态版本配置

- **DependencyConsistencyTest** - 依赖一致性检查
  - requirements.txt 与 pyproject.toml 依赖一致
  - Python 版本约束（3.11-3.12）

- **BuildScriptsTest** - 构建脚本测试
  - 脚本存在性检查
  - Shebang 检查
  - `set -e` 错误处理检查
  - 可执行权限检查

- **FileIntegrityTest** - 文件完整性检查
  - 必需文件存在性
  - XML/配置文件格式

- **InstallPathsTest** - 安装路径验证
  - 各打包格式的安装路径配置

- **PythonPackageTest** - Python 包测试
  - pyproject.toml 配置
  - 构建系统配置
  - 控制台脚本配置

- **CommandLineToolTest** - 命令行工具测试
  - 启动脚本存在性

- **GitHubActionsTest** - CI/CD 配置测试
  - 工作流文件存在性

- **DocumentationTest** - 文档测试
  - README 完整性

### 2. 打包验证工具 (validate_packaging.py)

独立的验证工具，提供彩色输出和详细报告：

```bash
# 基本验证
python tests/packaging/validate_packaging.py

# 严格模式（警告视为错误）
python tests/packaging/validate_packaging.py --strict

# 显示版本
python tests/packaging/validate_packaging.py --version
```

## 使用方法

### 运行所有测试

```bash
# 使用测试运行脚本
./tests/run_packaging_tests.sh

# 或使用 pytest
pytest tests/packaging/test_packaging.py -v

# 直接运行单元测试
python tests/packaging/test_packaging.py
```

### 运行单个测试类

```bash
# 仅运行打包配置测试
python -m unittest tests.packaging.test_packaging.PackagingConfigTest -v

# 仅运行版本一致性测试
python -m unittest tests.packaging.test_packaging.VersionConsistencyTest -v
```

### 运行单个测试方法

```bash
# 测试特定功能
python -m unittest tests.packaging.test_packaging.PackagingConfigTest.test_debian_control_valid -v
```

## CI/CD 集成

GitHub Actions 工作流配置已包含在 `.github/workflows/packaging-tests.yml` 中，自动在以下情况运行：

- 推送到 master/main/develop 分支
- 修改打包相关文件时
- 创建 Pull Request 时

工作流包含以下任务：
1. **test-packaging** - 运行打包配置验证和单元测试
2. **test-build-scripts** - 验证构建脚本语法和权限
3. **check-version-consistency** - 检查版本号一致性
4. **check-file-structure** - 检查必需文件存在性

## 添加新测试

要添加新的打包测试，请遵循以下步骤：

1. 在 `test_packaging.py` 中找到合适的测试类，或创建新类
2. 添加以 `test_` 开头的新方法
3. 使用 `self.assert*` 方法进行断言
4. 运行测试确认通过

示例：

```python
def test_new_feature(self):
    """测试新功能"""
    config_file = PACKAGING_DIR / "debian" / "new_file"
    self.assertTrue(
        config_file.exists(),
        f"新配置文件不存在: {config_file}"
    )
```

## 故障排除

### 测试失败常见原因

1. **版本不一致** - 确保所有打包配置的版本号与 `vocotype_version.py` 一致
2. **文件缺失** - 检查是否遗漏了必需的打包配置文件
3. **路径错误** - 验证 PROJECT_ROOT 是否正确指向项目根目录

### 调试测试

```bash
# 使用详细输出
python tests/packaging/test_packaging.py -v

# 使用 PDB 调试
python -m pdb tests/packaging/test_packaging.py
```

## 维护说明

当修改打包配置时，请确保：

1. 运行测试验证配置正确性
2. 如果添加新的打包文件，添加相应的测试
3. 更新版本号时同步更新所有打包配置

## 相关文档

- `packaging/README.md` - 打包系统使用指南
- `.github/workflows/packaging-tests.yml` - CI/CD 配置
