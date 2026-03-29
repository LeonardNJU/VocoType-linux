#!/usr/bin/env python3
"""
VoCoType 打包系统验证工具

此脚本用于验证打包配置是否正确，可在 CI/CD 中使用。
"""

import os
import sys
import re
import argparse
from pathlib import Path
from typing import List, Set, Tuple, Optional


# 项目根目录 - 从 tests/packaging/ 上两级到达项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
PACKAGING_DIR = PROJECT_ROOT / "packaging"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"


class Colors:
    """终端颜色"""
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    BOLD = '\033[1m'
    RESET = '\033[0m'


def print_success(msg: str):
    print(f"{Colors.GREEN}✓{Colors.RESET} {msg}")


def print_error(msg: str):
    print(f"{Colors.RED}✗{Colors.RESET} {msg}")


def print_warning(msg: str):
    print(f"{Colors.YELLOW}⚠{Colors.RESET} {msg}")


def print_info(msg: str):
    print(f"{Colors.BLUE}ℹ{Colors.RESET} {msg}")


def print_section(title: str):
    print(f"\n{Colors.BOLD}{title}{Colors.RESET}")
    print("=" * len(title))


class PackagingValidator:
    """打包配置验证器"""

    def __init__(self):
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.version = self._get_version()

    def _get_version(self) -> str:
        """获取当前版本号"""
        version_file = PROJECT_ROOT / "vocotype_version.py"
        if version_file.exists():
            content = version_file.read_text()
            match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', content)
            if match:
                return match.group(1)
        return "unknown"

    def validate_file_exists(self, path: Path, description: str) -> bool:
        """验证文件存在"""
        if not path.exists():
            self.errors.append(f"{description} 不存在: {path}")
            return False
        return True

    def validate_debian_control(self) -> bool:
        """验证 Debian control 文件"""
        print_info("检查 Debian control...")
        control_file = PACKAGING_DIR / "debian" / "control"

        if not self.validate_file_exists(control_file, "Debian control 文件"):
            return False

        content = control_file.read_text()
        required_fields = [
            "Source:", "Section:", "Priority:", "Maintainer:",
            "Build-Depends:", "Package:", "Architecture:",
            "Depends:", "Description:"
        ]

        valid = True
        for field in required_fields:
            if field not in content:
                self.errors.append(f"Debian control 缺少字段: {field}")
                valid = False

        # 检查 Python 版本约束
        if "python3 (>= 3.11)" not in content:
            self.warnings.append("Debian control 可能缺少 Python >= 3.11 约束")
        if "python3 (<< 3.13)" not in content:
            self.warnings.append("Debian control 可能缺少 Python < 3.13 约束")

        if valid:
            print_success("Debian control 格式正确")
        return valid

    def validate_rpm_spec(self) -> bool:
        """验证 RPM spec 文件"""
        print_info("检查 RPM spec...")
        spec_file = PACKAGING_DIR / "rpm" / "vocotype.spec"

        if not self.validate_file_exists(spec_file, "RPM spec 文件"):
            return False

        content = spec_file.read_text()

        # 检查版本是否一致
        match = re.search(r'^Version:\s*(.+)$', content, re.MULTILINE)
        if match:
            spec_version = match.group(1).strip()
            if spec_version != self.version:
                self.errors.append(
                    f"RPM spec 版本 ({spec_version}) 与主版本 ({self.version}) 不一致"
                )
                return False

        required_sections = [
            "%description", "%prep", "%build", "%install", "%files"
        ]

        valid = True
        for section in required_sections:
            if section not in content:
                self.errors.append(f"RPM spec 缺少节: {section}")
                valid = False

        if valid:
            print_success(f"RPM spec 格式正确 (版本: {self.version})")
        return valid

    def validate_arch_pkgbuild(self) -> bool:
        """验证 Arch PKGBUILD"""
        print_info("检查 Arch PKGBUILD...")
        pkgbuild_file = PACKAGING_DIR / "arch" / "PKGBUILD"

        if not self.validate_file_exists(pkgbuild_file, "Arch PKGBUILD"):
            return False

        content = pkgbuild_file.read_text()

        # 检查版本是否一致
        match = re.search(r'^pkgver=(.+)$', content, re.MULTILINE)
        if match:
            pkgbuild_version = match.group(1).strip()
            if pkgbuild_version != self.version:
                self.errors.append(
                    f"Arch PKGBUILD 版本 ({pkgbuild_version}) 与主版本 ({self.version}) 不一致"
                )
                return False

        required_vars = ["pkgname=", "pkgver=", "pkgrel=", "pkgdesc="]
        valid = True
        for var in required_vars:
            if var not in content:
                self.errors.append(f"Arch PKGBUILD 缺少: {var}")
                valid = False

        if valid:
            print_success(f"Arch PKGBUILD 格式正确 (版本: {self.version})")
        return valid

    def validate_build_scripts(self) -> bool:
        """验证构建脚本"""
        print_info("检查构建脚本...")
        scripts = ["build-deb.sh", "build-rpm.sh", "build-arch.sh"]

        valid = True
        for script_name in scripts:
            script = SCRIPTS_DIR / script_name
            if not script.exists():
                self.errors.append(f"构建脚本不存在: {script_name}")
                valid = False
                continue

            content = script.read_text()
            if "set -e" not in content:
                self.warnings.append(f"{script_name} 缺少 'set -e'")

            if not os.access(script, os.X_OK):
                self.warnings.append(f"{script_name} 不可执行")

        if valid:
            print_success("所有构建脚本存在")
        return valid

    def validate_dependencies(self) -> bool:
        """验证依赖一致性"""
        print_info("检查依赖一致性...")

        pyproject = PROJECT_ROOT / "pyproject.toml"
        requirements = PROJECT_ROOT / "requirements.txt"

        if not pyproject.exists():
            self.errors.append("pyproject.toml 不存在")
            return False

        pyproject_content = pyproject.read_text()

        # 检查 Python 版本约束
        if 'requires-python = ">=3.11,<3.13"' not in pyproject_content:
            self.errors.append("pyproject.toml 缺少正确的 Python 版本约束 (>=3.11,<3.13)")
            return False

        print_success("依赖配置正确")
        return True

    def validate_required_files(self) -> bool:
        """验证必需文件"""
        print_info("检查必需文件...")

        required_files = [
            (PROJECT_ROOT / "LICENSE", "LICENSE"),
            (PROJECT_ROOT / "readme.md", "README"),
            (PROJECT_ROOT / "pyproject.toml", "pyproject.toml"),
            (PROJECT_ROOT / "vocotype_version.py", "版本文件"),
            (PROJECT_ROOT / "data" / "ibus" / "vocotype.xml.in", "IBus XML 模板"),
            (PROJECT_ROOT / "fcitx5" / "data" / "vocotype.conf", "Fcitx5 addon 配置"),
            (PACKAGING_DIR / "systemd" / "vocotype-fcitx5-backend.service", "Systemd 服务"),
        ]

        valid = True
        for path, desc in required_files:
            if not path.exists():
                self.errors.append(f"{desc} 不存在: {path}")
                valid = False

        if valid:
            print_success("所有必需文件存在")
        return valid

    def validate_install_paths(self) -> bool:
        """验证安装路径配置"""
        print_info("检查安装路径...")

        rules_file = PACKAGING_DIR / "debian" / "rules"
        if rules_file.exists():
            content = rules_file.read_text()
            required_paths = [
                "/usr/share/fcitx5/addon/",
                "/usr/share/ibus/component/",
            ]

            valid = True
            for path in required_paths:
                if path not in content:
                    self.warnings.append(f"Debian rules 可能缺少安装路径: {path}")

            if valid:
                print_success("安装路径配置正确")
            return valid

        return True

    def run_all_validations(self) -> bool:
        """运行所有验证"""
        print_section("VoCoType 打包系统验证")
        print(f"项目版本: {Colors.BOLD}{self.version}{Colors.RESET}\n")

        validators = [
            self.validate_required_files,
            self.validate_debian_control,
            self.validate_rpm_spec,
            self.validate_arch_pkgbuild,
            self.validate_build_scripts,
            self.validate_dependencies,
            self.validate_install_paths,
        ]

        all_valid = True
        for validator in validators:
            if not validator():
                all_valid = False

        return all_valid

    def print_summary(self):
        """打印验证摘要"""
        print_section("验证摘要")

        if self.errors:
            print_error(f"发现 {len(self.errors)} 个错误:")
            for error in self.errors:
                print(f"  - {error}")

        if self.warnings:
            print_warning(f"发现 {len(self.warnings)} 个警告:")
            for warning in self.warnings:
                print(f"  - {warning}")

        if not self.errors and not self.warnings:
            print_success("所有检查通过！")
            return True

        return len(self.errors) == 0


def main():
    parser = argparse.ArgumentParser(
        description="VoCoType 打包系统验证工具"
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="将警告也视为错误"
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="显示版本信息"
    )

    args = parser.parse_args()

    if args.version:
        validator = PackagingValidator()
        print(f"VoCoType {validator.version}")
        return 0

    validator = PackagingValidator()
    validator.run_all_validations()
    success = validator.print_summary()

    if args.strict and validator.warnings:
        return 1

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
