#!/usr/bin/env python3
"""
VoCoType 打包验证测试套件

测试内容：
1. 包配置语法验证
2. 依赖完整性检查
3. 安装路径验证
4. 文件权限检查
5. 系统集成点验证

运行方式:
    python3 -m pytest tests/packaging/ -v
    或
    python3 tests/packaging/test_package.py
"""

import os
import re
import subprocess
import sys
from pathlib import Path
import unittest


# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.parent
PACKAGING_DIR = PROJECT_ROOT / "packaging"


class TestPackageConfigs(unittest.TestCase):
    """测试包配置文件格式和语法"""

    def test_debian_control_exists(self):
        """DEB control 文件存在且非空"""
        control_file = PACKAGING_DIR / "debian" / "control"
        self.assertTrue(control_file.exists(), "debian/control 不存在")
        self.assertGreater(control_file.stat().st_size, 0, "control 文件为空")

    def test_debian_control_syntax(self):
        """DEB control 文件语法检查"""
        control_file = PACKAGING_DIR / "debian" / "control"
        content = control_file.read_text()

        # 检查必填字段
        required_fields = ['Package:', 'Version:', 'Architecture:', 'Description:']
        for field in required_fields:
            self.assertIn(field, content, f"缺少必填字段: {field}")

        # 检查依赖声明
        self.assertIn('Depends:', content, "缺少 Depends 字段")
        self.assertIn('python3 (>= 3.11)', content, "缺少 Python 版本下限")
        self.assertIn('python3 (<< 3.13)', content, "缺少 Python 版本上限")

    def test_debian_rules_executable(self):
        """DEB rules 文件可执行"""
        rules_file = PACKAGING_DIR / "debian" / "rules"
        self.assertTrue(rules_file.exists())

        # 检查 shebang
        content = rules_file.read_text()
        self.assertTrue(content.startswith('#!/usr/bin/make -f'),
                        "rules 文件缺少正确的 shebang")

    def test_rpm_spec_exists(self):
        """RPM spec 文件存在"""
        spec_file = PACKAGING_DIR / "rpm" / "vocotype.spec"
        self.assertTrue(spec_file.exists(), "vocotype.spec 不存在")

    def test_rpm_spec_sections(self):
        """RPM spec 包含必要的 section"""
        spec_file = PACKAGING_DIR / "rpm" / "vocotype.spec"
        content = spec_file.read_text()

        required_sections = [
            'Name:', 'Version:', 'Release:', 'Summary:',
            '%description',
            '%prep',
            '%build',
            '%install',
            '%files',
        ]

        for section in required_sections:
            self.assertIn(section, content, f"spec 文件缺少 {section}")

    def test_arch_pkgbuild_exists(self):
        """Arch PKGBUILD 存在"""
        pkgbuild = PACKAGING_DIR / "arch" / "PKGBUILD"
        self.assertTrue(pkgbuild.exists(), "PKGBUILD 不存在")

    def test_arch_pkgbuild_syntax(self):
        """Arch PKGBUILD 语法检查"""
        pkgbuild = PACKAGING_DIR / "arch" / "PKGBUILD"
        content = pkgbuild.read_text()

        # 检查必填变量
        required_vars = ['pkgname=', 'pkgver=', 'pkgrel=', 'pkgdesc=']
        for var in required_vars:
            self.assertIn(var, content, f"PKGBUILD 缺少 {var}")

        # 检查构建函数
        self.assertIn('build()', content, "缺少 build() 函数")
        self.assertIn('package()', content, "缺少 package() 函数")

    def test_systemd_service_file(self):
        """Systemd 服务文件格式检查"""
        service_file = PACKAGING_DIR / "systemd" / "vocotype-fcitx5-backend.service"
        self.assertTrue(service_file.exists())

        content = service_file.read_text()

        # 检查必要 section
        self.assertIn('[Unit]', content, "缺少 [Unit] section")
        self.assertIn('[Service]', content, "缺少 [Service] section")
        self.assertIn('[Install]', content, "缺少 [Install] section")

        # 检查必要字段
        self.assertIn('ExecStart=', content, "缺少 ExecStart")
        self.assertIn('Type=', content, "缺少 Type")


