#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$ROOT"

fail() { echo "NATIVE_CONTRACT_FAIL: $*" >&2; exit 1; }

# Repository structure is an architectural contract, not a naming preference.
# New root entries require an explicit ADR and an update to this allowlist.
expected_top_level=$(cat <<'EOF'
.github
docs
packaging
resources
scripts
src
web
EOF
)
actual_top_level=$(git ls-files | awk -F/ 'NF > 1 {print $1}' | LC_ALL=C sort -u)
[[ "$actual_top_level" == "$expected_top_level" ]] || {
  printf 'Expected tracked top-level directories:\n%s\nActual:\n%s\n' \
    "$expected_top_level" "$actual_top_level" >&2
  fail "tracked top-level directory set drifted"
}

expected_root_files=$(cat <<'EOF'
.gitignore
CHANGELOG.md
LICENSE
Makefile
README.md
THIRD_PARTY_NOTICES.md
VERSION
flake.lock
flake.nix
EOF
)
actual_root_files=$(git ls-files | awk -F/ 'NF == 1 {print}' | LC_ALL=C sort)
[[ "$actual_root_files" == "$expected_root_files" ]] || {
  printf 'Expected tracked root files:\n%s\nActual:\n%s\n' \
    "$expected_root_files" "$actual_root_files" >&2
  fail "tracked root file set drifted"
}

for legacy in native fcitx5 ibus feedback_service deploy data installers nix site tests tools; do
  [[ ! -e "$legacy" ]] || fail "legacy top-level path returned: $legacy"
