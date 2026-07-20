#!/usr/bin/env bash
set -euo pipefail

for path in \
  /usr/bin/vocotype-settings \
  /usr/bin/vocotype-fcitx5-backend \
  /usr/bin/vocotype-fcitx5-recorder \
  /usr/libexec/vocotype-ibus-engine \
  /usr/share/ibus/component/vocotype.xml \
  /usr/share/vocotype/.system-package; do
  test ! -e "$path"
done

if find /usr/lib64 /usr/lib -path '*/fcitx5/vocotype.so' -type f -print -quit 2>/dev/null | grep -q .; then
  echo 'VoCoType Fcitx module remains after package removal' >&2
  exit 1
fi

test ! -e /usr/share/vocotype
echo PACKAGE_REMOVE_SMOKE_OK