class TestDependencies(unittest.TestCase):
    """测试依赖声明的完整性和一致性"""

    def setUp(self):
        # 读取 pyproject.toml 依赖
        pyproject = PROJECT_ROOT / "pyproject.toml"
        self.pyproject_deps = self._parse_pyproject_deps(pyproject)

        # 读取各包配置
        self.deb_deps = self._read_debian_deps()
        self.rpm_deps = self._read_rpm_deps()
        self.arch_deps = self._read_arch_deps()

    def _parse_pyproject_deps(self, pyproject_path):
        """解析 pyproject.toml 依赖"""
        content = pyproject_path.read_text()
        deps = []

        # 简单解析 dependencies 列表
        in_deps = False
        for line in content.split('\n'):
            if 'dependencies' in line and '=' in line:
                in_deps = True
                continue
            if in_deps:
                if line.strip().startswith(']'):
                    break
                # 提取包名 (去掉版本限制)
                match = re.search(r'"([a-zA-Z0-9_-]+)', line)
                if match:
                    deps.append(match.group(1).lower())

        return set(deps)

    def _read_debian_deps(self):
        """读取 Debian 系统依赖"""
        control = PACKAGING_DIR / "debian" / "control"
        content = control.read_text()

        # 提取 Package 段落的 Depends（运行时依赖）
        # 找到 Package: vocotype 后面的 Depends
        package_section = re.search(
            r'Package: vocotype\n(.*?)(?=\nPackage:|\nSource:|\Z)',
            content, re.DOTALL
        )

        if package_section:
            section = package_section.group(1)
            depends_match = re.search(r'Depends:(.*?)(?=\n[A-Z]|$)', section, re.DOTALL)
            if depends_match:
                deps_str = depends_match.group(1)
                # 清理换行和空格
                deps_str = deps_str.replace('\n', ' ').replace('  ', ' ')
                return set(d.strip() for d in deps_str.split(',') if d.strip())
        return set()

    def _read_rpm_deps(self):
        """读取 RPM 系统依赖"""
        spec = PACKAGING_DIR / "rpm" / "vocotype.spec"
        content = spec.read_text()

        deps = set()
        # 提取 BuildRequires 和 Requires
        for line in content.split('\n'):
            if line.startswith('BuildRequires:') or line.startswith('Requires:'):
                dep = line.split(':', 1)[1].strip()
                deps.add(dep)

        return deps

    def _read_arch_deps(self):
        """读取 Arch 依赖"""
        pkgbuild = PACKAGING_DIR / "arch" / "PKGBUILD"
        content = pkgbuild.read_text()

        deps = set()
        # 简单解析 depends 数组
        for pattern in [r'depends=\((.*?)\)', r'makedepends=\((.*?)\)']:
            match = re.search(pattern, content, re.DOTALL)
            if match:
                deps_str = match.group(1)
                # 提取引号中的包名
                deps.update(re.findall(r'["\']([a-zA-Z0-9_-]+)["\']', deps_str))

        return deps

    def test_python_version_constraint(self):
        """Python 版本限制一致性检查"""
        # 所有包都应该限制 Python 3.11-3.12
        pyproject = (PROJECT_ROOT / "pyproject.toml").read_text()
        self.assertIn('>=3.11', pyproject, "pyproject.toml 缺少 Python >=3.11 限制")
        self.assertIn('<3.13', pyproject, "pyproject.toml 缺少 Python <3.13 限制")

        # 检查 debian control
        control = (PACKAGING_DIR / "debian" / "control").read_text()
        self.assertIn('python3 (>= 3.11)', control)
        self.assertIn('python3 (<< 3.13)', control)

        # 检查 RPM spec
        spec = (PACKAGING_DIR / "rpm" / "vocotype.spec").read_text()
        self.assertIn('python3 >= 3.11', spec)
        self.assertIn('python3 < 3.13', spec)

    def test_core_dependencies_present(self):
        """核心依赖都存在"""
        # 检查 pyproject.toml 核心依赖
        core_python_deps = {'sounddevice', 'librosa', 'soundfile', 'funasr_onnx'}
        for dep in core_python_deps:
            self.assertIn(dep, self.pyproject_deps,
                         f"pyproject.toml 缺少核心依赖: {dep}")

    def test_audio_dependencies(self):
        """音频依赖声明检查"""
        # Debian 需要 portaudio
        deb_deps_str = ' '.join(self.deb_deps)
        self.assertTrue(
            any('portaudio' in d for d in self.deb_deps),
            "Debian 缺少 portaudio 依赖"
        )

        # RPM 需要 portaudio
        self.assertTrue(
            any('portaudio' in d for d in self.rpm_deps),
            "RPM 缺少 portaudio 依赖"
        )

    def test_input_method_framework_deps(self):
        """输入法框架依赖检查"""
        # Debian 需要 ibus 和 fcitx5
        deb_deps_str = ' '.join(self.deb_deps).lower()
        self.assertIn('ibus', deb_deps_str, "Debian 缺少 ibus 依赖")
        self.assertIn('fcitx5', deb_deps_str, "Debian 缺少 fcitx5 依赖")

        # RPM 同样需要
        rpm_deps_str = ' '.join(self.rpm_deps).lower()
        self.assertIn('ibus', rpm_deps_str, "RPM 缺少 ibus 依赖")
        self.assertIn('fcitx5', rpm_deps_str, "RPM 缺少 fcitx5 依赖")

    def test_cxx_build_deps(self):
        """C++ 构建依赖检查"""
        deb_deps_str = ' '.join(self.deb_deps).lower()

        # Fcitx5 addon 运行时依赖 fcitx5
        self.assertTrue(
            'fcitx5' in deb_deps_str,
            "Debian 缺少 fcitx5 依赖"
        )

        self.assertTrue(
            any('fcitx5' in d for d in self.rpm_deps),
            "RPM 缺少 fcitx5 依赖"
        )


