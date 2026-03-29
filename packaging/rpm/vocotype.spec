Name:           vocotype
Version:        2.2.0
Release:        1%{?dist}
Summary:        Linux 离线语音输入法

License:        Proprietary
URL:            https://github.com/LeonardNJU/VocoType-linux
Source0:        %{url}/archive/v%{version}/%{name}-%{version}.tar.gz

BuildRequires:  cmake >= 3.10
BuildRequires:  gcc-c++
BuildRequires:  fcitx5-devel
BuildRequires:  nlohmann-json-devel
BuildRequires:  python3-devel >= 3.11
BuildRequires:  python3-devel < 3.13
BuildRequires:  python3-pip
BuildRequires:  python3-wheel
BuildRequires:  python3-setuptools
BuildRequires:  python3-build
BuildRequires:  portaudio-devel
BuildRequires:  gobject-introspection-devel
BuildRequires:  pkgconfig

Requires:       python3 >= 3.11
Requires:       python3 < 3.13
Requires:       python3-pip
Requires:       python3-gobject
Requires:       portaudio
Requires:       ibus
Requires:       fcitx5
Requires:       fcitx5-gtk
Requires:       fcitx5-qt

Recommends:     librime

%description
VoCoType 是基于 FunASR 的 Linux 离线语音输入法，
支持 IBus 和 Fcitx5 两大输入法框架。

特性：
  - 100% 离线，隐私无忧
  - 旗舰级 FunASR Paraformer 识别引擎
  - PTT 按键说话 (F9)
  - 中英混合输入
  - 可选 Rime 拼音集成

%prep
%autosetup -n vocotype-%{version}

%build
# 构建 Fcitx5 C++ addon
cd fcitx5/addon
mkdir -p build && cd build
%cmake .. -DCMAKE_BUILD_TYPE=Release
%cmake_build
cd ../../..

# 构建 Python 包
%py3_build

%install
# 安装 C++ addon
cd fcitx5/addon/build
%cmake_install
cd ../../..

# 安装 Python 包
%py3_install

# 安装 Fcitx5 配置
install -Dm644 fcitx5/data/vocotype.conf %{buildroot}%{_datadir}/fcitx5/addon/vocotype.conf
install -Dm644 fcitx5/data/vocotype-addon.conf %{buildroot}%{_datadir}/fcitx5/inputmethod/vocotype.conf

# 安装 IBus 组件
install -Dm644 ibus/vocotype.xml %{buildroot}%{_datadir}/ibus/component/vocotype.xml
install -Dm755 ibus/start-vocotype.sh %{buildroot}%{_libdir}/vocotype/ibus/start-vocotype.sh

# 安装应用数据
mkdir -p %{buildroot}%{_datadir}/vocotype
cp -r app %{buildroot}%{_datadir}/vocotype/

# 安装 systemd 用户服务
install -Dm644 packaging/systemd/vocotype-fcitx5-backend.service %{buildroot}%{_userunitdir}/vocotype-fcitx5-backend.service

%files
%license LICENSE
%doc readme.md CHANGELOG.md

# Python 包
%{python3_sitelib}/vocotype*
%{python3_sitelib}/app/
%{python3_sitelib}/ibus/
%{python3_sitelib}/fcitx5/

# 可执行文件
%{_bindir}/vocotype-ibus
%{_bindir}/vocotype-fcitx5-addon
%{_bindir}/vocotype-download-models

# Fcitx5 addon
%{_libdir}/fcitx5/vocotype.so
%{_datadir}/fcitx5/addon/vocotype.conf
%{_datadir}/fcitx5/inputmethod/vocotype.conf

# IBus
%{_datadir}/ibus/component/vocotype.xml
%{_libdir}/vocotype/

# 应用数据
%{_datadir}/vocotype/

# Systemd
%{_userunitdir}/vocotype-fcitx5-backend.service

%post
echo "=========================================="
echo "VoCoType 语音输入法安装完成"
echo "=========================================="
echo ""
echo "使用方法："
echo "  1. IBus: 重新登录或运行 'ibus restart'"
echo "  2. Fcitx5: 运行 'fcitx5 -r' 重启"
echo ""
echo "首次使用需要下载模型："
echo "  vocotype-download-models"
echo ""
echo "快捷键：按住 F9 说话，松开自动输入"
echo "=========================================="

%postun
# 清理配置（可选）

%changelog
* Fri Mar 28 2025 Leonard Li <leo@lsamc.website> - 2.1.3-1
- Initial RPM release
- IBus and Fcitx5 dual framework support
- FunASR offline speech recognition
