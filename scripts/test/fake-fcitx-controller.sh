#!/usr/bin/env bash
set -euo pipefail

state=${VOCOTYPE_FAKE_FCITX_STATE:?}
log=${VOCOTYPE_FAKE_FCITX_LOG:?}
config=${XDG_CONFIG_HOME:?}/fcitx5/conf/vocotype.conf
printf '%s\n' "$*" >> "$log"

contains_arg() {
  local expected=$1 item
  shift
  for item in "$@"; do
    [[ "$item" == "$expected" ]] && return 0
  done
  return 1
}

if contains_arg GetConfig "$@"; then
  jq -c '{
    type: "va(sa(sssva{sv}))",
    data: [
      {type: "a{sv}", data: with_entries(.value = {type: "s", data: .value})},
      []
    ]
  }' "$state"
  exit 0
fi

if contains_arg SetConfig "$@"; then
  args=("$@")
  index=-1
  for ((i = 0; i < ${#args[@]}; ++i)); do
    if [[ ${args[$i]} == SetConfig ]]; then
      index=$i
      break
    fi
  done
  ((index >= 0))
  count=${args[$((index + 4))]}
  offset=$((index + 5))
  updated=$(cat "$state")
  for ((i = 0; i < count; ++i)); do
    key=${args[$offset]}
    type=${args[$((offset + 1))]}
    value=${args[$((offset + 2))]}
    [[ $type == s ]]
    updated=$(jq -c --arg key "$key" --arg value "$value" \
      '.[$key] = $value' <<<"$updated")
    offset=$((offset + 3))
  done
  printf '%s\n' "$updated" > "$state"
  mkdir -p "$(dirname "$config")"
  jq -r 'to_entries[] | "\(.key)=\(.value)"' "$state" > "$config"
  exit 0
fi

echo "unsupported fake busctl call: $*" >&2
exit 1