done
while IFS= read -r readme; do
  case "$readme" in
    README.md|docs/*) ;;
    *) fail "documentation is scattered outside docs/: $readme" ;;
  esac
done < <(git ls-files '*README.md')

for path in \
  src/common src/core src/desktop \
  src/integrations/fcitx5 src/integrations/ibus src/integrations/macos \
  src/workers/funasr src/services/feedback/deploy \
  packaging/arch packaging/debian packaging/rpm packaging/nix packaging/macos \
  packaging/common packaging/scripts packaging/tests \
  scripts/install scripts/test scripts/diagnostics scripts/benchmarks scripts/site \
  resources/desktop resources/metainfo resources/templates \
  docs/architecture docs/adr docs/integrations docs/services web; do
  [[ -d "$path" ]] || fail "canonical repository module is missing: $path"
done
[[ -f packaging/nix/package.nix ]] || fail "Nix implementation is not colocated with other packaging"
rg -Fq './packaging/nix/package.nix' flake.nix || fail "root flake does not delegate to packaging/nix"
rg -Fq 'builtins.readFile ../../VERSION' packaging/nix/package.nix || \
  fail "Nix derivation reads VERSION at the wrong repository depth"
[[ -f src/services/feedback/CMakeLists.txt && \
   -f src/services/feedback/deploy/vocotype-feedback.service ]] || \
  fail "feedback source and deployment assets are not colocated"

# Product/client implementation must remain compiled native code.
for path in app settings_center fcitx5/backend; do
  if [[ -d "$path" ]] && find "$path" -type f -print -quit | grep -q .; then
    fail "legacy client files remain under: $path"
  fi
done
for path in ibus/engine.py ibus/factory.py ibus/main.py ibus/rime_runtime.py \
            scripts/install/common/check-python-runtime.py scripts/install/common/setup-audio.py \
            scripts/install/common/validate-installed-integration.py; do
  [[ ! -e "$path" ]] || fail "legacy Python product file remains: $path"
done

# Installed/build package paths cannot invoke Python or private venvs.
if rg -n '(^|[;&|[:space:]])python(3)?([[:space:]]|$)|PYTHONPATH|[^[:alnum:]_]\.py([[:space:]"'"'"')]|$)' \
    packaging/common/bin packaging/common/systemd packaging/scripts/stage-system-package.sh \
    packaging/scripts/build-{deb,rpm,arch}.sh scripts/install/common/install-native-user.sh \
    scripts/install/fcitx5 scripts/install/ibus; then
  fail "Python reference remains in an installed or package build path"
fi

# gedit/GTK replacement regression: validate first, then issue delete and commit
# in the same transaction. Never poll stale surrounding-text as an ACK.
replace_body=$(awk '
  /void VoCoTypeModule::replaceSurroundingText\(/ {capture=1}
  capture {print}
  capture && /void VoCoTypeModule::applyVoiceEditResult\(/ {exit}
' src/integrations/fcitx5/module/vocotype_module.cpp)
[[ "$replace_body" == *'voiceEditSnapshotStillMatches'* ]] || fail "replacement lacks preflight snapshot check"
[[ "$replace_body" == *'deleteSurroundingText'* ]] || fail "replacement lacks surrounding deletion"
[[ "$replace_body" == *'commitText(ic, new_text)'* ]] || fail "replacement lacks immediate commit"
delete_line=$(grep -n 'deleteSurroundingText' <<<"$replace_body" | head -1 | cut -d: -f1)
commit_line=$(grep -n 'commitText(ic, new_text)' <<<"$replace_body" | head -1 | cut -d: -f1)
(( delete_line < commit_line )) || fail "replacement commits before deleting"
[[ "$replace_body" != *'scheduleEditReplacementCheck'* ]] || fail "stale-cache polling returned"
[[ "$replace_body" != *'当前输入框不支持替换文本'* ]] || fail "stale-cache unsupported error returned"
! rg -q 'finalizeEditReplacement|edit_replace_retries_left_|edit_replace_timer_' src/integrations/fcitx5/module || fail "obsolete delayed replacement state remains"


rg -Fq '🎤 语音编辑中...' src/integrations/fcitx5/module/vocotype_module.cpp || \
  fail "clean voice-edit status title missing"
if rg -q 'del=|sur=1|active=1|edit_replace_state_' src/integrations/fcitx5/module; then
  fail "voice-edit debug capability probe remains"
fi
rg -Uq 'snapshot\.selected_text,[[:space:]]*"supported",[[:space:]]*true\);' \
  src/integrations/fcitx5/module/vocotype_module.cpp || \
  fail "valid surrounding context is not advertised as replace-capable"

# Exact polish status requested by product owner.
rg -Fq '正在润色... （等待模型输出 ' src/integrations/fcitx5/module/vocotype_module.cpp || fail "polish elapsed/timeout line missing"
rg -Fq '粗识别文本：' src/integrations/fcitx5/module/vocotype_module.cpp || fail "rough recognition line missing"
rg -Fq 'active_polish_started_us_' src/integrations/fcitx5/module/vocotype_module.cpp || fail "polish timer missing"

# README must retain the public project-growth chart across documentation rewrites.
for token in \
  '## Star History' \
  'https://www.star-history.com/?repos=LeonardNJU%2FVocoType-linux&type=date&legend=top-left' \
  'alt="VoCoType Linux Star History Chart"'; do
  rg -Fq "$token" README.md || \
    fail "README Star History block is missing: $token"
done

# The native settings window must preserve the Beta3 information architecture
# while exposing framework-specific controls only in the active framework.
settings=src/desktop/src/settings_main.cpp
ui=src/desktop/src/settings_ui.cpp
for token in \
  'gtk_header_bar_new' 'gtk_stack_sidebar_new' '1120, 760' \
  '概览与安装' '通用设置' 'Playground' '用户词典' 'AI 功能' \
  '诊断' '教程' '反馈' \
  'list_audio_devices' 'play_pcm16' 'draw_waveform' 'edit_audio' \
  'query_latest_release' 'create_support_bundle' 'submit_feedback' \
  'uninstall_integration' 'verify_native_payload' \
  'discover_rime_schemas' 'gtk_combo_box_text_new' \
  '初始化 IBus 内置 Rime'; do
  rg -Fq "$token" "$settings" "$ui" || \
    fail "native settings capability missing: $token"
done
python3 - <<'PY_AUDIO_ENUM' || fail "settings reverted to duplicate PortAudio enumeration"
from pathlib import Path
source = Path("src/desktop/src/settings_main.cpp").read_text()
body = source.split("void refresh_devices(SettingsWindow &window) {", 1)[1].split(
    "bool terminate_owned_core()", 1
)[0]
assert body.count("list_audio_devices(") == 1
assert "list_input_devices(" not in body
assert "list_output_devices(" not in body
PY_AUDIO_ENUM
! rg -q 'gtk_notebook_new|GtkEntry \*rime_schema' "$settings" || \
  fail "legacy notebook or free-text Rime schema returned"
rg -Fq 'gtk_widget_set_visible(window.rime_resource_row, ibus)' "$settings" || \
  fail "IBus Rime initialization is not conditional"
for token in 'window.ibus_advanced_section' 'window.ibus_advanced_card' \
             'gtk_widget_set_visible(widget, ibus)'; do
  rg -Fq "$token" "$settings" || \
    fail "IBus advanced section is not conditionally visible: $token"
done
for token in 'window.fcitx_advanced_section' 'window.fcitx_advanced_card' \
             'Fcitx 5：高级选项'; do
  rg -Fq "$token" "$settings" || \
    fail "Fcitx advanced section is incomplete: $token"
done
if rg -Fq 'gtk_widget_set_no_show_all(window.' "$settings"; then
  fail "framework-specific containers can remain visible with hidden children"
fi
show_line=$(grep -n 'gtk_widget_show_all(window->window)' "$settings" | cut -d: -f1)
framework_line=$(grep -n 'apply_framework_selection(\*window, selected_framework' "$settings" | cut -d: -f1)
[[ -n "$show_line" && -n "$framework_line" && "$show_line" -lt "$framework_line" ]] || \
  fail "framework visibility is applied before GTK shows child controls"
rg -Fq '.card-row:last-child' "$ui" || \
  fail "settings cards still draw a duplicate separator below their final row"
rg -Fq 'border-bottom-width: 0' "$ui" || \
  fail "final settings row separator is not disabled"
rg -Fq 'VOCOTYPE_VERSION' src/desktop/src/ibus_main.cpp || \
  fail "native IBus XML does not use the repository version"
if rg -Fq '<version>3</version>' src/desktop/src/ibus_main.cpp; then
  fail "native IBus XML still hard-codes the V3 version"
fi
for token in \
  'parse_fcitx_addon_states' 'SetAddonsState' '--repair-fcitx-profile' \
  'migrate_legacy_fcitx_profile' 'legacy_fcitx_profile_references' \
  '.vocotype-backup' 'verify_fcitx_addon_enabled'; do
  rg -Fq -- "$token" src/desktop scripts/install/common/install-native-user.sh || \
    fail "Issue #4 Fcitx repair capability missing: $token"
done
rg -Fq 'app-org.fcitx.Fcitx5@autostart.service' "$settings" ||   fail "settings repair does not restart Fcitx through the desktop session"
if rg -Fq 'run_command({"fcitx5", "-r", "-d"})' "$settings"; then
  fail "settings repair can launch Fcitx without desktop environment checks"
fi

# Core lifecycle must use the persistent user service when installed. A
# settings window must never replace it with a parent-bound temporary child.
ipc=src/desktop/src/ipc.cpp
rg -Fq 'native_core_service_available' "$ipc" || \
  fail "persistent Core service discovery missing"
rg -Fq 'start_native_core_service(false, socket, wait_ms)' "$ipc" || \
  fail "ensure_native_core does not prefer the user service"
rg -Fq 'start_native_core_service(true, socket, 45000)' "$settings" || \
  fail "settings Core restart does not use the user service"
rg -Fq 'startBackendUserService' src/integrations/fcitx5/module/vocotype_module.cpp || \
  fail "Fcitx cannot recover a missing backend service"
rg -Fq 'backend_start_pending_' src/integrations/fcitx5/module/vocotype_module.cpp || \
  fail "Fcitx backend recovery is not serialized"
rg -Fq 'start_asr_prewarm(state)' src/desktop/src/ibus_main.cpp || \
  fail "IBus does not prewarm final ASR at recording start"
rg -Fq 'wait_for_asr_prepare(asr_lease' src/desktop/src/ibus_main.cpp || \
  fail "IBus final ASR does not wait for recording-time preparation"
rg -Fq 'startAsrPrewarm()' src/integrations/fcitx5/module/vocotype_module.cpp || \
  fail "Fcitx does not prewarm final ASR at voice-key press"
rg -Fq 'waitForAsrPrepare(asr_prewarm' src/integrations/fcitx5/module/vocotype_module.cpp || \
  fail "Fcitx final ASR does not wait for recording-time preparation"
rg -Fq 'prepareAsr(45000)' src/integrations/fcitx5/module/vocotype_module.cpp || \
  fail "Fcitx prewarm does not request final-ASR preparation"
rg -Fq 'prewarm_offline_asr(socket, config, asr_lease)' src/integrations/macos/VocoTypeInputController.mm || \
  fail "macOS does not prewarm final ASR at recording start"
rg -Fq 'wait_for_asr_prepare(asr_lease' src/integrations/macos/VocoTypeInputController.mm || \
  fail "macOS final ASR does not wait for recording-time preparation"
for service in scripts/install/common/install-native-user.sh \
               packaging/common/systemd/vocotype-fcitx5-backend.service; do
  rg -Fq 'Restart=always' "$service" || \
    fail "persistent Core service is not restart-always: $service"
done


# The tracked source tree contains no Python implementation or dependency
# manifest. Local build caches and historical staging directories are ignored.
if git ls-files '*.py' | grep -q .; then
  fail "Python source remains in the repository"
fi
if git ls-files 'pyproject.toml' 'uv.lock' 'requirements*.txt' | grep -q .; then
  fail "Python dependency manifest remains in the repository"
fi

for token in \
  'VOCOTYPE_SQLITE_TARGET' \
  'TARGET SQLite3::SQLite3' \
  'TARGET SQLite::SQLite3' \
  'add_executable(vocotype-feedback' \
  '#include <boost/beast/http.hpp>' \
  'vocotype-feedback serve' \
  '/opt/vocotype-feedback/bin/vocotype-feedback'; do
  rg -Fq "$token" src/services/feedback || \
    fail "native feedback capability missing: $token"
done


# Documentation publishing is also compiled/static and must not reintroduce a
# Python site generator.
for token in \
  'scripts/site/build.sh build/pages' \
  'scripts/site/docs_builder.cpp' \
  'Generated by the compiled VoCoType documentation builder'; do
  rg -Fq "$token" .github/workflows/pages.yml scripts/site/docs_builder.cpp || \
    fail "native documentation build token missing: $token"
done
if rg -n 'setup-python|mkdocs|requirements-docs|pip install' \
    .github/workflows/pages.yml scripts/site/build.sh; then
  fail "Python documentation build dependency remains"
fi

# Documentation must use the compiled renderer's supported Markdown subset and
# stay synchronized with the configurable-shortcut/native-Rime architecture.
for token in \
  'unsupported Markdown extension' \
  'unclosed fenced code block' \
  'validate_markdown_links' \
  'class=\"language-' \
  'pre code{display:block;white-space:pre'; do
  rg -Fq "$token" scripts/site/docs_builder.cpp || \
    fail "compiled documentation renderer is missing: $token"
done
for token in \
  'Static documentation leaked unrendered Markdown syntax' \
  'vocotype-linux-fcitx5_*.deb' \
  'vocotype-linux-fcitx5-*.rpm' \
  'vocotype-linux-fcitx5-*.pkg.tar.zst'; do
  rg -Fq "$token" scripts/site/build.sh || \
    fail "static documentation build gate is missing: $token"
done
if rg -n '^(=== |!!! |\?\?\? |:::[[:space:]])' docs; then
  fail "unsupported Markdown extension remains in documentation sources"
fi
for stale in \
  '由 yaml-cpp 验证' \
  '通过项目内 `ctypes` 适配层' \
  '修复版本**: 2.1.3（计划）' \
  'Recognition → IBus Rime schema'; do
  if rg -Fq "$stale" docs; then
    fail "stale documentation remains: $stale"
  fi
done
for token in \
  '只安装其中一种' \
  '三个动作都可以在 **VoCoType 设置 → 通用设置 → 语音快捷键** 中独立录制' \
  '不存在 Python binding 或 `ctypes` 适配层' \
  'Core 与设置中心共用的原生解析器'; do
  rg -Fq "$token" docs || fail "current product documentation is missing: $token"
done

# Package and source installations must compile the Core against the target
# distribution instead of reusing a portable libcurl-linked Core.
for lifecycle in packaging/scripts/stage-system-package.sh scripts/install/common/install-native-user.sh; do
  rg -Fq 'src/core' "$lifecycle" || \
    fail "$lifecycle does not build vocotype-core for the target distribution"
done
rg -Fq 'cmake --build "$CORE_BUILD" --target vocotype-core' \
  packaging/scripts/stage-system-package.sh || \
  fail "package staging does not compile the target-distribution Core"
rg -Fq 'cmake --build "$core_build" --target vocotype-core' \
  scripts/install/common/install-native-user.sh || \
  fail "source installation does not compile the target-distribution Core"
if rg -Fq 'install -m755 "$streaming_bundle/bin/vocotype-core"' \
    packaging/scripts/stage-system-package.sh; then
  fail "package staging still installs the portable bundle Core"
fi
rg -Fq 'rm -f "$bundle/bin/vocotype-core"' \
  packaging/scripts/build-native-streaming-release.sh || \
  fail "portable bundle builder does not remove stale Core artifacts"
if rg -Fq 'bin/vocotype-core' packaging/scripts/prepare-complete-source.sh; then
  fail "complete source still requires Core as a portable bundle member"
fi
if rg -Fq 'core_system=' src/workers/funasr/audit_bundle.sh; then
  fail "portable worker audit still has Core-specific system dependencies"
fi
rg -Fq 'strip --strip-unneeded "$private_dir/vocotype-core"' \
  packaging/scripts/stage-system-package.sh || \
  fail "package staging checksums an unstripped target-distribution Core"
rg -Fq 'override_dh_dwz:' packaging/debian/rules || \
  fail "Debian packaging may rewrite the checksummed private runtime"
for workflow in .github/workflows/ci.yml .github/workflows/release.yml; do
  rg -Fq 'packaging/tests/validate-rpm-flavors.sh' "$workflow" || \
    fail "$workflow does not reuse the Bash RPM validation driver"
  if rg -Fq "rpm -qp --qf '%{NAME}'" "$workflow"; then
    fail "$workflow duplicates RPM metadata lookup logic"
  fi
done
for helper in \
  packaging/scripts/find-rpm-package.sh \
  packaging/tests/validate-rpm-flavors.sh; do
  test -x "$helper" || fail "$helper is missing or not executable"
done
rg -Fq 'RPM_ALL_FLAVORS_VALIDATION_OK' packaging/tests/validate-rpm-flavors.sh || \
  fail "RPM validation driver lacks an explicit completion marker"
rg -Fq "! -name '*.src.rpm'" packaging/scripts/find-rpm-package.sh || \
  fail "RPM metadata lookup helper does not exclude source RPM archives"

# Archive audits must only pass existing library roots to find; IBus-only RPMs
# may contain /usr/lib64 without creating /usr/lib in the extracted package.
rg -Fq 'library_roots=()' packaging/tests/audit-built-package.sh || \
  fail "built-package audit does not handle flavor-specific library roots"
rg -Fq 'find "${library_roots[@]}"' packaging/tests/audit-built-package.sh || \
  fail "built-package audit still scans nonexistent library roots"

# Package smoke tests must support both conventional /usr/libexec and the
# Arch /usr/lib/vocotype helper layout.
for package_test in   packaging/tests/audit-built-package.sh   packaging/tests/smoke-installed-package.sh   packaging/tests/smoke-binary-runtime.sh; do
  rg -Fq '/usr/libexec/' "$package_test" ||     fail "$package_test does not support the standard libexec layout"
  rg -Fq '/usr/lib/vocotype/' "$package_test" ||     fail "$package_test does not support the Arch helper layout"
done
rg -Fq '/usr/lib64/vocotype' packaging/tests/smoke-removed-package.sh ||   fail "package removal smoke does not check private runtime cleanup"

# GitHub Release assets normalize Debian's '~' prerelease separator to '.'
# after filtering package-build metadata. Bash requires the tilde pattern to be
# escaped inside parameter substitution; `${value//~/.}` leaves it unchanged.
(
  work=$(mktemp -d)
  trap 'rm -rf "$work"' EXIT
  mkdir -p "$work/source"
  touch "$work/source/vocotype-linux_4.0.0~beta1-1_amd64.deb"
  touch "$work/source/vocotype-linux_4.0.0~beta1-1_amd64.buildinfo"
  touch "$work/source/vocotype-linux_4.0.0~beta1-1_amd64.changes"
  touch "$work/source/vocotype-linux-4.0.0-0.beta1.src.rpm"
  touch "$work/source/VoCoType-linux-4.0.0b1-macOS-arm64.dmg"
  packaging/scripts/collect-release-assets.sh \
    "$work/source" "$work/final" --installers-only >/dev/null
  [[ -f "$work/final/vocotype-linux_4.0.0.beta1-1_amd64.deb" ]] || \
    fail "release collector did not normalize Debian prerelease separator"
  [[ -f "$work/final/VoCoType-linux-4.0.0b1-macOS-arm64.dmg" ]] || \
    fail "release collector omitted the macOS DMG"
  [[ $(find "$work/final" -maxdepth 1 -type f | wc -l) -eq 2 ]] || \
    fail "release collector included non-installer build metadata"
)
rg -Fq '${name//\~/.}' packaging/scripts/collect-release-assets.sh || \
  fail "release collector uses ineffective unescaped tilde substitution"
rg -Fq '${DEB//\~/.}' packaging/scripts/validate-final-release-assets.sh || \
  fail "final asset validator uses ineffective unescaped tilde substitution"

# Fedora's libcurl package exposes the OpenSSL symbol version in ELF but not
# as an RPM provide. The spec must filter only that generated requirement and
# retain an explicit dependency on the full libcurl implementation.
rg -Fq '__requires_exclude ^libcurl\\.so\\.4\\(CURL_OPENSSL_4\\)\\(64bit\\)$'   packaging/rpm/vocotype.spec.in ||   fail "RPM spec does not filter Fedora's unsatisfiable CURL_OPENSSL_4 auto-require"
rg -Fq "'Requires:       libcurl-full'" packaging/scripts/render-package-metadata.sh ||   fail "RPM metadata does not require Fedora libcurl-full"
if rg -Fq "files=('%{_libexecdir}/vocotype-audio-recorder'"     packaging/scripts/render-package-metadata.sh; then
  fail "RPM flavor file list duplicates common native helpers"
fi

# Debian and Ubuntu name the PortAudio development package portaudio19-dev.
for metadata_source in packaging/scripts/render-package-metadata.sh scripts/install/common/install-system-dependencies.sh; do
  rg -Fq 'portaudio19-dev' "$metadata_source" ||     fail "$metadata_source does not use the Debian PortAudio development package"
  if rg -Fq 'libportaudio2-dev' "$metadata_source"; then
    fail "$metadata_source uses the nonexistent libportaudio2-dev package"
  fi
done

# Settings and package metadata must not bind to a distribution-specific
# yaml-cpp SONAME. Ubuntu 22.04 builds libyaml-cpp.so.0.7, while newer Ubuntu
# releases may remove that runtime package. Core and settings share one native
# parser instead.
for path in \
  src/common/include/vocotype/common/terms_yaml.hpp \
  src/common/src/terms_yaml.cpp; do
  test -f "$path" || fail "shared terms parser is missing: $path"
done
for token in 'parse_terms_yaml_content(content)' 'parse_rime_schema_metadata'; do
  rg -Fq "$token" src/desktop/src/settings_main.cpp || \
    fail "settings does not use the shared YAML parser: $token"
done
for path in \
  src/desktop/CMakeLists.txt src/desktop/src/settings_main.cpp \
  packaging/scripts/render-package-metadata.sh packaging/nix/package.nix \
  .github/workflows/ci.yml .github/workflows/release.yml; do
  if rg -Fq 'yaml-cpp' "$path"; then
    fail "distribution yaml-cpp dependency remains in $path"
  fi
done
yaml_audit=packaging/tests/check-yaml-package-dependencies.sh
test -x "$yaml_audit" || fail "manager-aware yaml-cpp package audit is missing"
yaml_test_dir=$(mktemp -d)
trap 'rm -rf "$yaml_test_dir"' EXIT
printf '%s
' 'libyaml-cpp0.7 (>= 0.7)' >"$yaml_test_dir/requires"
if "$yaml_audit" apt "$yaml_test_dir/requires"; then
  fail "Debian system yaml-cpp dependency was accepted"
fi
printf '%s
' 'yaml-cpp' >"$yaml_test_dir/requires"
if "$yaml_audit" pacman "$yaml_test_dir/requires"; then
  fail "Arch system yaml-cpp dependency was accepted"
fi
printf '%s
' 'yaml-cpp >= 0.8' >"$yaml_test_dir/requires"
if "$yaml_audit" dnf "$yaml_test_dir/requires"; then
  fail "RPM system yaml-cpp package dependency was accepted"
fi
printf '%s
' 'libyaml-cpp.so.0.6()(64bit)' >"$yaml_test_dir/requires"
: >"$yaml_test_dir/provides"
if "$yaml_audit" dnf "$yaml_test_dir/requires" "$yaml_test_dir/provides"; then
  fail "RPM unprovided private yaml-cpp SONAME was accepted"
fi
cp "$yaml_test_dir/requires" "$yaml_test_dir/provides"
"$yaml_audit" dnf "$yaml_test_dir/requires" "$yaml_test_dir/provides" || \
  fail "RPM self-provided private yaml-cpp SONAME was rejected"
rg -Fq 'settings center depends on distribution yaml-cpp ABI' \
  packaging/tests/audit-built-package.sh || \
  fail "settings ELF yaml-cpp regression audit is missing"

# Ubuntu 22.04 ships GLib 2.72, before G_APPLICATION_DEFAULT_FLAGS was
# introduced. The native settings app must retain the older zero-flags path.
rg -Fq 'GLIB_CHECK_VERSION(2, 74, 0)' src/desktop/src/settings_main.cpp ||   fail "native settings does not gate newer GApplication flags by GLib version"
rg -Fq 'G_APPLICATION_FLAGS_NONE' src/desktop/src/settings_main.cpp ||   fail "native settings does not support Ubuntu 22.04 GLib"

# Ubuntu 22.04 ships nlohmann-json 3.10.5 and is a supported build target.
for cmake_file in src/core/CMakeLists.txt src/desktop/CMakeLists.txt src/services/feedback/CMakeLists.txt; do
  rg -Fq 'find_package(nlohmann_json 3.10 REQUIRED)' "$cmake_file" ||     fail "$cmake_file requires a newer nlohmann-json than Ubuntu 22.04 provides"
done

# Native package metadata has no Python build/runtime dependency.
for format in debian rpm arch; do
  case "$format" in
    debian) template=packaging/debian/control ;;
    rpm) template=packaging/rpm/vocotype.spec.in ;;
    arch) template=packaging/arch/PKGBUILD.in ;;
  esac
  for flavor in universal ibus fcitx5; do
    output=$(mktemp)
    packaging/scripts/render-package-metadata.sh --format "$format" \
      --flavor "$flavor" --template "$template" --output "$output"
    if grep -Ei '(^|[ ,:])python([0-9.-]|$)|wheelhouse|virtualenv|PyGObject|numpy|sounddevice' "$output"; then
      rm -f "$output"
      fail "$format/$flavor metadata contains Python dependency"
    fi
    rm -f "$output"
  done
done

# Recorded shortcuts must be shared by settings, IBus, and Fcitx 5.
for path in \
  src/desktop/include/vocotype/desktop/hotkey.hpp \
  src/desktop/src/hotkey.cpp \
  scripts/test/hotkey-settings.sh \
  scripts/test/fake-fcitx-controller.sh; do
  test -f "$path" || fail "missing recorded-shortcut component: $path"
done
for token in \
  'hotkey_safety_error' \
  'hotkey_is_modifier_key' \
  'GDK_KEY_Alt_R' \
  '裸字母、数字、标点'; do
  rg -Fq "$token" src/desktop/src/hotkey.cpp || \
    fail "hotkey safety contract is missing: $token"
done
for token in \
  'HotkeySlot::transcribe' \
  'HotkeySlot::polish' \
  'HotkeySlot::edit' \
  'capture_hotkey_press' \
  'external_hotkey_conflict' \
  'XGrabKey' \
  'kglobalshortcutsrc' \
  'org.gnome.desktop.wm.keybindings' \
  'write_ibus_hotkeys' 'write_shared_config'; do
  rg -Fq "$token" src/desktop/src/settings_main.cpp || \
    fail "settings shortcut recorder/conflict contract is missing: $token"
done
for token in 'PolishKey' 'EditKey' 'hotkeyModeForKey' 'active_hotkey_'; do
  rg -Fq "$token" src/integrations/fcitx5/module/vocotype_module.h \
    src/integrations/fcitx5/module/vocotype_module.cpp || \
    fail "Fcitx configurable-shortcut contract is missing: $token"
done
python3 - <<'PY_CONTRACT' || fail "Fcitx PTT session initialization clears the active release key"
from pathlib import Path
source = Path("src/integrations/fcitx5/module/vocotype_module.cpp").read_text()
body = source.split("void VoCoTypeModule::armPendingRecordingStart(", 1)[1].split(
    "void VoCoTypeModule::cancelPendingRecordingStart", 1
)[0]
cancel = body.index("cancelPendingRecordingStart();")
pending = body.index("pending_ptt_key_ = pressed_key.normalize();")
active = body.index("active_hotkey_ = configured_hotkey;")
assert cancel < pending < active
PY_CONTRACT
for token in 'fcitx_addon_is_installed' 'XDG_DATA_HOME' 'XDG_DATA_DIRS'; do
  rg -Fq "$token" src/desktop/src/settings_main.cpp || \
    fail "stale Fcitx addon shortcut filter is missing: $token"
done
for token in \
  'fcitx://config/addon/vocotype' \
  '"SetConfig", "sv"' \
  'query_live_fcitx_config()' \
  'reconcile_live_fcitx_config' \
  '--reconcile-fcitx-hotkeys-from-config' \
  'verify_fcitx_config_values' \
  'user_config_root()' \
  'Fcitx 配置与运行实例同步' \
  '共享配置职责' \
  'IBus 快捷键配置'; do
  rg -Fq -- "$token" src/desktop/src/settings_main.cpp || \
    fail "live Fcitx hotkey persistence contract is missing: $token"
done
for token in 'shared_config_path' 'ibus_config_path' \
             'legacy_runtime_config_path' 'migrate_config_layout' \
             'write_shared_config' 'write_ibus_hotkeys'; do
  rg -Fq "$token" src/desktop/include/vocotype/desktop/config.hpp \
    src/desktop/src/config.cpp || \
    fail "role-specific config layout is missing: $token"
done
! rg -Fq 'window.config["hotkeys"]' src/desktop/src/settings_main.cpp || \
  fail "shared settings still own runtime hotkeys"
! rg -Fq 'write_json_file_atomic(directory / "fcitx5-backend.json"' \
  src/desktop/src/settings_main.cpp || \
  fail "settings still writes the legacy Fcitx backend JSON"
rg -Fq 'value.erase("hotkeys")' src/desktop/src/config.cpp || \
  fail "shared config writer does not enforce its no-hotkeys responsibility"
rg -Fq '{"transcribe", hotkeys.value("transcribe", "F9")}' \
  src/desktop/src/config.cpp || \
  fail "IBus config writer does not normalize to the three supported shortcuts"
rg -Fq '已有 `vocotype.conf` 始终优先' docs/guides/shortcuts.md || \
  fail "shortcut documentation does not define Fcitx source precedence"

python3 - <<'PY_FCITX_SAVE' || fail "settings still restarts Fcitx after writing shortcut files"
from pathlib import Path
source = Path("src/desktop/src/settings_main.cpp").read_text()
old_sequence = """save_config(*self);
          if (framework_installed("fcitx5"))
            (void)restart_fcitx_with_vocotype();"""
assert old_sequence not in source
assert '"SetConfig", "sv"' in source
assert "verify_fcitx_config_values(query_live_fcitx_config(), values);" in source
PY_FCITX_SAVE
for token in 'hotkeys.value("transcribe"' 'hotkeys.value("polish"' \
             'hotkeys.value("edit"' 'hotkey_mask'; do
  rg -Fq "$token" src/desktop/src/ibus_main.cpp || \
    fail "IBus configurable-shortcut contract is missing: $token"
done
if rg -Fq 'if (keyval == IBUS_KEY_F9)' src/desktop/src/ibus_main.cpp; then
  fail "IBus still hard-codes F9 in its key event handler"
fi

# macOS must be built from a clean arm64 runner and join the exact Release set.
for workflow in .github/workflows/ci.yml .github/workflows/release.yml; do
  for token in 'runs-on: macos-15' 'ALLOW_ADHOC_TEST: 1' \
               'packaging/macos/build-dmg.sh' 'hdiutil verify'; do
    rg -Fq "$token" "$workflow" ||
      fail "$workflow is missing macOS CI token: $token"
  done
done
for token in 'VoCoType-linux-*-macOS-arm64.dmg' \
             'VoCoType-linux-${VERSION}-macOS-arm64.dmg' \
             'Expected 10 installers and SHA256SUMS' \
             'Checksum set must contain 10 installers'; do
  rg -Fq "$token" packaging/scripts/collect-release-assets.sh \
    packaging/scripts/validate-final-release-assets.sh ||
    fail "macOS release asset contract is missing: $token"
done
for required in packaging/macos/build-dmg.sh docs/getting-started/macos.md \
                docs/development/macos-packaging.md; do
  test -f "$required" || fail "macOS release/documentation file is missing: $required"
done

# The tested-release publisher must consume the single assembled artifact.  It
# must never merge package-job artifacts with final-release-assets, which would
# duplicate every installer at publication time.
for token in \
  'name: final-release-assets' \
  'path: final-assets' \
  'Revalidate the exact assembled downloadable asset set' \
  'for required in native-streaming macos source-deb rpm arch' \
  'for required in macos-arm64-dmg source-deb rpm arch final-release-assets'; do
  rg -Fq "$token" .github/workflows/publish-tested-release.yml || \
    fail "tested-release final artifact contract is missing: $token"
done
if rg -Fq 'merge-multiple: true' .github/workflows/publish-tested-release.yml; then
  fail "tested-release publisher still merges package and assembled artifacts"
fi

# Nix support must remain source-built, locked, and cover all integration flavors.
for path in flake.nix flake.lock packaging/nix/package.nix docs/getting-started/nix.md; do
  test -f "$path" || fail "missing Nix support file: $path"
done
jq -e '.version == 7 and .nodes.nixpkgs.locked.rev == "b6018f87da91d19d0ab4cf979885689b469cdd41"' \
  flake.lock >/dev/null || fail "flake.lock is not pinned to the reviewed nixpkgs revision"
for token in \
  'vocotype-universal' \
  'vocotype-ibus' \
  'vocotype-fcitx5' \
  'vocotype-funasr-workers'; do
  rg -Fq "$token" flake.nix || fail "flake output is missing: $token"
done
for token in \
  'bd6e72142f1cca3c30b7651bf5fa567dfe969810' \
  'sha256-3abFrokYBHCfoRlxXnF92pwBleypRX4E1eFL+tTXAI8=' \
  'onnxruntime.dev' \
  'src/workers/funasr/build.sh' \
  'src/core' \
  'src/integrations/fcitx5/module'; do
  rg -Fq "$token" packaging/nix/package.nix || fail "Nix source-build contract is missing: $token"
done
if rg -Fq 'lib.fakeHash' packaging/nix/package.nix; then
  fail "Nix package still contains a fake fixed-output hash"
fi
for token in \
  'DeterminateSystems/determinate-nix-action@v3' \
  'nix build .#vocotype-fcitx5' \
  'nix build .#vocotype-ibus' \
  'nix build .#vocotype-universal'; do
  rg -Fq "$token" .github/workflows/ci.yml || \
    fail "CI does not build Nix output: $token"
done
rg -Fq 'NLOHMANN_JSON_INCLUDE_DIR' src/workers/funasr/build.sh || \
  fail "native worker build cannot consume Nix-provided nlohmann headers"
rg -Fq 'chmod -R u+w "$SOURCE_COPY"' src/workers/funasr/build.sh || \
  fail "native worker build cannot overlay read-only Nix sources"
if rg -Fq 'compgen -G' src/workers/funasr/build.sh; then
  fail "native worker build relies on compgen unavailable in minimal Nix Bash"
fi
rg -Fq 'bash "$SCRIPT_DIR/audit_bundle.sh"' src/workers/funasr/build.sh || \
  fail "native worker audit relies on an FHS /usr/bin/env shebang"
for token in '--nix-store' 'mode=nix-store' '/nix/store/*' 'ldd "$path"'; do
  rg -Fq -- "$token" src/workers/funasr/audit_bundle.sh || \
    fail "native bundle audit is missing Nix-store rule: $token"
done
rg -Fq 'VOCOTYPE_BUNDLE_AUDIT_MODE=nix-store' packaging/nix/package.nix || \
  fail "Nix workers do not request Nix-store bundle auditing"
for token in 'autoPatchelfHook' 'alsa-lib' 'libX11' 'libXdmcp' 'libsysprof-capture'; do
  rg -Fq "$token" packaging/nix/package.nix || \
    fail "Nix runtime closure is missing dependency: $token"
done
for token in 'vocotype-streaming-worker --help' 'vocotype-offline-worker --help' "grep -Fq 'XGrabKey'"; do
  rg -Fq "$token" .github/workflows/ci.yml || \
    fail "Nix smoke coverage is missing: $token"
done
rg -Fq 'nix path-info -Sh ./result-workers ./result-fcitx5 ./result-ibus ./result-universal' \
  .github/workflows/ci.yml || \
  fail "Nix size smoke treats result symlinks as flake registry names"
rg -Fq 'VOCOTYPE_FCITX5_BACKEND_PATH' src/integrations/fcitx5/module/CMakeLists.txt || \
  fail "Fcitx module cannot receive a Nix store backend path"


# AI connection testing is an optional diagnostic, never persistent activation
# state. Opening settings or editing fields must not send real LLM requests.
ai_semantics_files=(
  src/desktop/src/settings_main.cpp
  src/integrations/macos/VocoTypeApplicationController.mm
  src/core/src/voice_edit.cpp
  docs/guides/slm-streaming.md
  docs/guides/voice-editing.md
  docs/integrations/ibus.md
)
for phrase in \
  '自动测活' \
  '成功后即可使用 AI 功能' \
  'AI 功能尚未启用或未测活' \
  '启用并测活' \
  '已启用且已测活' \
  'AI 功能需先完成端点 / 模型测活'; do
  if rg -Fq "$phrase" "${ai_semantics_files[@]}"; then
    fail "AI connection testing is still described as activation state: $phrase"
  fi
done
if rg -q 'scheduleAIHealthCheck|runAIHealthCheck|_lastAIHealthCheck' \
    src/integrations/macos/VocoTypeApplicationController.mm; then
  fail "macOS still contains automatic AI health-check scheduling"
fi
rg -Fq '测试 AI 连接（可选）' src/desktop/src/settings_main.cpp || \
  fail "GTK settings does not identify AI connection testing as optional"
rg -Fq '测试 AI 连接' src/integrations/macos/VocoTypeApplicationController.mm || \
  fail "macOS settings does not expose the manual AI connection test"
rg -Fq '无需在每次启动后执行' src/desktop/src/settings_main.cpp || \
  fail "GTK settings does not explain persistent AI configuration semantics"
rg -Fq '无需在每次启动后重复' src/integrations/macos/VocoTypeApplicationController.mm || \
  fail "macOS settings does not explain persistent AI configuration semantics"
rg -Fq '可能产生延迟或费用' src/desktop/src/settings_main.cpp || \
  fail "GTK settings does not disclose real-request latency or charges"
rg -Fq '可能产生延迟或费用' src/integrations/macos/VocoTypeApplicationController.mm || \
  fail "macOS settings does not disclose real-request latency or charges"

echo NATIVE_CONTRACTS_OK
