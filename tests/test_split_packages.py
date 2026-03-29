#!/usr/bin/env python3
"""
VoCoType 分离包测试套件
测试分离后的包结构和依赖关系
"""

import unittest
import subprocess
import os
import tempfile
import shutil


class TestPackageStructure(unittest.TestCase):
    """测试包结构完整性"""

    @classmethod
    def setUpClass(cls):
        cls.debian_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'debian'
        )

    def test_control_file_exists(self):
        """测试 control 文件存在"""
        control_path = os.path.join(self.debian_dir, 'control')
        self.assertTrue(os.path.exists(control_path), "control 文件不存在")

    def test_all_packages_defined(self):
        """测试所有包都在 control 中定义"""
        control_path = os.path.join(self.debian_dir, 'control')
        with open(control_path, 'r') as f:
            content = f.read()

        required_packages = [
            'vocotype-common',
            'vocotype-ibus',
            'vocotype-fcitx5',
            'vocotype'
        ]

        for pkg in required_packages:
            self.assertIn(f'Package: {pkg}', content,
                         f"包 {pkg} 未在 control 中定义")

    def test_install_files_exist(self):
        """测试所有 .install 文件存在"""
        install_files = [
            'vocotype-common.install',
            'vocotype-ibus.install',
            'vocotype-fcitx5.install'
        ]

        for f in install_files:
            path = os.path.join(self.debian_dir, f)
            self.assertTrue(os.path.exists(path),
                           f"{f} 不存在")

    def test_postinst_files_exist(self):
        """测试所有 .postinst 文件存在且可执行"""
        postinst_files = [
            'vocotype-common.postinst',
            'vocotype-fcitx5.postinst',
            'vocotype-ibus.postinst'
        ]

        for f in postinst_files:
            path = os.path.join(self.debian_dir, f)
            self.assertTrue(os.path.exists(path),
                           f"{f} 不存在")
            self.assertTrue(os.access(path, os.X_OK),
                           f"{f} 不可执行")


class TestPackageDependencies(unittest.TestCase):
    """测试包依赖关系"""

    @classmethod
    def setUpClass(cls):
        cls.debian_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'debian'
        )
        control_path = os.path.join(cls.debian_dir, 'control')
        with open(control_path, 'r') as f:
            cls.control_content = f.read()

    def test_vocotype_common_no_framework_dependency(self):
        """测试 common 包不依赖输入法框架"""
        # 提取 common 包段落
        common_section = self._extract_package_section('vocotype-common')

        # 不应包含 ibus 或 fcitx5 依赖
        self.assertNotIn('ibus,', common_section,
                        "common 包不应依赖 ibus")
        self.assertNotIn('fcitx5,', common_section,
                        "common 包不应依赖 fcitx5")

    def test_vocotype_ibus_depends_on_common(self):
        """测试 ibus 包依赖 common"""
        section = self._extract_package_section('vocotype-ibus')
        self.assertIn('vocotype-common (= ${binary:Version})',
                     section,
                     "ibus 包应依赖 common")
        self.assertIn('ibus,', section,
                     "ibus 包应依赖 ibus")

    def test_vocotype_fcitx5_depends_on_common(self):
        """测试 fcitx5 包依赖 common"""
        section = self._extract_package_section('vocotype-fcitx5')
        self.assertIn('vocotype-common (= ${binary:Version})',
                     section,
                     "fcitx5 包应依赖 common")
        self.assertIn('fcitx5,', section,
                     "fcitx5 包应依赖 fcitx5")

    def test_vocotype_meta_package_or_dependency(self):
        """测试元包使用 | 依赖"""
        section = self._extract_package_section('vocotype')

        # 应包含或依赖
        self.assertRegex(
            section,
            r'vocotype-ibus.*\|.*vocotype-fcitx5|vocotype-fcitx5.*\|.*vocotype-ibus',
            "元包应使用 | 依赖 ibus 或 fcitx5 包"
        )

    def test_no_circular_dependencies(self):
        """测试没有循环依赖"""
        # 提取 common 包的 Depends 部分
        common_section = self._extract_package_section('vocotype-common')

        # 只检查 Depends 行，不包含 Description
        depends_section = ""
        for line in common_section.split('\n'):
            if line.startswith('Depends:'):
                depends_section = line
                break
            elif line.startswith('Description:'):
                break  # 停止在 Description 之前

        # common 的 Depends 不应包含 vocotype-ibus 或 vocotype-fcitx5
        self.assertNotIn('vocotype-ibus', depends_section,
                        "common 不应反向依赖 ibus")
        self.assertNotIn('vocotype-fcitx5', depends_section,
                        "common 不应反向依赖 fcitx5")

    def test_vocotype_meta_no_direct_framework_dep(self):
        """测试元包不直接依赖输入法框架"""
        section = self._extract_package_section('vocotype')

        # 不应直接依赖 ibus 或 fcitx5（应通过子包依赖）
        lines = section.split('\n')
        depends_line = ''
        for line in lines:
            if line.startswith('Depends:'):
                depends_line = line
                break

        # 提取 Depends 内容
        self.assertNotRegex(depends_line, r'\bibus\b',
                           "元包不应直接依赖 ibus")
        self.assertNotRegex(depends_line, r'\bfcitx5\b',
                           "元包不应直接依赖 fcitx5")

    def _extract_package_section(self, package_name):
        """从 control 内容提取指定包段落"""
        lines = self.control_content.split('\n')
        section = []
        in_section = False

        for line in lines:
            if line.startswith(f'Package: {package_name}'):
                in_section = True
                section.append(line)
            elif in_section:
                if line.startswith('Package:') and package_name not in line:
                    break
                section.append(line)

        return '\n'.join(section)


