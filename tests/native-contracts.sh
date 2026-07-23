#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"

fail() { echo "NATIVE_CONTRACT_FAIL: $*" >&2; exit 1; }

# Product/client implementation must remain compiled native code.
for path in app settings_center fcitx5/backend; do
  if [[ -d "$path" ]] && find "$path" -type f -print -quit | grep -q .; then
    fail "legacy client files remain under: $path"
  fi
done
for path in ibus/engine.py ibus/factory.py ibus/main.py ibus/rime_runtime.py \
            installers/check-python-runtime.py installers/setup-audio.py \
            installers/validate-installed-integration.py; do
  [[ ! -e "$path" ]] || fail "legacy Python product file remains: $path"
done

# Installed/build package paths cannot invoke Python or private venvs.
if rg -n '(^|[;&|[:space:]])python(3)?([[:space:]]|$)|PYTHONPATH|[^[:alnum:]_]\.py([[:space:]"'"'"')]|$)' \
    packaging/bin packaging/systemd packaging/tools/stage-system-package.sh \
    packaging/tools/build-{deb,rpm,arch}.sh installers/install-native-user.sh \
    fcitx5/scripts ibus/scripts; then
  fail "Python reference remains in an installed or package build path"
fi

# gedit/GTK replacement regression: validate first, then issue delete and commit
# in the same transaction. Never poll stale surrounding-text as an ACK.
replace_body=$(awk '
  /void VoCoTypeModule::replaceSurroundingText\(/ {capture=1}
  capture {print}
  capture && /void VoCoTypeModule::applyVoiceEditResult\(/ {exit}
' fcitx5/module/vocotype_module.cpp)
[[ "$replace_body" == *'voiceEditSnapshotStillMatches'* ]] || fail "replacement lacks preflight snapshot check"
[[ "$replace_body" == *'deleteSurroundingText'* ]] || fail "replacement lacks surrounding deletion"
[[ "$replace_body" == *'commitText(ic, new_text)'* ]] || fail "replacement lacks immediate commit"
delete_line=$(grep -n 'deleteSurroundingText' <<<"$replace_body" | head -1 | cut -d: -f1)
commit_line=$(grep -n 'commitText(ic, new_text)' <<<"$replace_body" | head -1 | cut -d: -f1)
(( delete_line < commit_line )) || fail "replacement commits before deleting"
[[ "$replace_body" != *'scheduleEditReplacementCheck'* ]] || fail "stale-cache polling returned"
[[ "$replace_body" != *'当前输入框不支持替换文本'* ]] || fail "stale-cache unsupported error returned"
! rg -q 'finalizeEditReplacement|edit_replace_retries_left_|edit_replace_timer_' fcitx5/module || fail "obsolete delayed replacement state remains"


rg -Fq '🎤 语音编辑中...' fcitx5/module/vocotype_module.cpp || \
  fail "clean voice-edit status title missing"
if rg -q 'del=|sur=1|active=1|edit_replace_state_' fcitx5/module; then
  fail "voice-edit debug capability probe remains"
fi
rg -Uq 'snapshot\.selected_text,\n[[:space:]]+"supported",\n[[:space:]]+true\);' \
  fcitx5/module/vocotype_module.cpp || \
  fail "valid surrounding context is not advertised as replace-capable"

# Exact polish status requested by product owner.
rg -Fq '正在润色... （等待模型输出 ' fcitx5/module/vocotype_module.cpp || fail "polish elapsed/timeout line missing"
rg -Fq '粗识别文本：' fcitx5/module/vocotype_module.cpp || fail "rough recognition line missing"
rg -Fq 'active_polish_started_us_' fcitx5/module/vocotype_module.cpp || fail "polish timer missing"

# The native settings window must preserve the Beta3 information architecture
# while exposing framework-specific controls only in the active framework.
settings=native/desktop/src/settings_main.cpp
ui=native/desktop/src/settings_ui.cpp
for token in \
  'gtk_header_bar_new' 'gtk_stack_sidebar_new' '1120, 760' \
  '概览与安装' '通用设置' 'Playground' '用户词典' 'AI 功能' \
  '诊断' '教程' '反馈' \
  'list_output_devices' 'play_pcm16' 'draw_waveform' 'edit_audio' \
  'query_latest_release' 'create_support_bundle' 'submit_feedback' \
  'uninstall_integration' 'verify_native_payload' \
  'discover_rime_schemas' 'gtk_combo_box_text_new' \
  '初始化 IBus 内置 Rime'; do
  rg -Fq "$token" "$settings" "$ui" || \
    fail "native settings capability missing: $token"
done
! rg -q 'gtk_notebook_new|GtkEntry \*rime_schema' "$settings" || \
  fail "legacy notebook or free-text Rime schema returned"
rg -Fq 'gtk_widget_set_visible(window.rime_resource_row, ibus)' "$settings" || \
  fail "IBus Rime initialization is not conditional"
rg -Fq 'gtk_widget_set_visible(window.rime_schema_row, ibus)' "$settings" || \
  fail "IBus Rime schema is not conditional"
rg -Fq 'VOCOTYPE_VERSION' native/desktop/src/ibus_main.cpp || \
  fail "native IBus XML does not use the repository version"
if rg -Fq '<version>3</version>' native/desktop/src/ibus_main.cpp; then
  fail "native IBus XML still hard-codes the V3 version"
fi
rg -Fq 'gtk_widget_set_visible(window.fcitx_composing_row, !ibus)' "$settings" || \
  fail "Fcitx-specific controls are not conditional"
for token in \
  'parse_fcitx_addon_states' 'SetAddonsState' '--repair-fcitx-profile' \
  'migrate_legacy_fcitx_profile' 'legacy_fcitx_profile_references' \
  '.vocotype-backup' 'verify_fcitx_addon_enabled'; do
  rg -Fq -- "$token" native/desktop installers/install-native-user.sh || \
    fail "Issue #4 Fcitx repair capability missing: $token"
done
rg -Fq 'app-org.fcitx.Fcitx5@autostart.service' "$settings" ||   fail "settings repair does not restart Fcitx through the desktop session"
if rg -Fq 'run_command({"fcitx5", "-r", "-d"})' "$settings"; then
  fail "settings repair can launch Fcitx without desktop environment checks"
fi

# Core lifecycle must use the persistent user service when installed. A
# settings window must never replace it with a parent-bound temporary child.
ipc=native/desktop/src/ipc.cpp
rg -Fq 'native_core_service_available' "$ipc" || \
  fail "persistent Core service discovery missing"
rg -Fq 'start_native_core_service(false, socket, wait_ms)' "$ipc" || \
  fail "ensure_native_core does not prefer the user service"
rg -Fq 'start_native_core_service(true, socket, 45000)' "$settings" || \
  fail "settings Core restart does not use the user service"
rg -Fq 'startBackendUserService' fcitx5/module/vocotype_module.cpp || \
  fail "Fcitx cannot recover a missing backend service"
rg -Fq 'backend_start_pending_' fcitx5/module/vocotype_module.cpp || \
  fail "Fcitx backend recovery is not serialized"
for service in installers/install-native-user.sh \
               packaging/systemd/vocotype-fcitx5-backend.service; do
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
  'Boost.Beast' \
  'vocotype-feedback serve' \
  '/opt/vocotype-feedback/bin/vocotype-feedback'; do
  rg -Fq "$token" feedback_service deploy/feedback || \
    fail "native feedback capability missing: $token"
done


# Documentation publishing is also compiled/static and must not reintroduce a
# Python site generator.
for token in \
  'tools/build-static-site.sh build/pages' \
  'tools/docs_builder.cpp' \
  'Generated by the compiled VoCoType documentation builder'; do
  rg -Fq "$token" .github/workflows/pages.yml tools/docs_builder.cpp || \
    fail "native documentation build token missing: $token"
done
if rg -n 'setup-python|mkdocs|requirements-docs|pip install' \
    .github/workflows/pages.yml tools/build-static-site.sh; then
  fail "Python documentation build dependency remains"
fi

# Package smoke tests must support both conventional /usr/libexec and the
# Arch /usr/lib/vocotype helper layout.
for package_test in   packaging/tests/audit-built-package.sh   packaging/tests/smoke-installed-package.sh; do
  rg -Fq '/usr/libexec/' "$package_test" ||     fail "$package_test does not support the standard libexec layout"
  rg -Fq '/usr/lib/vocotype/' "$package_test" ||     fail "$package_test does not support the Arch helper layout"
done
rg -Fq '/usr/lib64/vocotype' packaging/tests/smoke-removed-package.sh ||   fail "package removal smoke does not check private runtime cleanup"

# Native package metadata has no Python build/runtime dependency.
for format in debian rpm arch; do
  case "$format" in
    debian) template=packaging/debian/control ;;
    rpm) template=packaging/rpm/vocotype.spec.in ;;
    arch) template=packaging/arch/PKGBUILD.in ;;
  esac
  for flavor in universal ibus fcitx5; do
    output=$(mktemp)
    packaging/tools/render-package-metadata.sh --format "$format" \
      --flavor "$flavor" --template "$template" --output "$output"
    if grep -Ei '(^|[ ,:])python([0-9.-]|$)|wheelhouse|virtualenv|PyGObject|numpy|sounddevice' "$output"; then
      rm -f "$output"
      fail "$format/$flavor metadata contains Python dependency"
    fi
    rm -f "$output"
  done
done

echo NATIVE_CONTRACTS_OK
