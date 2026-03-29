"""
VoCoType 打包系统测试套件

测试内容包括：
1. 打包配置文件验证（DEB/RPM/Arch）
2. 构建脚本测试
3. 文件完整性检查
4. 依赖版本一致性检查
5. 安装路径验证
"""

import os
import sys
import re
import subprocess
import unittest
from pathlib import Path
from typing import List, Tuple, Optional


# 项目根目录 - 从 tests/packaging/ 上两级到达项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
PACKAGING_DIR = PROJECT_ROOT / "packaging"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"


class PackagingConfigTest(unittest.TestCase):
    """打包配置文件验证测试"""

    def setUp(self):
        self.version_file = PROJECT_ROOT / "vocotype_version.py"
        self.version = self._get_version()

    def _get_version(self) -> str:
        """获取当前版本号"""
        if self.version_file.exists():
            content = self.version_file.read_text()
            match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', content)
            if match:
                return match.group(1)
        return "2.1.3"  # 默认版本

    def test_version_file_exists(self):
        """测试版本文件存在"""
        self.assertTrue(
            self.version_file.exists(),
            f"版本文件不存在: {self.version_file}"
        )

    def test_debian_control_exists(self):
        """测试 Debian control 文件存在"""
        control_file = PACKAGING_DIR / "debian" / "control"
        self.assertTrue(
            control_file.exists(),
            f"Debian control 文件不存在: {control_file}"
        )

    def test_debian_control_valid(self):
        """测试 Debian control 文件格式正确"""
        control_file = PACKAGING_DIR / "debian" / "control"
        content = control_file.read_text()

        # 检查必需字段
        required_fields = [
            "Source:",
            "Section:",
            "Priority:",
            "Maintainer:",
            "Build-Depends:",
            "Package:",
            "Architecture:",
            "Depends:",
            "Description:"
        ]

        for field in required_fields:
            self.assertIn(
                field,
                content,
                f"Debian control 缺少字段: {field}"
            )

    def test_debian_rules_exists(self):
        """测试 Debian rules 文件存在且可执行"""
        rules_file = PACKAGING_DIR / "debian" / "rules"
        self.assertTrue(
            rules_file.exists(),
            f"Debian rules 文件不存在: {rules_file}"
        )

    def test_debian_rules_shebang(self):
        """测试 Debian rules 有正确的 shebang"""
        rules_file = PACKAGING_DIR / "debian" / "rules"
        content = rules_file.read_text()
        self.assertTrue(
            content.startswith("#!/usr/bin/make -f"),
            "Debian rules 缺少正确的 shebang"
        )

    def test_rpm_spec_exists(self):
        """测试 RPM spec 文件存在"""
        spec_file = PACKAGING_DIR / "rpm" / "vocotype.spec"
        self.assertTrue(
            spec_file.exists(),
            f"RPM spec 文件不存在: {spec_file}"
        )

    def test_rpm_spec_valid(self):
        """测试 RPM spec 文件格式正确"""
        spec_file = PACKAGING_DIR / "rpm" / "vocotype.spec"
        content = spec_file.read_text()

        # 检查必需字段
        required_fields = [
            "Name:",
            "Version:",
            "Release:",
            "Summary:",
            "License:",
            "URL:",
            "Source0:",
            "BuildRequires:",
            "Requires:",
            "%description",
            "%prep",
            "%build",
            "%install",
            "%files"
        ]

        for field in required_fields:
            self.assertIn(
                field,
                content,
                f"RPM spec 缺少字段: {field}"
            )

    def test_arch_pkgbuild_exists(self):
        """测试 Arch PKGBUILD 文件存在"""
        pkgbuild_file = PACKAGING_DIR / "arch" / "PKGBUILD"
        self.assertTrue(
            pkgbuild_file.exists(),
            f"Arch PKGBUILD 文件不存在: {pkgbuild_file}"
        )

    def test_arch_pkgbuild_valid(self):
        """测试 Arch PKGBUILD 格式正确"""
        pkgbuild_file = PACKAGING_DIR / "arch" / "PKGBUILD"
        content = pkgbuild_file.read_text()

        # 检查必需变量
        required_vars = [
            "pkgname=",
            "pkgver=",
            "pkgrel=",
            "pkgdesc=",
            "arch=",
            "url=",
            "license=",
            "depends=",
            "makedepends=",
            "build()",
            "package()"
        ]

        for var in required_vars:
            self.assertIn(
                var,
                content,
                f"Arch PKGBUILD 缺少: {var}"
            )

    def test_systemd_service_exists(self):
        """测试 systemd 服务文件存在"""
        service_file = PACKAGING_DIR / "systemd" / "vocotype-fcitx5-backend.service"
        self.assertTrue(
            service_file.exists(),
            f"Systemd 服务文件不存在: {service_file}"
        )

    def test_systemd_service_valid(self):
        """测试 systemd 服务文件格式正确"""
        service_file = PACKAGING_DIR / "systemd" / "vocotype-fcitx5-backend.service"
        content = service_file.read_text()

        # 检查必需节
        required_sections = ["[Unit]", "[Service]"]
        for section in required_sections:
            self.assertIn(
                section,
                content,
                f"Systemd 服务文件缺少节: {section}"
            )