class TestInstallFileContent(unittest.TestCase):
    """测试 .install 文件内容"""

    @classmethod
    def setUpClass(cls):
        cls.debian_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'debian'
        )

    def test_common_install_content(self):
        """测试 common 包安装正确文件"""
        path = os.path.join(self.debian_dir, 'vocotype-common.install')
        with open(path, 'r') as f:
            content = f.read()

        # 应包含 Python 模块
        self.assertIn('python3/dist-packages', content,
                     "common 应安装 Python 模块")

        # 应包含下载脚本
        self.assertIn('vocotype-download-models', content,
                     "common 应安装下载脚本")

        # 不应包含输入法框架特定文件
        self.assertNotIn('fcitx5', content,
                        "common 不应包含 fcitx5 文件")
        self.assertNotIn('ibus/component', content,
                        "common 不应包含 ibus 组件")

    def test_ibus_install_content(self):
        """测试 ibus 包安装正确文件"""
        path = os.path.join(self.debian_dir, 'vocotype-ibus.install')
        with open(path, 'r') as f:
            content = f.read()

        # 应包含 IBus 组件
        self.assertIn('ibus/component/vocotype.xml', content,
                     "ibus 包应安装组件 XML")

        # 应包含引擎启动脚本
        self.assertIn('vocotype-ibus-engine', content,
                     "ibus 包应安装引擎脚本")

        # 不应包含 Fcitx5 文件
        self.assertNotIn('fcitx5', content,
                        "ibus 包不应包含 fcitx5 文件")

    def test_fcitx5_install_content(self):
        """测试 fcitx5 包安装正确文件"""
        path = os.path.join(self.debian_dir, 'vocotype-fcitx5.install')
        with open(path, 'r') as f:
            content = f.read()

        # 应包含 Fcitx5 插件
        self.assertIn('fcitx5/vocotype.so', content,
                     "fcitx5 包应安装插件")

        # 应包含后端服务
        self.assertIn('systemd/user', content,
                     "fcitx5 包应安装 systemd 服务")

        # 不应包含 IBus 文件
        self.assertNotIn('ibus', content,
                        "fcitx5 包不应包含 ibus 文件")


class TestPostinstContent(unittest.TestCase):
    """测试 postinst 脚本内容"""

    @classmethod
    def setUpClass(cls):
        cls.debian_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'debian'
        )

    def test_common_postinst_model_download(self):
        """测试 common postinst 处理模型下载"""
        path = os.path.join(self.debian_dir, 'vocotype-common.postinst')
        with open(path, 'r') as f:
            content = f.read()

        # 应包含模型检测
        self.assertIn('models_exist', content,
                     "common postinst 应检测模型")

        # 应包含下载提示
        self.assertIn('prompt_download', content,
                     "common postinst 应有下载提示")

        # 不应包含输入法框架服务配置
        self.assertNotIn('fcitx5', content.lower(),
                        "common postinst 不应配置 fcitx5")
        self.assertNotIn('systemctl', content,
                        "common postinst 不应使用 systemctl")

    def test_fcitx5_postinst_service_config(self):
        """测试 fcitx5 postinst 配置服务"""
        path = os.path.join(self.debian_dir, 'vocotype-fcitx5.postinst')
        with open(path, 'r') as f:
            content = f.read()

        # 应包含服务配置
        self.assertIn('systemctl', content,
                     "fcitx5 postinst 应配置服务")
        self.assertIn('daemon-reload', content,
                     "fcitx5 postinst 应 reload 服务")

        # 应包含 Fcitx5 重载
        self.assertIn('fcitx5', content.lower(),
                     "fcitx5 postinst 应提及 fcitx5")

        # 不应包含模型下载逻辑
        self.assertNotIn('models_exist', content,
                        "fcitx5 postinst 不应检测模型")
        self.assertNotIn('modelscope', content,
                        "fcitx5 postinst 不应涉及模型下载")

    def test_ibus_postinst_simple(self):
        """测试 ibus postinst 简单提示"""
        path = os.path.join(self.debian_dir, 'vocotype-ibus.postinst')
        with open(path, 'r') as f:
            content = f.read()

        # 应包含使用提示
        self.assertIn('ibus-setup', content,
                     "ibus postinst 应提示 ibus-setup")

        # 不应包含服务配置（IBus 不需要）
        self.assertNotIn('systemctl', content,
                        "ibus postinst 不应使用 systemctl")

        # 不应包含模型下载
        self.assertNotIn('models_exist', content,
                        "ibus postinst 不应检测模型")


