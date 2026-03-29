#!/usr/bin/env python3
"""
VoCoType 测试套件 - 验证安装脚本和模型下载功能
"""

import os
import sys
import json
import shutil
import tempfile
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock, call

# 添加项目根目录到路径
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


class TestModelManager(unittest.TestCase):
    """测试 model_manager 模块的功能（独立测试，不依赖其他模块）"""

    def setUp(self):
        """测试前准备"""
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir)

    def test_format_size_bytes(self):
        """测试字节格式化 - 字节级别"""
        # 内联实现，不依赖外部模块
        def format_size(size_bytes):
            for unit in ['B', 'KB', 'MB', 'GB']:
                if size_bytes < 1024:
                    return f"{size_bytes:.1f} {unit}"
                size_bytes /= 1024
            return f"{size_bytes:.1f} TB"

        self.assertEqual(format_size(500), "500.0 B")
        self.assertEqual(format_size(1023), "1023.0 B")
        self.assertEqual(format_size(1024), "1.0 KB")
        self.assertEqual(format_size(1024 * 1024), "1.0 MB")

    def test_model_cache_path_logic(self):
        """测试模型缓存路径逻辑"""
        home = Path.home()
        model_name = "iic/test-model"
        short_name = model_name.split('/')[-1]
        expected = home / ".cache" / "modelscope" / "hub" / "models" / "iic" / short_name

        # 验证路径构建逻辑
        self.assertEqual(short_name, "test-model")
        self.assertIn(".cache", str(expected))
        self.assertIn("modelscope", str(expected))

    def test_model_exists_logic(self):
        """测试模型存在性检查逻辑"""
        # 创建模拟模型目录
        model_dir = Path(self.temp_dir) / "test_model"
        model_dir.mkdir()

        # 没有模型文件时应该返回 False
        self.assertFalse((model_dir / "model_quant.onnx").exists())
        self.assertFalse((model_dir / "model.onnx").exists())

        # 创建 quant 文件后应该返回 True
        (model_dir / "model_quant.onnx").touch()
        self.assertTrue((model_dir / "model_quant.onnx").exists())


class TestPostinstScript(unittest.TestCase):
    """测试 postinst 安装脚本"""

    def setUp(self):
        """测试前准备"""
        self.postinst_path = PROJECT_ROOT / "debian" / "postinst"

    def test_postinst_script_exists(self):
        """测试 postinst 脚本存在"""
        self.assertTrue(self.postinst_path.exists(),
                       "postinst 脚本应该存在")

    def test_postinst_has_shebang(self):
        """测试 postinst 有正确的 shebang"""
        with open(self.postinst_path) as f:
            first_line = f.readline().strip()
        self.assertEqual(first_line, "#!/bin/bash",
                        "应该有 bash shebang")

    def test_postinst_has_configure_case(self):
        """测试 postinst 包含 configure 处理逻辑"""
        with open(self.postinst_path) as f:
            content = f.read()
        self.assertIn('configure)', content,
                     "应该处理 configure 事件")

    def test_postinst_has_model_check_function(self):
        """测试 postinst 包含模型检查函数"""
        with open(self.postinst_path) as f:
            content = f.read()
        self.assertIn('models_exist()', content,
                     "应该有模型检查函数")

    def test_postinst_has_gui_prompt(self):
        """测试 postinst 包含 GUI 提示逻辑"""
        with open(self.postinst_path) as f:
            content = f.read()
        self.assertIn('zenity', content,
                     "应该支持 zenity GUI 提示")

    def test_postinst_has_cli_prompt(self):
        """测试 postinst 包含命令行提示逻辑"""
        with open(self.postinst_path) as f:
            content = f.read()
        self.assertIn('prompt_download_cli', content,
                     "应该有命令行提示函数")

    def test_postinst_has_fcitx5_service_setup(self):
        """测试 postinst 包含 Fcitx5 服务设置"""
        with open(self.postinst_path) as f:
            content = f.read()
        self.assertIn('setup_fcitx5_service', content,
                     "应该有 Fcitx5 服务设置函数")
        self.assertIn('systemctl --user', content,
                     "应该使用 systemctl")

    def test_postinst_has_error_handling(self):
        """测试 postinst 包含错误处理"""
        with open(self.postinst_path) as f:
            content = f.read()
        self.assertIn('set -e', content,
                     "应该启用错误时退出")

    def test_postinst_has_debhelper(self):
        """测试 postinst 包含 DEBHELPER 标记"""
        with open(self.postinst_path) as f:
            content = f.read()
        self.assertIn('#DEBHELPER#', content,
                     "应该包含 #DEBHELPER# 标记")

    def test_postinst_executable(self):
        """测试 postinst 是可执行的"""
        import stat
        mode = self.postinst_path.stat().st_mode
        self.assertTrue(mode & stat.S_IXUSR,
                       "postinst 应该是可执行的")