class VersionConsistencyTest(unittest.TestCase):
    """版本一致性测试"""

    def setUp(self):
        self.version_file = PROJECT_ROOT / "vocotype_version.py"
        self.version = self._get_version()
        self.pyproject = PROJECT_ROOT / "pyproject.toml"

    def _get_version(self) -> str:
        """获取当前版本号"""
        if self.version_file.exists():
            content = self.version_file.read_text()
            match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', content)
            if match:
                return match.group(1)
        return "2.1.3"

    def test_rpm_spec_version_matches(self):
        """测试 RPM spec 版本与主版本一致"""
        spec_file = PACKAGING_DIR / "rpm" / "vocotype.spec"
        content = spec_file.read_text()

        match = re.search(r'^Version:\s*(.+)$', content, re.MULTILINE)
        self.assertIsNotNone(match, "RPM spec 缺少 Version 字段")
        spec_version = match.group(1).strip()

        self.assertEqual(
            spec_version,
            self.version,
            f"RPM spec 版本 ({spec_version}) 与主版本 ({self.version}) 不一致"
        )

    def test_arch_pkgbuild_version_matches(self):
        """测试 Arch PKGBUILD 版本与主版本一致"""
        pkgbuild_file = PACKAGING_DIR / "arch" / "PKGBUILD"
        content = pkgbuild_file.read_text()

        match = re.search(r'^pkgver=(.+)$', content, re.MULTILINE)
        self.assertIsNotNone(match, "Arch PKGBUILD 缺少 pkgver")
        pkgbuild_version = match.group(1).strip()

        self.assertEqual(
            pkgbuild_version,
            self.version,
            f"Arch PKGBUILD 版本 ({pkgbuild_version}) 与主版本 ({self.version}) 不一致"
        )

    def test_pyproject_version_dynamic(self):
        """测试 pyproject.toml 使用动态版本"""
        if not self.pyproject.exists():
            self.skipTest("pyproject.toml 不存在")

        content = self.pyproject.read_text()

        # 检查是否使用动态版本
        self.assertIn(
            'dynamic = ["version"]',
            content,
            "pyproject.toml 应该使用动态版本"
        )

        # 检查版本来源
        self.assertIn(
            'version = { attr = "vocotype_version.__version__" }',
            content,
            "pyproject.toml 应该从 vocotype_version 获取版本"
        )


