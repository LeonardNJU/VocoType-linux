#!/bin/sh
# Open the settings center installed on the host instead of running GTK
# inside Fcitx5's sandbox.
exec flatpak-spawn --host /bin/sh -c '
  if [ -x "$HOME/.local/bin/vocotype-settings" ]; then
    exec "$HOME/.local/bin/vocotype-settings" "$@"
  fi
  if [ -x /usr/bin/vocotype-settings ]; then
    exec /usr/bin/vocotype-settings "$@"
  fi
  echo "VoCoType host settings missing; install native host runtime." >&2
  exit 78
' vocotype-host-settings "$@"
