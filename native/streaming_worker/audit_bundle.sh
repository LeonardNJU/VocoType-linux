#!/usr/bin/env bash
set -euo pipefail

mode=portable
if [[ ${1:-} == --nix-store ]]; then
  mode=nix-store
  shift
fi

root=${1:?usage: $0 [--nix-store] BUNDLE_DIR}
root=$(realpath "$root")
lib_dir="$root/lib"
[[ -d "$lib_dir" ]] || { echo "Missing bundle lib directory" >&2; exit 1; }
allowed_host=' libc.so.6 libdl.so.2 libgcc_s.so.1 libm.so.6 libpthread.so.0 librt.so.1 libstdc++.so.6 ld-linux-aarch64.so.1 ld-linux-x86-64.so.2 '
errors=0
mapfile -t paths < <(find "$root/bin" "$lib_dir" -maxdepth 1 -type f -print | sort)
for path in "${paths[@]}"; do
  section=$(readelf -d "$path" 2>/dev/null || true)
  [[ -n "$section" ]] || continue
  bundled=" $(find "$lib_dir" -maxdepth 1 -printf '%f ' | tr '\n' ' ')"
  allowed="$allowed_host"
  while IFS= read -r dependency; do
    if [[ "$bundled" == *" $dependency "* || "$allowed" == *" $dependency "* ]]; then
      continue
    fi
    if [[ "$mode" == nix-store ]]; then
      continue
    fi
    echo "$(basename "$path"): unbundled dependency $dependency" >&2
    errors=1
  done < <(sed -n 's/.*Shared library: \[\([^]]*\)\].*/\1/p' <<<"$section")

  if [[ "$mode" == nix-store ]]; then
    unresolved=$(ldd "$path" 2>/dev/null | sed -n '/not found/p' || true)
    if [[ -n "$unresolved" ]]; then
      echo "$(basename "$path"): unresolved Nix dependency: $unresolved" >&2
      errors=1
    fi
  fi

  while IFS= read -r runpath; do
    IFS=: read -ra entries <<<"$runpath"
    for entry in "${entries[@]}"; do
      if [[ "$entry" != /* ]]; then
        continue
      fi
      if [[ "$mode" == nix-store && "$entry" == /nix/store/* ]]; then
        continue
      fi
      echo "$(basename "$path"): absolute RUNPATH $entry" >&2
      errors=1
    done
  done < <(sed -n 's/.*Library \(rpath\|runpath\): \[\([^]]*\)\].*/\2/p' <<<"$section")
done
(( errors == 0 )) || exit 1
echo "Native bundle audit passed: $root mode=$mode"