class DependencyConsistencyTest(unittest.TestCase):
    """依赖一致性测试"""

    def setUp(self):
        self.pyproject = PROJECT_ROOT / "pyproject.toml"
        self.requirements = PROJECT_ROOT / "requirements.txt"

    def _get_pyproject_deps(self) -> List[str]:
        """从 pyproject.toml 获取依赖"""
        if not self.pyproject.exists():
            return []

        content = self.pyproject.read_text()
        deps = []

        # 解析 dependencies
        in_deps = False
        for line in content.split('\n'):
            if 'dependencies = [' in line:
                in_deps = True
                continue
            if in_deps:
                if ']' in line:
                    break
                match = re.search(r'"([^"]+)"', line)
                if match:
                    deps.append(match.group(1))

        return deps

    def _get_requirements_deps(self) -> List[str]:
        """从 requirements.txt 获取依赖"""
        if not self.requirements.exists():
            return []

        content = self.requirements.read_text()
        deps = []

        for line in content.split('\n'):
            line = line.strip()
            if line and not line.startswith('#'):
                deps.append(line)

        return deps

    def test_requirements_matches_pyproject(self):
        """测试 requirements.txt 与 pyproject.toml 依赖一致"""
        pyproject_deps = self._get_pyproject_deps()
        requirements_deps = self._get_requirements_deps()

        # 提取包名（不含版本）
        def get_package_name(dep: str) -> str:
            return dep.split('==')[0].split('>=')[0].split('<')[0].strip()

        pyproject_packages = {get_package_name(d) for d in pyproject_deps}
        requirements_packages = {get_package_name(d) for d in requirements_deps}

        # 检查 requirements.txt 中的包是否都在 pyproject.toml 中
        missing_in_pyproject = requirements_packages - pyproject_packages
        self.assertEqual(
            missing_in_pyproject,
            set(),
            f"requirements.txt 中有但 pyproject.toml 缺少的包: {missing_in_pyproject}"
        )

    def test_debian_python_version_constraint(self):
        """测试 Debian 控制文件 Python 版本约束"""
        control_file = PACKAGING_DIR / "debian" / "control"
        content = control_file.read_text()

        # 检查 Build-Depends 中的 Python 版本
        self.assertIn(
            "python3 (>= 3.11)",
            content,
            "Debian Build-Depends 缺少 Python >= 3.11 约束"
        )
        self.assertIn(
            "python3 (<< 3.13)",
            content,
            "Debian Build-Depends 缺少 Python < 3.13 约束"
        )

    def test_rpm_python_version_constraint(self):
        """测试 RPM spec Python 版本约束"""
        spec_file = PACKAGING_DIR / "rpm" / "vocotype.spec"
        content = spec_file.read_text()

        # 检查 BuildRequires 中的 Python 版本
        self.assertRegex(
            content,
            r'BuildRequires:\s*python3-devel\s*>=\s*3\.11',
            "RPM spec 缺少 Python >= 3.11 约束"
        )


class BuildScriptsTest(unittest.TestCase):
    """构建脚本测试"""

    def test_build_deb_script_exists(self):
        """测试 build-deb.sh 脚本存在"""
        script = SCRIPTS_DIR / "build-deb.sh"
        self.assertTrue(
            script.exists(),
            f"build-deb.sh 脚本不存在: {script}"
        )

    def test_build_deb_script_shebang(self):
        """测试 build-deb.sh 有正确的 shebang"""
        script = SCRIPTS_DIR / "build-deb.sh"
        content = script.read_text()
        self.assertTrue(
            content.startswith("#!/bin/bash"),
            "build-deb.sh 缺少正确的 shebang"
        )

    def test_build_deb_script_has_set_e(self):
        """测试 build-deb.sh 有 set -e"""
        script = SCRIPTS_DIR / "build-deb.sh"
        content = script.read_text()
        self.assertIn(
            "set -e",
            content,
            "build-deb.sh 应该包含 set -e 以确保错误退出"
        )

    def test_build_rpm_script_exists(self):
        """测试 build-rpm.sh 脚本存在"""
        script = SCRIPTS_DIR / "build-rpm.sh"
        self.assertTrue(
            script.exists(),
            f"build-rpm.sh 脚本不存在: {script}"
        )

    def test_build_arch_script_exists(self):
        """测试 build-arch.sh 脚本存在"""
        script = SCRIPTS_DIR / "build-arch.sh"
        self.assertTrue(
            script.exists(),
            f"build-arch.sh 脚本不存在: {script}"
        )

    def test_build_scripts_executable(self):
        """测试构建脚本可执行"""
        for script_name in ["build-deb.sh", "build-rpm.sh", "build-arch.sh"]:
            script = SCRIPTS_DIR / script_name
            if script.exists():
                self.assertTrue(
                    os.access(script, os.X_OK),
                    f"{script_name} 应该可执行"
                )


