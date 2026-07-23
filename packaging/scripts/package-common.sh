#!/usr/bin/env bash
# Shared native package metadata. Safe to source under `set -u`.

vocotype_root() {
  cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd
}

vocotype_version() {
  local root=${1:-$(vocotype_root)} value
  value=$(tr -d '[:space:]' < "$root/VERSION")
  [[ "$value" =~ ^[0-9]+\.[0-9]+\.[0-9]+((b|rc)[1-9][0-9]*)?$ ]] || {
    echo "Invalid VERSION: $value" >&2
    return 2
  }
  printf '%s\n' "$value"
}

vocotype_version_from_tag() {
  local tag=$1
  if [[ "$tag" =~ ^v([0-9]+\.[0-9]+\.[0-9]+)-beta\.([1-9][0-9]*)$ ]]; then
    printf '%sb%s\n' "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}"
  elif [[ "$tag" =~ ^v([0-9]+\.[0-9]+\.[0-9]+)-rc\.([1-9][0-9]*)$ ]]; then
    printf '%src%s\n' "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}"
  elif [[ "$tag" =~ ^v([0-9]+\.[0-9]+\.[0-9]+)$ ]]; then
    printf '%s\n' "${BASH_REMATCH[1]}"
  else
    echo "Invalid release tag: $tag" >&2
    return 2
  fi
}

vocotype_version_field() {
  local version=$1 field=${2:-native} base stage serial
  if [[ "$version" =~ ^([0-9]+\.[0-9]+\.[0-9]+)(b|rc)([1-9][0-9]*)$ ]]; then
    base=${BASH_REMATCH[1]}; stage=${BASH_REMATCH[2]}; serial=${BASH_REMATCH[3]}
  elif [[ "$version" =~ ^([0-9]+\.[0-9]+\.[0-9]+)$ ]]; then
    base=${BASH_REMATCH[1]}; stage=; serial=
  else
    echo "Invalid version: $version" >&2
    return 2
  fi
  case "$field" in
    native|arch) printf '%s\n' "$version" ;;
    base|rpm_version) printf '%s\n' "$base" ;;
    tag)
      if [[ -z "$stage" ]]; then printf 'v%s\n' "$base"
      elif [[ "$stage" == b ]]; then printf 'v%s-beta.%s\n' "$base" "$serial"
      else printf 'v%s-rc.%s\n' "$base" "$serial"; fi ;;
    debian)
      if [[ -z "$stage" ]]; then printf '%s\n' "$base"
      elif [[ "$stage" == b ]]; then printf '%s~beta%s\n' "$base" "$serial"
      else printf '%s~rc%s\n' "$base" "$serial"; fi ;;
    rpm_release)
      if [[ -z "$stage" ]]; then printf '1\n'
      elif [[ "$stage" == b ]]; then printf '0.beta%s\n' "$serial"
      else printf '0.rc%s\n' "$serial"; fi ;;
    prerelease) [[ -n "$stage" ]] && printf 'true\n' || printf 'false\n' ;;
    *) echo "Unknown version field: $field" >&2; return 2 ;;
  esac
}

vocotype_flavor() {
  local value=${1,,}
  case "$value" in
    universal|all|both) printf 'universal\n' ;;
    ibus) printf 'ibus\n' ;;
    fcitx|fcitx5) printf 'fcitx5\n' ;;
    *) echo "Unknown package flavor: $1" >&2; return 2 ;;
  esac
}

vocotype_flavor_field() {
  local flavor; flavor=$(vocotype_flavor "$1")
  local field=$2
  case "$flavor:$field" in
    universal:flavor|ibus:flavor|fcitx5:flavor) printf '%s\n' "$flavor" ;;
    universal:package_name) printf 'vocotype-linux\n' ;;
    ibus:package_name) printf 'vocotype-linux-ibus\n' ;;
    fcitx5:package_name) printf 'vocotype-linux-fcitx5\n' ;;
    universal:title) printf 'IBus and Fcitx 5\n' ;;
    ibus:title) printf 'IBus\n' ;;
    fcitx5:title) printf 'Fcitx 5\n' ;;
    universal:summary) printf 'Offline voice input for IBus and Fcitx 5\n' ;;
    ibus:summary) printf 'Offline voice input for IBus\n' ;;
    fcitx5:summary) printf 'Offline voice input for Fcitx 5\n' ;;
    universal:includes_ibus|ibus:includes_ibus) printf 'true\n' ;;
    fcitx5:includes_ibus) printf 'false\n' ;;
    universal:includes_fcitx5|fcitx5:includes_fcitx5) printf 'true\n' ;;
    ibus:includes_fcitx5) printf 'false\n' ;;
    universal:conflicts) printf 'vocotype-linux-ibus vocotype-linux-fcitx5\n' ;;
    ibus:conflicts) printf 'vocotype-linux vocotype-linux-fcitx5\n' ;;
    fcitx5:conflicts) printf 'vocotype-linux vocotype-linux-ibus\n' ;;
    *) echo "Unknown flavor field: $field" >&2; return 2 ;;
  esac
}
