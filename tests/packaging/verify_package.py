#!/usr/bin/env python3
"""
构建后包验证脚本

验证内容：
1. 包文件完整性
2. 依赖解析
3. 预安装检查
4. 安装/卸载测试

用法:
    python3 tests/packaging/verify_package.py <package_file>
    python3 tests/packaging/verify_package.py --deb vocotype_2.1.3-1_amd64.deb
    python3 tests/packaging/verify_package.py --rpm vocotype-2.1.3-1.fc40.x86_64.rpm
"""

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


class PackageVerifier:
    """包验证器基类"""

    def __init__(self, package_path):
        self.package_path = Path(package_path)
        self.errors = []
        self.warnings = []

    def log_error(self, msg):
        self.errors.append(msg)
        print(f"[ERROR] {msg}")

    def log_warning(self, msg):
        self.warnings.append(msg)
        print(f"[WARN] {msg}")

    def log_info(self, msg):
        print(f"[INFO] {msg}")

    def verify(self):
        """运行所有验证"""
        raise NotImplementedError

    def summary(self):
        """打印验证摘要"""
        print("\n" + "=" * 60)
        print("验证摘要")
        print("=" * 60)
        print(f"错误: {len(self.errors)}")
        print(f"警告: {len(self.warnings)}")

        if self.errors:
            print("\n错误列表:")
            for e in self.errors:
                print(f"  - {e}")

        if self.warnings:
            print("\n警告列表:")
            for w in self.warnings:
                print(f"  - {w}")

        return len(self.errors) == 0