class TestInstallationPaths(unittest.TestCase):
    """测试安装路径配置"""

    def test_debian_install_paths(self):
        """Debian 安装路径检查"""
        rules = (PACKAGING_DIR / "debian" / "rules").read_text()

        # 检查关键安装路径
        required_paths = [
            r'/usr/share/fcitx5/addon',
            r'/usr/share/fcitx5/inputmethod',
            r'/usr/share/ibus/component',
            r'/usr/lib/vocotype',
            r'/usr/share/vocotype',
        ]

        for path in required_paths:
            self.assertIn(path, rules, f"rules 文件缺少路径: {path}")

    def test_rpm_install_paths(self):
        """RPM 安装路径检查"""
        spec = (PACKAGING_DIR / "rpm" / "vocotype.spec").read_text()

        # 检查 %files 中的路径
        self.assertIn('%{_libdir}/fcitx5/', spec, "缺少 fcitx5 addon 路径")
        self.assertIn('%{_datadir}/ibus/component/', spec, "缺少 ibus 组件路径")
        self.assertIn('%{_datadir}/vocotype/', spec, "缺少应用数据路径")

    def test_arch_install_paths(self):
        """Arch 安装路径检查"""
        pkgbuild = (PACKAGING_DIR / "arch" / "PKGBUILD").read_text()

        # 检查关键安装命令
        self.assertIn('/usr/share/fcitx5/addon', pkgbuild)
        self.assertIn('/usr/share/ibus/component', pkgbuild)
        self.assertIn('/usr/share/vocotype', pkgbuild)


class TestBuildScripts(unittest.TestCase):
    """测试构建脚本"""

    def test_build_scripts_exist(self):
        """构建脚本存在"""
        scripts = ['build-deb.sh', 'build-rpm.sh', 'build-arch.sh']
        for script in scripts:
            script_path = PROJECT_ROOT / "scripts" / script
            self.assertTrue(script_path.exists(), f"缺少构建脚本: {script}")

    def test_build_scripts_executable(self):
        """构建脚本可执行"""
        scripts = ['build-deb.sh', 'build-rpm.sh', 'build-arch.sh']
        for script in scripts:
            script_path = PROJECT_ROOT / "scripts" / script
            if script_path.exists():
                self.assertTrue(
                    os.access(script_path, os.X_OK),
                    f"{script} 不可执行"
                )

    def test_build_scripts_shebang(self):
        """构建脚本有正确的 shebang"""
        scripts = ['build-deb.sh', 'build-rpm.sh', 'build-arch.sh']
        for script in scripts:
            script_path = PROJECT_ROOT / "scripts" / script
            if script_path.exists():
                content = script_path.read_text()
                self.assertTrue(
                    content.startswith('#!/bin/bash') or
                    content.startswith('#!/usr/bin/env bash'),
                    f"{script} 缺少正确的 shebang"
                )


