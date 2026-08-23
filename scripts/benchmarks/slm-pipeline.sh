#!/usr/bin/env bash
set -euo pipefail
if [[ -n "${VOCOTYPE_SOCKET_PATH:-}" ]]; then
  SOCKET=$VOCOTYPE_SOCKET_PATH
elif [[ -n "${VOCOTYPE_FCITX5_SOCKET:-}" ]]; then
  SOCKET=$VOCOTYPE_FCITX5_SOCKET
elif [[ -n "${XDG_RUNTIME_DIR:-}" ]]; then
  SOCKET=$XDG_RUNTIME_DIR/vocotype-fcitx5.sock
else
  SOCKET=/tmp/vocotype-fcitx5-$(id -u).sock
fi
REPEAT=3
DISABLE_SLM=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --repeat) REPEAT=${2:?}; shift 2 ;;
    --disable-slm) DISABLE_SLM=true; shift ;;
    -h|--help) echo "Usage: $0 [--repeat N] [--disable-slm] AUDIO.wav..."; exit 0 ;;
    --*) echo "Unsupported option: $1" >&2; exit 2 ;;
    *) break ;;
  esac
done
(( $# > 0 )) || { echo "At least one WAV file is required" >&2; exit 2; }
command -v socat >/dev/null || { echo "socat is required" >&2; exit 127; }
command -v jq >/dev/null || { echo "jq is required" >&2; exit 127; }
[[ -S "$SOCKET" ]] || { echo "Native core socket is not ready: $SOCKET" >&2; exit 1; }
request() { printf '%s' "$1" | socat -t 180 - UNIX-CONNECT:"$SOCKET"; }
for audio in "$@"; do
  [[ -f "$audio" ]] || { echo "Missing audio: $audio" >&2; continue; }
  for ((run=1; run<=REPEAT; ++run)); do
    start=$(date +%s%N)
    result=$(request "$(jq -nc --arg path "$(realpath "$audio")" '{type:"transcribe",audio_path:$path,long_mode:false}')")
    end=$(date +%s%N)
    text=$(jq -r '.text // .error // ""' <<<"$result")
    asr_ms=$(( (end-start)/1000000 ))
    printf '%s\trun=%d\tasr_ms=%d\t%s\n' "$audio" "$run" "$asr_ms" "$text"
    if [[ "$DISABLE_SLM" != true && $(jq -r '.success // false' <<<"$result") == true ]]; then
      start=$(date +%s%N)
      polished=$(request "$(jq -nc --arg text "$text" '{type:"polish_text",text:$text}')")
      end=$(date +%s%N)
      printf '%s\trun=%d\tslm_ms=%d\t%s\n' "$audio" "$run" "$(( (end-start)/1000000 ))" "$(jq -r '.text // .error // ""' <<<"$polished")"
    fi
  done
done
