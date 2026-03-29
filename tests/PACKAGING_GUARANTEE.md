# VoCoType Linux 打包保证机制总结

## 当前打包保证实现

本项目通过以下机制保证打包的正确性和一致性：

### 1. 多平台打包配置

#### DEB 包 (Debian/Ubuntu)
- **配置文件**: `packaging/debian/` 目录
  - `control` - 包元数据和依赖
  - `rules` - 构建规则（Makefile 格式）
  - `compat` - debhelper 兼容级别
  - `changelog` - 版本变更记录
  - `copyright` - 版权信息
  - `source/format` - 源码格式

- **构建脚本**: `scripts/build-deb.sh`
  - 自动安装构建依赖
  - 复制 debian 配置到项目根目录
  - 自动更新 changelog 版本
  - 使用 `dpkg-buildpackage` 构建

#### RPM 包 (Fedora/RHEL/SUSE)
- **配置文件**: `packaging/rpm/vocotype.spec`
  - 完整的 RPM 规范定义
  - BuildRequires/Requires 依赖声明
  - %prep/%build/%install/%files 各阶段定义

- **构建脚本**: `scripts/build-rpm.sh`
  - 自动设置 rpmbuild 目录结构
  - 创建源码 tarball
  - 使用 `rpmbuild -ba` 构建

#### Arch 包 (Arch Linux)
- **配置文件**: `packaging/arch/PKGBUILD`
  - 符合 Arch 打包标准
  - 包含 build() 和 package() 函数

- **构建脚本**: `scripts/build-arch.sh`
  - 自动更新版本和 checksums
  - 使用 `makepkg` 构建

### 2. 版本一致性保证

**单点版本源**: `vocotype_version.py`
```python
__version__ = "2.1.3"
```

**各打包配置的版本引用**:
- Debian: 通过 `build-deb.sh` 自动更新 changelog
- RPM: spec 文件中 `Version: 2.1.3`
- Arch: PKGBUILD 中 `pkgver=2.1.3`
- Python: `pyproject.toml` 使用动态版本

### 3. 依赖管理保证

**Python 依赖** (单点定义):
- `pyproject.toml` - 主要依赖声明
- `requirements.txt` - 导出格式

**系统依赖一致性**:
| 组件 | Debian | RPM | Arch |
|-----|--------|-----|------|
| Python | python3 (>=3.11, <<3.13) | python3-devel >=3.11 | python>=3.11 |
| Fcitx5 | libfcitx5core-dev | fcitx5-devel | fcitx5 |
| JSON | nlohmann-json3-dev | nlohmann-json-devel | nlohmann-json |
| Audio | libportaudio2 | portaudio | portaudio |

### 4. 安装路径标准化

所有打包格式使用一致的安装路径:

| 内容 | 安装路径 |
|-----|---------|
| Python 包 | `/usr/lib/python3.x/site-packages/` 或 `/usr/share/vocotype/` |
| Fcitx5 addon | `/usr/lib/fcitx5/vocotype.so` |
| Fcitx5 配置 | `/usr/share/fcitx5/{addon,inputmethod}/` |
| IBus 配置 | `/usr/share/ibus/component/` |
| Systemd 服务 | `/usr/lib/systemd/user/` |
| 应用数据 | `/usr/share/vocotype/` |

### 5. 测试保证 (新增)

#### 单元测试 (`tests/packaging/test_packaging.py`)
10 个测试类，44 个测试方法，覆盖:
- 打包配置文件格式验证
- 版本一致性检查
- 依赖一致性检查
- 文件完整性验证
- 安装路径验证

#### 验证工具 (`tests/packaging/validate_packaging.py`)
独立的验证脚本，提供:
- 彩色输出报告
- 详细的错误/警告信息
- 严格模式支持 (`--strict`)

#### CI/CD 集成 (`.github/workflows/packaging-tests.yml`)
自动化检查:
- 打包配置验证
- 构建脚本语法检查
- 版本一致性验证
- 文件结构检查

### 6. 文件生成机制

以下文件在打包/安装时从模板生成:

| 模板文件 | 生成文件 | 生成时机 |
|---------|---------|---------|
| `data/ibus/vocotype.xml.in` | `ibus/vocotype.xml` | 安装时由 `install-ibus.sh` 生成 |
| `fcitx5/data/vocotype.conf.in` | `fcitx5/data/vocotype.conf` | 构建时生成 |
| - | `scripts/vocotype-ibus-engine` | 安装时动态创建 |

### 7. 打包流程保证

#### 开发阶段
1. 修改代码并更新 `vocotype_version.py` 版本号
2. 运行打包测试: `./tests/run_packaging_tests.sh`
3. 确保所有测试通过

#### 构建阶段
1. 运行对应的构建脚本: `./scripts/build-{deb,rpm,arch}.sh`
2. 脚本自动处理版本同步
3. 生成对应格式的安装包

#### 发布阶段
1. GitHub Actions 自动运行打包测试
2. 通过所有检查后生成 Release
3. 上传构建好的包文件

## 打包保证检查清单

- [ ] 版本号在所有打包配置中一致
- [ ] 依赖声明在各格式中同步
- [ ] 安装路径在各格式中一致
- [ ] 所有必需文件存在
- [ ] 构建脚本可执行且有 `set -e`
- [ ] Systemd 服务文件格式正确
- [ ] 文档完整（README、LICENSE）

## 运行测试

```bash
# 完整测试套件
./tests/run_packaging_tests.sh

# 仅验证工具
python tests/packaging/validate_packaging.py

# 仅单元测试
python tests/packaging/test_packaging.py
```