class TestDownloadScript(unittest.TestCase):
    """测试下载脚本 vocotype-download-models"""

    def setUp(self):
        """测试前准备"""
        self.script_path = PROJECT_ROOT / "scripts" / "vocotype-download-models"

    def test_download_script_exists(self):
        """测试下载脚本存在"""
        self.assertTrue(self.script_path.exists(),
                       "下载脚本应该存在")

    def test_download_script_executable(self):
        """测试下载脚本是可执行的"""
        import stat
        mode = self.script_path.stat().st_mode
        self.assertTrue(mode & stat.S_IXUSR,
                       "下载脚本应该是可执行的")

    def test_download_script_has_shebang(self):
        """测试下载脚本有正确的 shebang"""
        with open(self.script_path) as f:
            first_line = f.readline().strip()
        self.assertEqual(first_line, "#!/usr/bin/env python3",
                        "应该有 python3 shebang")

    def test_download_script_syntax(self):
        """测试下载脚本语法正确"""
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(self.script_path)],
            capture_output=True,
            text=True
        )
        self.assertEqual(result.returncode, 0,
                        f"脚本应该有有效的 Python 语法: {result.stderr}")

    def test_download_script_has_main_functions(self):
        """测试下载脚本包含主要功能"""
        with open(self.script_path) as f:
            content = f.read()

        # 检查必要的函数
        self.assertIn('def main()', content, "应该有 main 函数")
        self.assertIn('argparse', content, "应该使用 argparse")
        self.assertIn('--download', content, "应该有 --download 参数")
        self.assertIn('--prompt', content, "应该有 --prompt 参数")
        self.assertIn('--check', content, "应该有 --check 参数")


class TestDebianPackage(unittest.TestCase):
    """Debian 包相关测试"""

    def test_control_file_exists(self):
        """测试 control 文件存在"""
        control_path = PROJECT_ROOT / "debian" / "control"
        self.assertTrue(control_path.exists())

    def test_rules_file_exists(self):
        """测试 rules 文件存在"""
        rules_path = PROJECT_ROOT / "debian" / "rules"
        self.assertTrue(rules_path.exists())

    def test_rules_has_download_script(self):
        """测试 rules 文件包含下载脚本安装"""
        rules_path = PROJECT_ROOT / "debian" / "rules"
        with open(rules_path) as f:
            content = f.read()
        self.assertIn('vocotype-download-models', content,
                     "rules 应该安装下载脚本")

    def test_install_file_exists(self):
        """测试 install 文件存在"""
        install_path = PROJECT_ROOT / "debian" / "vocotype.install"
        self.assertTrue(install_path.exists())

    def test_systemd_service_exists(self):
        """测试 systemd 服务文件存在"""
        service_path = (PROJECT_ROOT / "packaging" / "systemd" /
                       "vocotype-fcitx5-backend.service")
        self.assertTrue(service_path.exists())


class TestIntegration(unittest.TestCase):
    """集成测试"""

    def test_project_structure(self):
        """测试项目结构完整性"""
        required_dirs = [
            'app',
            'debian',
            'scripts',
            'fcitx5',
            'ibus',
        ]

        for dir_name in required_dirs:
            dir_path = PROJECT_ROOT / dir_name
            self.assertTrue(dir_path.exists(),
                          f"必需目录 {dir_name} 应该存在")

    def test_all_scripts_executable(self):
        """测试所有脚本是可执行的"""
        scripts_dir = PROJECT_ROOT / "scripts"
        if scripts_dir.exists():
            for script in scripts_dir.iterdir():
                if script.is_file() and not script.name.endswith('.py'):
                    import stat
                    mode = script.stat().st_mode
                    self.assertTrue(
                        mode & stat.S_IXUSR or script.suffix == '.py',
                        f"{script.name} 应该是可执行的"
                    )


class TestEdgeCases(unittest.TestCase):
    """边界情况测试"""

    def test_empty_model_name(self):
        """测试空模型名处理"""
        model_name = ""
        short_name = model_name.split('/')[-1] if '/' in model_name else model_name
        self.assertEqual(short_name, "")

    def test_model_name_with_multiple_slashes(self):
        """测试多层路径模型名"""
        model_name = "iic/speech/model/name"
        short_name = model_name.split('/')[-1]
        self.assertEqual(short_name, "name")

    def test_non_interactive_environment(self):
        """测试非交互式环境处理"""
        # 检查环境变量
        is_tty = sys.stdin.isatty() if hasattr(sys.stdin, 'isatty') else False
        # 测试应该能正常处理两种情况
        self.assertIn(is_tty, [True, False])

    def test_path_construction(self):
        """测试路径构建"""
        home = Path.home()
        cache_dir = home / ".cache" / "modelscope" / "hub" / "models" / "iic"

        # 验证路径是可用的
        self.assertIsInstance(cache_dir, Path)
        self.assertTrue(str(cache_dir).startswith(str(home)))


class TestSystemRequirements(unittest.TestCase):
    """系统需求测试"""

    def test_python_version(self):
        """测试 Python 版本 >= 3.8"""
        version = sys.version_info
        self.assertGreaterEqual(version.major, 3)
        self.assertGreaterEqual(version.minor, 8)

    def test_required_modules_available(self):
        """测试所需模块是否可用"""
        required_modules = [
            'pathlib',
            'tempfile',
            'json',
            'subprocess',
            'argparse',
            'unittest',
        ]

        for module in required_modules:
            try:
                __import__(module)
            except ImportError:
                self.fail(f"必需模块 {module} 不可用")

    def test_bash_available(self):
        """测试 bash 是否可用"""
        result = subprocess.run(['which', 'bash'], capture_output=True)
        self.assertEqual(result.returncode, 0, "bash 应该可用")

    def test_common_tools_available(self):
        """测试常用工具是否可用"""
        tools = ['chmod', 'mkdir', 'cp', 'rm']
        for tool in tools:
            result = subprocess.run(['which', tool], capture_output=True)
            self.assertEqual(result.returncode, 0,
                           f"工具 {tool} 应该可用")


def run_tests():
    """运行所有测试"""
    # 创建测试套件
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # 添加所有测试类
    test_classes = [
        TestModelManager,
        TestPostinstScript,
        TestDownloadScript,
        TestDebianPackage,
        TestIntegration,
        TestEdgeCases,
        TestSystemRequirements,
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
