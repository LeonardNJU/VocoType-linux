#!/usr/bin/env bash
set -euo pipefail

manager=${1:?usage: $0 MANAGER REQUIRES_FILE [PROVIDES_FILE]}
requires_file=${2:?usage: $0 MANAGER REQUIRES_FILE [PROVIDES_FILE]}
provides_file=${3:-/dev/null}

case "$manager" in
  apt|pacman)
    if grep -Eqi '(^|[^[:alnum:]-])(lib)?yaml-cpp([0-9.:+~_-]|$)' "$requires_file"; then
      echo "package metadata depends on distribution yaml-cpp ABI" >&2
      cat "$requires_file" >&2
      exit 1
    fi
    ;;
  dnf)
    if grep -Eqi '^yaml-cpp([[:space:]<>=(:]|$)' "$requires_file"; then
      echo "RPM metadata explicitly depends on the distribution yaml-cpp package" >&2
      cat "$requires_file" >&2
      exit 1
    fi
    while IFS= read -r capability; do
      [[ -n "$capability" ]] || continue
      if ! grep -Fqx -- "$capability" "$provides_file"; then
        echo "RPM metadata requires an unprovided private yaml-cpp capability: $capability" >&2
        exit 1
      fi
    done < <(grep -E '^libyaml-cpp\.so([.0-9]+)?' "$requires_file" || true)
    ;;
  *)
    echo "Unsupported package manager: $manager" >&2
    exit 2
    ;;
esac