class FileIntegrityTest(unittest.TestCase):
    """文件完整性测试"""

    def test_ibus_component_xml_template_exists(self):
        """测试 IBus 组件 XML 模板存在"""
        xml_file = PROJECT_ROOT / "data" / "ibus" / "vocotype.xml.in"
        self.assertTrue(
            xml_file.exists(),
            f"IBus 组件 XML 模板不存在: {xml_file}"
        )

    def test_ibus_component_xml_template_valid(self):
        """测试 IBus 组件 XML 模板格式正确"""
        xml_file = PROJECT_ROOT / "data" / "ibus" / "vocotype.xml.in"
        content = xml_file.read_text()

        # 检查必需元素
        required_elements = [
            "<component>",
            "<name>",
            "<description>",
            "<exec>",
            "</component>"
        ]

        for element in required_elements:
            self.assertIn(
                element,
                content,
                f"IBus XML 模板缺少元素: {element}"
            )

    def test_fcitx5_addon_conf_exists(self):
        """测试 Fcitx5 addon 配置存在"""
        conf_file = PROJECT_ROOT / "fcitx5" / "data" / "vocotype.conf"
        self.assertTrue(
            conf_file.exists(),
            f"Fcitx5 addon 配置不存在: {conf_file}"
        )

    def test_fcitx5_inputmethod_conf_exists(self):
        """测试 Fcitx5 输入法配置模板存在（打包时生成）"""
        # 配置文件可能是在打包时从模板生成的
        conf_file = PROJECT_ROOT / "fcitx5" / "data" / "vocotype.conf.in"
        if conf_file.exists():
            self.assertTrue(
                conf_file.exists(),
                f"Fcitx5 输入法配置模板不存在: {conf_file}"
            )
        else:
            # 如果模板不存在，至少检查主配置存在
            conf_file = PROJECT_ROOT / "fcitx5" / "data" / "vocotype.conf"
            self.assertTrue(
                conf_file.exists(),
                f"Fcitx5 输入法配置不存在: {conf_file}"
            )

    def test_license_file_exists(self):
        """测试 LICENSE 文件存在"""
        license_file = PROJECT_ROOT / "LICENSE"
        self.assertTrue(
            license_file.exists(),
            f"LICENSE 文件不存在: {license_file}"
        )

    def test_readme_exists(self):
        """测试 readme.md 存在"""
        readme = PROJECT_ROOT / "readme.md"
        self.assertTrue(
            readme.exists(),
            f"readme.md 不存在: {readme}"
        )

    def test_app_directory_exists(self):
        """测试 app 目录存在"""
        app_dir = PROJECT_ROOT / "app"
        self.assertTrue(
            app_dir.exists() and app_dir.is_dir(),
            f"app 目录不存在: {app_dir}"
        )

    def test_app_init_exists(self):
        """测试 app/__init__.py 存在"""
        init_file = PROJECT_ROOT / "app" / "__init__.py"
        self.assertTrue(
            init_file.exists(),
            f"app/__init__.py 不存在: {init_file}"
        )


class InstallPathsTest(unittest.TestCase):
    """安装路径验证测试"""

    def test_debian_rules_install_paths(self):
        """测试 Debian rules 安装路径正确"""
        rules_file = PACKAGING_DIR / "debian" / "rules"
        content = rules_file.read_text()

        # 检查关键安装路径
        required_paths = [
            "/usr/share/fcitx5/addon/",
            "/usr/share/fcitx5/inputmethod/",
            "/usr/share/ibus/component/",
            "/usr/share/vocotype/",
            "/usr/lib/systemd/user/"
        ]

        for path in required_paths:
            self.assertIn(
                path,
                content,
                f"Debian rules 缺少安装路径: {path}"
            )

    def test_rpm_spec_install_paths(self):
        """测试 RPM spec 安装路径正确"""
        spec_file = PACKAGING_DIR / "rpm" / "vocotype.spec"
        content = spec_file.read_text()

        # 检查关键安装路径（使用 RPM 宏）
        required_macros = [
            "%{_datadir}/fcitx5/",
            "%{_datadir}/ibus/",
            "%{_datadir}/vocotype/",
            "%{_userunitdir}/"
        ]

        for macro in required_macros:
            self.assertIn(
                macro,
                content,
                f"RPM spec 缺少安装路径宏: {macro}"
            )

    def test_arch_pkgbuild_install_paths(self):
        """测试 Arch PKGBUILD 安装路径正确"""
        pkgbuild_file = PACKAGING_DIR / "arch" / "PKGBUILD"
        content = pkgbuild_file.read_text()

        # 检查关键安装路径
        required_paths = [
            "$pkgdir/usr/share/fcitx5/",
            "$pkgdir/usr/share/ibus/",
            "$pkgdir/usr/share/vocotype/",
            "$pkgdir/usr/lib/systemd/user/"
        ]

        for path in required_paths:
            self.assertIn(
                path,
                content,
                f"Arch PKGBUILD 缺少安装路径: {path}"
            )


