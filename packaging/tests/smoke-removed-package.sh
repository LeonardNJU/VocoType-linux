#!/usr/bin/env bash
set -euo pipefail
for path in \
  /usr/bin/vocotype-settings \
  /usr/bin/vocotype-fcitx5-backend \
  /usr/bin/vocotype-fcitx5-recorder \
  /usr/libexec/vocotype-audio-recorder \
  /usr/libexec/vocotype-model-manager \
  /usr/libexec/vocotype-core \
  /usr/libexec/vocotype-streaming-worker \
  /usr/libexec/vocotype-offline-worker \
  /usr/libexec/vocotype-ibus-engine \
  /usr/lib/vocotype \
  /usr/lib64/vocotype \
  /usr/share/ibus/component/vocotype.xml \
  /usr/share/vocotype/.system-package; do
  [[ ! -e "$path" ]]
done
if find /usr/lib64 /usr/lib -path '*/fcitx5/vocotype.so' -type f \
    -print -quit 2>/dev/null | grep -q .; then
  echo 'VoCoType Fcitx module remains after package removal' >&2
  exit 1
fi
[[ ! -e /usr/share/vocotype ]]
echo PACKAGE_REMOVE_SMOKE_OK
