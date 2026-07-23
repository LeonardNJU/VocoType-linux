#!/usr/bin/env bash
set -euo pipefail
SOURCE=${1:?usage: $0 SOURCE DESTINATION [--installers-only]}
DESTINATION=${2:?usage: $0 SOURCE DESTINATION [--installers-only]}
INSTALLERS_ONLY=false
[[ ${3:-} == --installers-only ]] && INSTALLERS_ONLY=true
SOURCE=$(realpath "$SOURCE")
DESTINATION=$(realpath -m "$DESTINATION")
[[ "$DESTINATION" != "$SOURCE" && "$DESTINATION" != "$SOURCE"/* ]] || { echo "destination must be outside source" >&2; exit 2; }
rm -rf "$DESTINATION"; mkdir -p "$DESTINATION"
declare -A seen=()
count=0
while IFS= read -r -d '' path; do
  name=$(basename "$path"); normalized=${name//\~/.}
  [[ "$normalized" =~ ^[A-Za-z0-9][A-Za-z0-9._+-]*$ ]] || { echo "Unsafe asset name: $name" >&2; exit 1; }
  if [[ "$INSTALLERS_ONLY" == true ]]; then
    case "$normalized" in
      *.src.rpm) continue ;;
      vocotype-linux*_amd64.deb|vocotype-linux*.x86_64.rpm|vocotype-linux*-x86_64.pkg.tar.zst) ;;
      *) continue ;;
    esac
  fi
  [[ -z ${seen[$normalized]+x} ]] || { echo "Duplicate asset: $normalized" >&2; exit 1; }
  seen[$normalized]=$path
  cp -p "$path" "$DESTINATION/$normalized"
  ((count+=1))
done < <(find "$SOURCE" -type f -print0 | sort -z)
(( count > 0 )) || { echo "No release assets collected" >&2; exit 1; }
echo "Collected $count assets in $DESTINATION"