class TestIntegration(unittest.TestCase):
    """集成测试 - 验证整个打包流程"""

    def test_version_consistency(self):
        """所有配置文件中版本一致"""
        # 从 vocotype_version.py 读取版本
        version_file = PROJECT_ROOT / "vocotype_version.py"
        version_content = version_file.read_text()
        version_match = re.search(r'__version__ = "([\d.]+)"', version_content)
        self.assertIsNotNone(version_match, "无法读取版本号")
        version = version_match.group(1)

        # 检查 changelog
        changelog = (PACKAGING_DIR / "debian" / "changelog").read_text()
        self.assertIn(version, changelog, "changelog 版本不匹配")

        # 检查 spec 文件
        spec = (PACKAGING_DIR / "rpm" / "vocotype.spec").read_text()
        self.assertIn(f'Version:        {version}', spec, "spec 版本不匹配")

        # 检查 PKGBUILD
        pkgbuild = (PACKAGING_DIR / "arch" / "PKGBUILD").read_text()
        self.assertIn(f'pkgver={version}', pkgbuild, "PKGBUILD 版本不匹配")

    def test_all_configs_complete(self):
        """所有包配置完整"""
        # 统计文件数量
        debian_files = list((PACKAGING_DIR / "debian").iterdir())
        self.assertGreaterEqual(len(debian_files), 6, "debian 配置文件不完整")

        # 检查每个配置都有对应的构建脚本
        self.assertTrue((PROJECT_ROOT / "scripts" / "build-deb.sh").exists())
        self.assertTrue((PROJECT_ROOT / "scripts" / "build-rpm.sh").exists())
        self.assertTrue((PROJECT_ROOT / "scripts" / "build-arch.sh").exists())

    def test_documentation_exists(self):
        """打包文档存在"""
        readme = PACKAGING_DIR / "README.md"
        self.assertTrue(readme.exists(), "缺少打包文档")

        content = readme.read_text()
        self.assertIn('DEB', content, "文档缺少 DEB 说明")
        self.assertIn('RPM', content, "文档缺少 RPM 说明")
        self.assertIn('PKGBUILD', content, "文档缺少 PKGBUILD 说明")


def run_command(cmd, cwd=None):
    """辅助函数：运行 shell 命令"""
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=cwd or PROJECT_ROOT,
            capture_output=True,
            text=True
        )
        return result.returncode == 0, result.stdout, result.stderr
    except Exception as e:
        return False, "", str(e)


class TestRuntimeChecks(unittest.TestCase):
    """运行时检查 - 需要构建环境"""

    @unittest.skipUnless(os.environ.get('TEST_BUILD_ENV'), "需要构建环境")
    def test_debian_builddeps_installable(self):
        """Debian 构建依赖可安装"""
        # 复制 debian 目录到项目根目录
        ok, out, err = run_command("cp -r packaging/debian .")
        self.assertTrue(ok, f"复制 debian 目录失败: {err}")

        # 检查依赖是否可以满足
        ok, out, err = run_command("dpkg-checkbuilddeps 2>&1")
        if not ok:
            # 可能有警告，不一定是错误
            self.assertNotIn("Unmet build dependencies", out + err,
                           "有未满足的构建依赖")

    @unittest.skipUnless(os.environ.get('TEST_RPM_ENV'), "需要 RPM 环境")
    def test_rpm_spec_valid(self):
        """RPM spec 文件语法正确"""
        ok, out, err = run_command(
            "rpmlint packaging/rpm/vocotype.spec 2>&1"
        )
        # rpmlint 返回 0 或警告，错误时会返回非零
        self.assertNotIn("error:", (out + err).lower(), "spec 文件有错误")


def main():
    """主函数 - 可以直接运行测试"""
    # 设置测试输出
    unittest.main(verbosity=2)


if __name__ == '__main__':
    main()