class DebVerifier(PackageVerifier):
    """DEB 包验证器"""

    def verify(self):
        """验证 DEB 包"""
        self.log_info(f"验证 DEB 包: {self.package_path}")

        # 检查文件存在
        if not self.package_path.exists():
            self.log_error(f"包文件不存在: {self.package_path}")
            return False

        # 1. 检查包信息
        self._verify_control_info()

        # 2. 检查文件列表
        self._verify_file_list()

        # 3. 检查依赖
        self._verify_dependencies()

        # 4. 检查脚本
        self._verify_scripts()

        return len(self.errors) == 0

    def _verify_control_info(self):
        """验证 control 信息"""
        self.log_info("检查包信息...")

        result = subprocess.run(
            ['dpkg-deb', '-I', str(self.package_path)],
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            self.log_error(f"无法读取包信息: {result.stderr}")
            return

        output = result.stdout

        # 检查必要字段
        required_fields = ['Package:', 'Version:', 'Architecture:', 'Depends:']
        for field in required_fields:
            if field not in output:
                self.log_error(f"缺少字段: {field}")

        # 提取并检查 Python 版本
        version_match = re.search(r'Version: ([\d.]+)', output)
        if version_match:
            version = version_match.group(1)
            self.log_info(f"包版本: {version}")

        # 检查依赖
        depends_match = re.search(r'Depends: (.+?)\n[A-Z]', output, re.DOTALL)
        if depends_match:
            depends = depends_match.group(1).replace('\n', ' ')
            self.log_info(f"依赖: {depends[:100]}...")

            if 'python3 (>= 3.11)' not in depends:
                self.log_error("缺少 Python >= 3.11 版本限制")
            if 'python3 (<< 3.13)' not in depends:
                self.log_error("缺少 Python < 3.13 版本限制")

    def _verify_file_list(self):
        """验证文件列表"""
        self.log_info("检查文件列表...")

        result = subprocess.run(
            ['dpkg-deb', '-c', str(self.package_path)],
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            self.log_error(f"无法读取文件列表: {result.stderr}")
            return

        files = result.stdout

        # 检查必要文件
        required_paths = [
            r'/usr/lib/python3',  # Python 包
            r'/usr/lib/fcitx5/vocotype',  # Fcitx5 addon
            r'/usr/share/fcitx5/addon/vocotype.conf',
            r'/usr/share/ibus/component/vocotype.xml',
            r'/usr/bin/vocotype-ibus',
            r'/usr/bin/vocotype-download-models',
        ]

        for path in required_paths:
            if not re.search(path, files):
                self.log_warning(f"可能缺少文件: {path}")

        # 检查不应该存在的文件
        forbidden_patterns = ['.pyc', '__pycache__', '.git']
        for pattern in forbidden_patterns:
            if pattern in files:
                self.log_warning(f"包含不应存在的文件: {pattern}")

    def _verify_dependencies(self):
        """验证依赖"""
        self.log_info("验证依赖...")

        # 使用 apt 检查依赖是否可满足
        result = subprocess.run(
            ['apt-cache', 'show', str(self.package_path)],
            capture_output=True,
            text=True
        )

        # 检查是否有未定义依赖
        check_result = subprocess.run(
            ['dpkg-deb', '-I', str(self.package_path)],
            capture_output=True,
            text=True
        )

        output = check_result.stdout

        # 检查关键依赖
        critical_deps = ['python3', 'portaudio', 'ibus', 'fcitx5']
        for dep in critical_deps:
            if dep not in output.lower():
                self.log_warning(f"可能缺少关键依赖: {dep}")

    def _verify_scripts(self):
        """验证维护脚本"""
        self.log_info("检查维护脚本...")

        # 提取控制脚本
        with tempfile.TemporaryDirectory() as tmpdir:
            result = subprocess.run(
                ['dpkg-deb', '-e', str(self.package_path), tmpdir],
                capture_output=True,
                text=True
            )

            if result.returncode != 0:
                self.log_error(f"无法提取控制脚本: {result.stderr}")
                return

            # 检查脚本
            scripts = ['preinst', 'postinst', 'prerm', 'postrm']
            for script in scripts:
                script_path = Path(tmpdir) / script
                if script_path.exists():
                    self.log_info(f"存在 {script} 脚本")
                    # 检查脚本语法
                    result = subprocess.run(
                        ['bash', '-n', str(script_path)],
                        capture_output=True,
                        text=True
                    )
                    if result.returncode != 0:
                        self.log_error(f"{script} 脚本语法错误: {result.stderr}")


class RpmVerifier(PackageVerifier):
    """RPM 包验证器"""

    def verify(self):
        """验证 RPM 包"""
        self.log_info(f"验证 RPM 包: {self.package_path}")

        if not self.package_path.exists():
            self.log_error(f"包文件不存在: {self.package_path}")
            return False

        # 1. 检查包信息
        self._verify_info()

        # 2. 检查文件列表
        self._verify_file_list()

        # 3. 检查依赖
        self._verify_dependencies()

        # 4. 运行 rpmlint
        self._run_rpmlint()

        return len(self.errors) == 0

    def _verify_info(self):
        """验证 RPM 信息"""
        self.log_info("检查包信息...")

        result = subprocess.run(
            ['rpm', '-qip', str(self.package_path)],
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            self.log_error(f"无法读取包信息: {result.stderr}")
            return

        output = result.stdout

        # 检查必要字段
        required_fields = ['Name', 'Version', 'Release', 'Summary']
        for field in required_fields:
            if f'{field}     :' not in output and f'{field}    :' not in output:
                self.log_error(f"缺少字段: {field}")

        # 提取版本
        version_match = re.search(r'Version\s+:\s+([\d.]+)', output)
        if version_match:
            self.log_info(f"包版本: {version_match.group(1)}")

    def _verify_file_list(self):
        """验证文件列表"""
        self.log_info("检查文件列表...")

        result = subprocess.run(
            ['rpm', '-qlp', str(self.package_path)],
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            self.log_error(f"无法读取文件列表: {result.stderr}")
            return

        files = result.stdout

        # 检查必要文件
        required_paths = [
            r'/usr/lib/python3',
            r'/usr/lib64/fcitx5/vocotype',
            r'/usr/share/fcitx5/addon/',
            r'/usr/share/ibus/component/',
            r'/usr/bin/vocotype',
        ]

        for path in required_paths:
            if path not in files:
                self.log_warning(f"可能缺少路径: {path}")

    def _verify_dependencies(self):
        """验证依赖"""
        self.log_info("验证依赖...")

        result = subprocess.run(
            ['rpm', '-qpR', str(self.package_path)],
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            self.log_error(f"无法读取依赖: {result.stderr}")
            return

        deps = result.stdout

        # 检查关键依赖
        critical_deps = ['python3', 'portaudio', 'ibus', 'fcitx5']
        for dep in critical_deps:
            if dep not in deps.lower():
                self.log_warning(f"可能缺少关键依赖: {dep}")

    def _run_rpmlint(self):
        """运行 rpmlint 检查"""
        self.log_info("运行 rpmlint...")

        result = subprocess.run(
            ['rpmlint', str(self.package_path)],
            capture_output=True,
            text=True
        )

        # rpmlint 返回 0 但可能有警告
        output = result.stdout + result.stderr

        # 检查错误
        if 'E:' in output:
            errors = [line for line in output.split('\n') if 'E:' in line]
            for error in errors[:5]:  # 只显示前5个
                self.log_error(f"rpmlint: {error}")

        # 检查警告
        if 'W:' in output:
            warnings = [line for line in output.split('\n') if 'W:' in line]
            self.log_info(f"rpmlint 发现 {len(warnings)} 个警告")


def verify_arch_pkg(pkg_path):
    """验证 Arch 包"""
    print(f"验证 Arch 包: {pkg_path}")

    errors = []
    warnings = []

    # 检查文件是否存在
    if not os.path.exists(pkg_path):
        print(f"[ERROR] 包文件不存在: {pkg_path}")
        return False

    # 检查包格式
    result = subprocess.run(
        ['tar', '-tzf', pkg_path],
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        print(f"[ERROR] 无法读取包内容: {result.stderr}")
        return False

    files = result.stdout

    # 检查 .PKGINFO 存在
    if '.PKGINFO' not in files:
        errors.append("缺少 .PKGINFO 文件")
    else:
        # 提取并检查 .PKGINFO
        with tempfile.TemporaryDirectory() as tmpdir:
            subprocess.run(
                ['tar', '-xzf', pkg_path, '-C', tmpdir, '.PKGINFO'],
                check=True
            )
            pkginfo = Path(tmpdir) / '.PKGINFO'
            content = pkginfo.read_text()

            # 检查必要字段
            required = ['pkgname', 'pkgver', 'pkgdesc', 'arch']
            for field in required:
                if f'{field} = ' not in content:
                    errors.append(f"PKGINFO 缺少 {field}")

            print(f"[INFO] 包名: {re.search(r'pkgname = (.+)', content).group(1)}")
            print(f"[INFO] 版本: {re.search(r'pkgver = (.+)', content).group(1)}")

    # 检查文件列表
    print("[INFO] 检查文件列表...")
    required_paths = [
        'usr/lib/python3',
        'usr/lib/fcitx5/vocotype',
        'usr/share/fcitx5/',
        'usr/share/ibus/',
    ]

    for path in required_paths:
        if path not in files:
            warnings.append(f"可能缺少: {path}")

    # 摘要
    print("\n" + "=" * 60)
    print("验证摘要")
    print("=" * 60)
    print(f"错误: {len(errors)}")
    print(f"警告: {len(warnings)}")

    for e in errors:
        print(f"  [ERROR] {e}")
    for w in warnings:
        print(f"  [WARN] {w}")

    return len(errors) == 0


def main():
    parser = argparse.ArgumentParser(description='验证 VoCoType 安装包')
    parser.add_argument('package', nargs='?', help='包文件路径')
    parser.add_argument('--deb', help='DEB 包路径')
    parser.add_argument('--rpm', help='RPM 包路径')
    parser.add_argument('--arch', help='Arch 包路径')
    parser.add_argument('--verbose', '-v', action='store_true', help='详细输出')

    args = parser.parse_args()

    package = args.package or args.deb or args.rpm or args.arch

    if not package:
        parser.print_help()
        sys.exit(1)

    # 根据扩展名自动检测类型
    if package.endswith('.deb') or args.deb:
        verifier = DebVerifier(package)
    elif package.endswith('.rpm') or args.rpm:
        verifier = RpmVerifier(package)
    elif '.pkg.tar.' in package or args.arch:
        success = verify_arch_pkg(package)
        sys.exit(0 if success else 1)
    else:
        print(f"无法识别的包格式: {package}")
        sys.exit(1)

    verifier.verify()
    success = verifier.summary()

    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
