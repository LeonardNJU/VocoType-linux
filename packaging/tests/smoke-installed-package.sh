#!/usr/bin/env bash
set -euo pipefail
expected_version=${1:?usage: $0 EXPECTED_VERSION [EXPECTED_FLAVOR]}
expected_flavor=${2:-}
marker=/usr/share/vocotype/.system-package

runtime_executable() {
  local name=$1 candidate
  for candidate in \
    "/usr/libexec/$name" \
    "/usr/lib/vocotype/$name" \
    "/usr/lib64/vocotype/$name"; do
    [[ -x "$candidate" ]] && { printf '%s\n' "$candidate"; return 0; }
  done
  return 1
}

[[ -f "$marker" ]] || { echo "missing package marker" >&2; exit 1; }
grep -Fxq "version=$expected_version" "$marker"
grep -Fxq 'managed-by=native-package' "$marker"
grep -Fxq 'runtime=native' "$marker"
flavor=$(sed -n 's/^flavor=//p' "$marker")
[[ -z "$expected_flavor" || "$flavor" == "$expected_flavor" ]]
case "$flavor" in universal|ibus|fcitx5) ;; *) exit 1;; esac
includes_ibus=false
includes_fcitx=false
case "$flavor" in
  universal) includes_ibus=true; includes_fcitx=true ;;
  ibus) includes_ibus=true ;;
  fcitx5) includes_fcitx=true ;;
esac

if find /usr/share/vocotype -type f \( -name '*.py' -o -name '*.pyc' -o -name '*.whl' \) \
    -print -quit | grep -q .; then
  echo "installed runtime contains a Python artifact" >&2
  exit 1
fi

for path in \
  /usr/bin/vocotype-settings \
  /usr/share/applications/io.github.LeonardNJU.VoCoType.Settings.desktop \
  /usr/share/metainfo/io.github.LeonardNJU.VoCoType.metainfo.xml; do
  [[ -e "$path" ]] || { echo "missing packaged path: $path" >&2; exit 1; }
done

for executable in \
  /usr/bin/vocotype-settings \
  "$(runtime_executable vocotype-audio-recorder)" \
  "$(runtime_executable vocotype-model-manager)"; do
  file "$executable" | grep -q ELF
  runtime_log=$(mktemp)
  ldd -r "$executable" >"$runtime_log" 2>&1
  if grep -Eqi 'not found|undefined symbol|version `[^`]+. not found' "$runtime_log"; then
    cat "$runtime_log" >&2
    rm -f "$runtime_log"
    exit 1
  fi
  rm -f "$runtime_log"
done
"$(runtime_executable vocotype-audio-recorder)" --help >/dev/null
"$(runtime_executable vocotype-model-manager)" --help >/dev/null

native_dir=$(find /usr/lib64 /usr/lib -path '*/vocotype/.native-payload.sha256' \
  -printf '%h\n' -quit 2>/dev/null)
[[ -n "$native_dir" ]]
(cd "$native_dir" && sha256sum -c .native-payload.sha256)
for executable in vocotype-core vocotype-streaming-worker vocotype-offline-worker; do
  "$(runtime_executable "$executable")" --help >/dev/null
done

if [[ "$includes_ibus" == true ]]; then
  component=/usr/share/ibus/component/vocotype.xml
  [[ -f "$component" ]]
  ibus_engine=$(runtime_executable vocotype-ibus-engine)
  file "$ibus_engine" | grep -q ELF
  ldd -r "$ibus_engine" >/dev/null
  "$ibus_engine" --help >/dev/null
  ibus_xml=$("$ibus_engine" --xml)
  grep -q '<name>vocotype</name>' <<<"$ibus_xml"
  grep -Fq "<version>$expected_version</version>" <<<"$ibus_xml"
  grep -Fq "<version>$expected_version</version>" "$component"
else
  [[ ! -e /usr/share/ibus/component/vocotype.xml ]]
  ! runtime_executable vocotype-ibus-engine >/dev/null 2>&1
fi

if [[ "$includes_fcitx" == true ]]; then
  [[ -x /usr/bin/vocotype-fcitx5-backend ]]
  [[ -x /usr/bin/vocotype-fcitx5-recorder ]]
  [[ -f /usr/share/fcitx5/addon/vocotype.conf ]]
  module=$(find /usr/lib64 /usr/lib -path '*/fcitx5/vocotype.so' \
    -type f -print -quit 2>/dev/null)
  [[ -n "$module" ]]
  ldd -r "$module" >/dev/null
else
  [[ ! -e /usr/bin/vocotype-fcitx5-backend ]]
  [[ ! -e /usr/share/fcitx5/addon/vocotype.conf ]]
fi

bash -n /usr/share/vocotype/installers/install-native-user.sh
bash -n /usr/share/vocotype/installers/uninstall-native-user.sh
[[ "$includes_ibus" != true ]] || "$(dirname "$0")/smoke-ibus-registry.sh"
[[ "$includes_fcitx" != true ]] || "$(dirname "$0")/smoke-fcitx-addon.sh"
echo "PACKAGE_NATIVE_RUNTIME_OK version=$expected_version flavor=$flavor"