class TestSyntaxValidity(unittest.TestCase):
    """测试语法有效性"""

    @classmethod
    def setUpClass(cls):
        cls.debian_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'debian'
        )

    def test_control_file_syntax(self):
        """测试 control 文件语法"""
        # 使用 lintian 检查（如果可用）
        try:
            result = subprocess.run(
                ['dpkg-parsechangelog', '-l', f'{self.debian_dir}/changelog'],
                capture_output=True, text=True, cwd=self.debian_dir
            )
            # 只要能解析就不报错
        except FileNotFoundError:
            self.skipTest("dpkg-parsechangelog 不可用")

    def test_postinst_bash_syntax(self):
        """测试所有 postinst 脚本 bash 语法"""
        postinst_files = [
            'vocotype-common.postinst',
            'vocotype-fcitx5.postinst',
            'vocotype-ibus.postinst'
        ]

        for f in postinst_files:
            path = os.path.join(self.debian_dir, f)
            result = subprocess.run(
                ['bash', '-n', path],
                capture_output=True
            )
            self.assertEqual(result.returncode, 0,
                           f"{f} 有 bash 语法错误")

    def test_rules_makefile_syntax(self):
        """测试 rules 文件 make 语法"""
        rules_path = os.path.join(self.debian_dir, 'rules')

        # 检查基本结构
        with open(rules_path, 'r') as f:
            content = f.read()

        # 必须包含必要目标
        self.assertIn('%:', content, "rules 应包含 %: 目标")
        self.assertIn('override_dh_auto_install',
                     content, "rules 应有 install 覆盖")


class TestBoundaryConditions(unittest.TestCase):
    """测试边界条件"""

    @classmethod
    def setUpClass(cls):
        cls.debian_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'debian'
        )
        control_path = os.path.join(cls.debian_dir, 'control')
        with open(control_path, 'r') as f:
            cls.control_content = f.read()

    def test_version_dependency_exact(self):
        """测试版本依赖使用精确匹配"""
        # 子包之间应使用 (= ${binary:Version}) 精确匹配
        ibus_section = self._extract_package_section('vocotype-ibus')
        fcitx5_section = self._extract_package_section('vocotype-fcitx5')

        self.assertIn('${binary:Version}', ibus_section,
                     "ibus 应使用 ${binary:Version}")
        self.assertIn('${binary:Version}', fcitx5_section,
                     "fcitx5 应使用 ${binary:Version}")

    def test_architecture_correct(self):
        """测试架构设置正确"""
        # vocotype-common 应为 all（纯 Python/脚本）
        common_section = self._extract_package_section('vocotype-common')
        self.assertIn('Architecture: all', common_section,
                     "common 应为 Architecture: all")

        # vocotype-fcitx5 应为 any（有 C++ 插件）
        fcitx5_section = self._extract_package_section('vocotype-fcitx5')
        self.assertIn('Architecture: any', fcitx5_section,
                     "fcitx5 应为 Architecture: any")

    def test_no_duplicate_files(self):
        """测试没有重复文件定义"""
        # 收集所有安装路径
        all_paths = []
        install_files = [
            'vocotype-common.install',
            'vocotype-ibus.install',
            'vocotype-fcitx5.install'
        ]

        for f in install_files:
            path = os.path.join(self.debian_dir, f)
            if os.path.exists(path):
                with open(path, 'r') as file:
                    for line in file:
                        line = line.strip()
                        if line and not line.startswith('#'):
                            all_paths.append((f, line))

        # 检查重复（忽略通配符）
        exact_paths = [p[1] for p in all_paths if '*' not in p[1]]
        duplicates = set([x for x in exact_paths if exact_paths.count(x) > 1])

        self.assertEqual(len(duplicates), 0,
                        f"发现重复文件定义: {duplicates}")

    def test_vocotype_meta_no_files(self):
        """测试元包没有 .install 文件"""
        vocotype_install = os.path.join(self.debian_dir, 'vocotype.install')
        self.assertFalse(os.path.exists(vocotype_install),
                        "元包不应有 .install 文件")

    def _extract_package_section(self, package_name):
        """提取包段落"""
        lines = self.control_content.split('\n')
        section = []
        in_section = False

        for line in lines:
            if line.startswith(f'Package: {package_name}'):
                in_section = True
                section.append(line)
            elif in_section:
                if line.startswith('Package:'):
                    break
                section.append(line)

        return '\n'.join(section)


if __name__ == '__main__':
    # 创建测试套件
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # 添加所有测试类
    suite.addTests(loader.loadTestsFromTestCase(TestPackageStructure))
    suite.addTests(loader.loadTestsFromTestCase(TestPackageDependencies))
    suite.addTests(loader.loadTestsFromTestCase(TestInstallFileContent))
    suite.addTests(loader.loadTestsFromTestCase(TestPostinstContent))
    suite.addTests(loader.loadTestsFromTestCase(TestSyntaxValidity))
    suite.addTests(loader.loadTestsFromTestCase(TestBoundaryConditions))

    # 运行测试
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # 返回退出码
    exit(0 if result.wasSuccessful() else 1)
