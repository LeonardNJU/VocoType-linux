#!/usr/bin/env bash
set -euo pipefail
root=${1:?usage: $0 BUNDLE_DIR}
root=$(realpath "$root")
lib_dir="$root/lib"
[[ -d "$lib_dir" ]] || { echo "Missing bundle lib directory" >&2; exit 1; }
allowed_host=' libc.so.6 libdl.so.2 libgcc_s.so.1 libm.so.6 libpthread.so.0 librt.so.1 libstdc++.so.6 ld-linux-aarch64.so.1 ld-linux-x86-64.so.2 '
core_system=' libbrotlicommon.so.1 libbrotlidec.so.1 libcom_err.so.2 libcrypto.so.3 libcurl.so.4 libffi.so.8 libgmp.so.10 libgnutls.so.30 libgssapi_krb5.so.2 libhogweed.so.6 libidn2.so.0 libk5crypto.so.3 libkeyutils.so.1 libkrb5.so.3 libkrb5support.so.0 libldap-2.5.so.0 liblber-2.5.so.0 libnettle.so.8 libnghttp2.so.14 libp11-kit.so.0 libpsl.so.5 libresolv.so.2 librtmp.so.1 libssh.so.4 libssl.so.3 libtasn1.so.6 libunistring.so.2 libz.so.1 libzstd.so.1 '
errors=0
mapfile -t paths < <(find "$root/bin" "$lib_dir" -maxdepth 1 -type f -print | sort)
for path in "${paths[@]}"; do
  section=$(readelf -d "$path" 2>/dev/null || true)
  [[ -n "$section" ]] || continue
  bundled=" $(find "$lib_dir" -maxdepth 1 -printf '%f ' | tr '\n' ' ')"
  allowed="$allowed_host"
  [[ $(basename "$path") == vocotype-core ]] && allowed+="$core_system"
  while IFS= read -r dependency; do
    [[ "$bundled" == *" $dependency "* || "$allowed" == *" $dependency "* ]] || {
      echo "$(basename "$path"): unbundled dependency $dependency" >&2
      errors=1
    }
  done < <(sed -n 's/.*Shared library: \[\([^]]*\)\].*/\1/p' <<<"$section")
  while IFS= read -r runpath; do
    IFS=: read -ra entries <<<"$runpath"
    for entry in "${entries[@]}"; do
      [[ "$entry" != /* ]] || { echo "$(basename "$path"): absolute RUNPATH $entry" >&2; errors=1; }
    done
  done < <(sed -n 's/.*Library \(rpath\|runpath\): \[\([^]]*\)\].*/\2/p' <<<"$section")
done
(( errors == 0 )) || exit 1
echo "Native bundle audit passed: $root"
