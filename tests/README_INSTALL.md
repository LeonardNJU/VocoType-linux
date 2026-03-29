# VoCoType 安装时模型下载与 Fcitx5 自动启动

## 功能概述

本实现为 VoCoType DEB 包添加了以下功能：

1. **安装时模型下载提示** - 首次安装时提示用户下载约 500MB 的语音模型
2. **用户同意后自动下载** - 通过 GUI (zenity/kdialog) 或命令行交互式提示
3. **Fcitx5 后端服务自动启动** - 安装后自动启用并启动 systemd 用户服务

## 文件变更

### 新增文件

| 文件 | 描述 |
|------|------|
| `scripts/vocotype-download-models` | 命令行模型下载工具，支持 `--download`、`--prompt`、`--check` 等参数 |
| `debian/postinst` | 安装后脚本，处理模型下载提示和 Fcitx5 服务启动 |
| `packaging/debian/postinst` | 同步的 postinst 脚本 |
| `tests/test_install.py` | Python 测试套件，验证安装功能（33 个测试用例） |
| `tests/test_postinst.sh` | Shell 脚本测试，验证 postinst 脚本 |

### 修改文件

| 文件 | 变更 |
|------|------|
| `debian/rules` | 添加下载脚本安装步骤 |
| `debian/postinst` | 新增安装后处理逻辑 |

## 实现细节

### 1. 安装时模型下载流程

```
用户安装 DEB 包
    ↓
dpkg 执行 postinst configure
    ↓
检查模型是否已存在
    ↓
如果不存在：
    ├─ 有图形界面 → 使用 zenity/kdialog 提示
    └─ 命令行 → 使用交互式提示
        ↓
    用户同意 → 自动下载模型
    用户取消 → 提示手动下载命令
    ↓
完成安装
```

### 2. Fcitx5 后端服务启动流程

```
postinst configure
    ↓
启用 systemd 用户服务
    ↓
尝试立即启动服务
    ↓
重新加载 Fcitx5 配置（如果正在运行）
```

### 3. postinst 脚本功能

- **环境检测**：`is_interactive()`、`has_display()`
- **模型检查**：`models_exist()` - 检查 `.cache/modelscope/hub/models/iic/` 目录
- **GUI 提示**：`prompt_download_gui()` - 支持 zenity 和 kdialog
- **CLI 提示**：`prompt_download_cli()` - 交互式命令行提示
- **模型下载**：`download_models()` - 调用 `vocotype-download-models` 脚本
- **服务启动**：`setup_fcitx5_service()` - 启用并启动 systemd 服务

## 测试

### 运行所有测试

```bash
# Python 测试（33 个测试用例）
python3 tests/test_install.py

# Shell 脚本测试
chmod +x tests/test_postinst.sh
./tests/test_postinst.sh
```

### 测试覆盖

- **ModelManager 测试**：字节格式化、模型路径、存在性检查
- **Postinst 脚本测试**：shebang、函数定义、GUI/CLI 提示、Fcitx5 配置
- **下载脚本测试**：存在性、可执行性、语法、参数
- **Debian 包测试**：control、rules、install、systemd 服务文件
- **集成测试**：项目结构、脚本权限
- **边界情况测试**：空模型名、多层路径、非交互式环境
- **系统需求测试**：Python 版本、必需模块、bash 可用性

## 使用说明

### 对于用户

首次安装时：
1. 安装 DEB 包：`sudo dpkg -i vocotype_*.deb`
2. 图形界面用户会看到下载提示对话框
3. 命令行用户会看到交互式提示
4. 同意后会自动下载约 500MB 的模型
5. Fcitx5 后端服务会自动启动

### 手动下载模型

如果安装时跳过了模型下载：

```bash
# 交互式提示下载
sudo vocotype-download-models --prompt

# 直接下载
sudo vocotype-download-models --download

# 检查模型状态
sudo vocotype-download-models --check
```

### 手动启动 Fcitx5 后端

如果服务未自动启动：

```bash
# 启用服务（开机自启）
systemctl --user enable vocotype-fcitx5-backend.service

# 启动服务
systemctl --user start vocotype-fcitx5-backend.service

# 查看状态
systemctl --user status vocotype-fcitx5-backend.service
```

## 边界情况处理

1. **非交互式安装**（如 CI/CD）：自动跳过模型下载，不阻塞安装
2. **无图形界面**：自动回退到命令行提示
3. **模型已存在**：跳过下载提示
4. **下载失败**：显示友好错误信息，提示手动下载命令
5. **服务启动失败**：提示下次登录时自动启动
6. **sudo 安装**：正确处理 `SUDO_USER` 环境变量

## 设计原则

1. **最少修改主体代码**：所有功能集中在 postinst 脚本和独立下载工具
2. **健壮性**：完善的错误处理和边界情况考虑
3. **用户体验**：支持 GUI 和 CLI，自动检测环境
4. **可测试性**：全面的测试覆盖
5. **符合 Debian 规范**：正确使用 debhelper 和 maintainer 脚本
