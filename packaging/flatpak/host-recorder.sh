#!/bin/sh
# Audio capture happens on the host, not inside Fcitx5's sandbox.
# Forward arguments as positional parameters, never via eval.
exec flatpak-spawn --host /bin/sh -c '
  if [ -x "$HOME/.local/bin/vocotype-fcitx5-recorder" ]; then
    exec "$HOME/.local/bin/vocotype-fcitx5-recorder" "$@"
  fi
  if [ -x /usr/bin/vocotype-fcitx5-recorder ]; then
    exec /usr/bin/vocotype-fcitx5-recorder "$@"
  fi
  echo "VoCoType host recorder missing; install native host runtime." >&2
  exit 78
' vocotype-host-recorder "$@"
