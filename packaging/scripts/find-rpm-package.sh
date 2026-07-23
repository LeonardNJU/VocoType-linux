#!/usr/bin/env bash
set -euo pipefail
directory=${1:?usage: $0 DIRECTORY PACKAGE_NAME}
package_name=${2:?usage: $0 DIRECTORY PACKAGE_NAME}
command -v rpm >/dev/null 2>&1 || {
  echo "rpm is required to inspect package metadata" >&2
  exit 127
}
[[ -d "$directory" ]] || {
  echo "RPM directory does not exist: $directory" >&2
  exit 2
}

matches=()
while IFS= read -r -d '' candidate; do
  name=$(rpm -qp --qf '%{NAME}' "$candidate" 2>/dev/null || true)
  arch=$(rpm -qp --qf '%{ARCH}' "$candidate" 2>/dev/null || true)
  [[ "$name" == "$package_name" && "$arch" != src ]] || continue
  matches+=("$candidate")
done < <(find "$directory" -maxdepth 1 -type f -name '*.rpm' ! -name '*.src.rpm' -print0 | sort -z)

if [[ ${#matches[@]} -ne 1 ]]; then
  echo "Expected exactly one binary RPM named $package_name; found ${#matches[@]}" >&2
  while IFS= read -r -d '' candidate; do
    printf '  %s name=%s arch=%s\n' \
      "$candidate" \
      "$(rpm -qp --qf '%{NAME}' "$candidate" 2>/dev/null || echo unreadable)" \
      "$(rpm -qp --qf '%{ARCH}' "$candidate" 2>/dev/null || echo unreadable)" >&2
  done < <(find "$directory" -maxdepth 1 -type f -name '*.rpm' ! -name '*.src.rpm' -print0 | sort -z)
  exit 1
fi
printf '%s\n' "${matches[0]}"
