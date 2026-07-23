#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
. "$ROOT/packaging/tools/package-common.sh"
FORMAT= FLAVOR= TEMPLATE= OUTPUT=
while [[ $# -gt 0 ]]; do
  case "$1" in
    --format) FORMAT=${2:?}; shift 2 ;;
    --flavor) FLAVOR=${2:?}; shift 2 ;;
    --template) TEMPLATE=${2:?}; shift 2 ;;
    --output) OUTPUT=${2:?}; shift 2 ;;
    -h|--help) echo "Usage: $0 --format debian|rpm|arch --flavor FLAVOR --template FILE --output FILE"; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ -n "$FORMAT" && -n "$FLAVOR" && -f "$TEMPLATE" && -n "$OUTPUT" ]] || { echo "missing renderer argument" >&2; exit 2; }
FLAVOR=$(vocotype_flavor "$FLAVOR")
PACKAGE_NAME=$(vocotype_flavor_field "$FLAVOR" package_name)
TITLE=$(vocotype_flavor_field "$FLAVOR" title)
SUMMARY=$(vocotype_flavor_field "$FLAVOR" summary)
INCLUDES_IBUS=$(vocotype_flavor_field "$FLAVOR" includes_ibus)
INCLUDES_FCITX=$(vocotype_flavor_field "$FLAVOR" includes_fcitx5)
read -r -a CONFLICT_LIST <<<"$(vocotype_flavor_field "$FLAVOR" conflicts)"
join_by() { local delimiter=$1; shift; local result= first=true; for value in "$@"; do if [[ "$first" == true ]]; then result=$value; first=false; else result+="$delimiter$value"; fi; done; printf '%s' "$result"; }
escape_sed() { printf '%s' "$1" | sed -e 's/[&|\\]/\\&/g'; }

case "$FORMAT" in
  debian)
    build=("debhelper-compat (= 13)" cmake g++ pkg-config portaudio19-dev libgtk-3-dev libyaml-cpp-dev libcurl4-openssl-dev libssl-dev nlohmann-json3-dev)
    depends=('${shlibs:Depends}' '${misc:Depends}' libgtk-3-0 libportaudio2)
    [[ "$INCLUDES_IBUS" == true ]] && { build+=(libibus-1.0-dev librime-dev); depends+=(ibus librime1 librime-data rime-data-luna-pinyin); }
    [[ "$INCLUDES_FCITX" == true ]] && { build+=(libfcitx5core-dev); depends+=(fcitx5); }
    build_text=$(join_by ', ' "${build[@]}")
    depends_text=$(join_by ', ' "${depends[@]}")
    conflicts_text=$(join_by ', ' "${CONFLICT_LIST[@]}")
    sed \
      -e "s|@PACKAGE_NAME@|$(escape_sed "$PACKAGE_NAME")|g" \
      -e "s|@BUILD_DEPENDS@|$(escape_sed "$build_text")|g" \
      -e "s|@DEPENDS@|$(escape_sed "$depends_text")|g" \
      -e "s|@CONFLICTS@|$(escape_sed "$conflicts_text")|g" \
      -e "s|@SUMMARY@|$(escape_sed "$SUMMARY")|g" \
      -e "s|@TITLE@|$(escape_sed "$TITLE")|g" \
      "$TEMPLATE" > "$OUTPUT"
    ;;
  rpm)
    build=(
      'BuildRequires:  cmake' 'BuildRequires:  gcc-c++'
      'BuildRequires:  pkgconfig' 'BuildRequires:  systemd-rpm-macros'
      'BuildRequires:  portaudio-devel' 'BuildRequires:  gtk3-devel'
      'BuildRequires:  yaml-cpp-devel' 'BuildRequires:  libcurl-devel'
      'BuildRequires:  openssl-devel' 'BuildRequires:  nlohmann-json-devel'
    )
    requires=('Requires:       gtk3' 'Requires:       portaudio' 'Requires:       yaml-cpp' 'Requires:       libcurl-full')
    files=()
    if [[ "$INCLUDES_IBUS" == true ]]; then
      build+=('BuildRequires:  ibus-devel' 'BuildRequires:  librime-devel')
      requires+=('Requires:       ibus' 'Requires:       librime' 'Requires:       brise')
      files+=('%{_libexecdir}/vocotype-ibus-engine' '%{_datadir}/ibus/component/vocotype.xml')
    fi
    if [[ "$INCLUDES_FCITX" == true ]]; then
      build+=('BuildRequires:  fcitx5-devel')
      requires+=('Requires:       fcitx5')
      files+=('%{_bindir}/vocotype-fcitx5-backend' '%{_bindir}/vocotype-fcitx5-recorder' '%{_libdir}/fcitx5/vocotype.so' '%{_datadir}/fcitx5/addon/vocotype.conf' '%{_userunitdir}/vocotype-fcitx5-backend.service')
    fi
    conflicts=(); for item in "${CONFLICT_LIST[@]}"; do conflicts+=("Conflicts:      $item"); done
    : > "$OUTPUT"
    while IFS= read -r line || [[ -n "$line" ]]; do
      case "$line" in
        '@BUILD_REQUIRES@') printf '%s\n' "${build[@]}" >> "$OUTPUT" ;;
        '@REQUIRES@') printf '%s\n' "${requires[@]}" >> "$OUTPUT" ;;
        '@CONFLICTS@') printf '%s\n' "${conflicts[@]}" >> "$OUTPUT" ;;
        '@FRAMEWORK_FILES@') printf '%s\n' "${files[@]}" >> "$OUTPUT" ;;
        *)
          line=${line//@PACKAGE_NAME@/$PACKAGE_NAME}
          line=${line//@FLAVOR@/$FLAVOR}
          line=${line//@SUMMARY@/$SUMMARY}
          line=${line//@TITLE@/$TITLE}
          printf '%s\n' "$line" >> "$OUTPUT"
          ;;
      esac
    done < "$TEMPLATE"
    ;;
  arch)
    depends=(gtk3 portaudio yaml-cpp curl openssl)
    makedepends=(cmake gcc pkgconf nlohmann-json)
    [[ "$INCLUDES_IBUS" == true ]] && depends+=(ibus librime librime-data)
    [[ "$INCLUDES_FCITX" == true ]] && depends+=(fcitx5)
    quote_list() { local value result=; for value in "$@"; do result+=" '$value'"; done; printf '%s' "${result# }"; }
    sed \
      -e "s|@PACKAGE_NAME@|$(escape_sed "$PACKAGE_NAME")|g" \
      -e "s|@FLAVOR@|$(escape_sed "$FLAVOR")|g" \
      -e "s|@SUMMARY@|$(escape_sed "$SUMMARY")|g" \
      -e "s|@DEPENDS@|$(escape_sed "$(quote_list "${depends[@]}")")|g" \
      -e "s|@MAKEDEPENDS@|$(escape_sed "$(quote_list "${makedepends[@]}")")|g" \
      -e "s|@CONFLICTS@|$(escape_sed "$(quote_list "${CONFLICT_LIST[@]}")")|g" \
      "$TEMPLATE" > "$OUTPUT"
    ;;
  *) echo "Unknown format: $FORMAT" >&2; exit 2 ;;
esac
if grep -Eq '@(PACKAGE_NAME|BUILD_DEPENDS|DEPENDS|CONFLICTS|SUMMARY|TITLE|FLAVOR|MAKEDEPENDS|REQUIRES|BUILD_REQUIRES|FRAMEWORK_FILES)@' "$OUTPUT"; then
  echo "Unresolved package placeholders" >&2
  exit 1
fi