class PythonPackageTest(unittest.TestCase):
    """Python 包测试"""

    def setUp(self):
        self.pyproject = PROJECT_ROOT / "pyproject.toml"

    def test_pyproject_exists(self):
        """测试 pyproject.toml 存在"""
        self.assertTrue(
            self.pyproject.exists(),
            f"pyproject.toml 不存在"
        )

    def test_pyproject_build_system(self):
        """测试 pyproject.toml 构建系统配置"""
        content = self.pyproject.read_text()

        # 检查构建系统
        self.assertIn(
            '[build-system]',
            content,
            "pyproject.toml 缺少 [build-system] 节"
        )

        self.assertIn(
            'requires = ["setuptools',
            content,
            "pyproject.toml 应该使用 setuptools 作为构建后端"
        )

    def test_pyproject_scripts(self):
        """测试 pyproject.toml 控制台脚本配置"""
        content = self.pyproject.read_text()

        # 检查入口点
        self.assertIn(
            '[project.scripts]',
            content,
            "pyproject.toml 缺少 [project.scripts] 节"
        )

        # 检查必需的脚本
        required_scripts = [
            "vocotype-ibus",
            "vocotype-download-models"
        ]

        for script in required_scripts:
            self.assertIn(
                f"{script} =",
                content,
                f"pyproject.toml 缺少 {script} 脚本配置"
            )

    def test_pyproject_python_version(self):
        """测试 pyproject.toml Python 版本约束"""
        content = self.pyproject.read_text()

        self.assertIn(
            'requires-python = ">=3.11,<3.13"',
            content,
            "pyproject.toml 应该限制 Python 版本为 3.11-3.12"
        )


class CommandLineToolTest(unittest.TestCase):
    """命令行工具测试"""

    def test_vocotype_ibus_engine_source(self):
        """测试 vocotype-ibus-engine 脚本源文件存在（打包时生成）"""
        # 检查安装脚本是否存在，它会生成 vocotype-ibus-engine
        install_script = SCRIPTS_DIR / "install-ibus.sh"
        self.assertTrue(
            install_script.exists(),
            f"install-ibus.sh 脚本不存在（用于生成 vocotype-ibus-engine）"
        )

    def test_fcitx5_config_source(self):
        """测试 Fcitx5 配置文件源文件存在"""
        # 检查 vocotype.conf.in 模板（打包时生成 vocotype.conf）
        conf_template = PROJECT_ROOT / "fcitx5" / "data" / "vocotype.conf.in"
        if conf_template.exists():
            self.assertTrue(
                conf_template.exists(),
                f"Fcitx5 配置模板不存在: {conf_template}"
            )


class GitHubActionsTest(unittest.TestCase):
    """GitHub Actions 配置测试"""

    def test_github_workflows_dir_exists(self):
        """测试 .github/workflows 目录存在"""
        workflows_dir = PROJECT_ROOT / ".github" / "workflows"
        self.assertTrue(
            workflows_dir.exists() and workflows_dir.is_dir(),
            f".github/workflows 目录不存在"
        )

    def test_release_workflow_exists(self):
        """测试发布工作流存在"""
        # 查找任意工作流文件
        workflows_dir = PROJECT_ROOT / ".github" / "workflows"
        if workflows_dir.exists():
            workflows = list(workflows_dir.glob("*.yml")) + list(workflows_dir.glob("*.yaml"))
            self.assertTrue(
                len(workflows) > 0,
                "应该至少有一个 GitHub Actions 工作流文件"
            )


class DocumentationTest(unittest.TestCase):
    """文档测试"""

    def test_packaging_readme_exists(self):
        """测试 packaging README 存在"""
        readme = PACKAGING_DIR / "README.md"
        self.assertTrue(
            readme.exists(),
            f"packaging/README.md 不存在"
        )

    def test_packaging_readme_content(self):
        """测试 packaging README 包含必要内容"""
        readme = PACKAGING_DIR / "README.md"
        content = readme.read_text()

        # 检查是否提到所有打包格式
        required_formats = ["DEB", "RPM", "Arch", "PKGBUILD"]
        for fmt in required_formats:
            self.assertIn(
                fmt,
                content,
                f"packaging README 应该提到 {fmt} 格式"
            )


def run_tests():
    """运行所有测试"""
    # 创建测试套件
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # 添加所有测试类
    test_classes = [
        PackagingConfigTest,
        VersionConsistencyTest,
        DependencyConsistencyTest,
        BuildScriptsTest,
        FileIntegrityTest,
        InstallPathsTest,
        PythonPackageTest,
        CommandLineToolTest,
        GitHubActionsTest,
        DocumentationTest
    ]

    for test_class in test_classes:
        tests = loader.loadTestsFromTestCase(test_class)
        suite.addTests(tests)

    # 运行测试
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # 返回退出码
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(run_tests())
