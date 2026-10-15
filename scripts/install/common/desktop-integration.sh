#!/usr/bin/env bash
# Shared by source installs and per-user activation of system packages.
# shellcheck shell=bash

desktop_exec_path() {
  local value=$1
  value=${value//\\/\\\\}
  value=${value//\"/\\\"}
  value=${value//\$/\\\$}
  value=${value//\`/\\\`}
  value=${value//%/%%}
  # Desktop Entry string escaping is applied before Exec argument unquoting.
  value=${value//\\/\\\\}
  printf '"%s"' "$value"
}

desktop_string_value() {
  local value=$1
  value=${value//\\/\\\\}
  value=${value//$'\n'/\\n}
  value=${value//$'\r'/\\r}
  value=${value//$'\t'/\\t}
  printf '%s' "$value"
}

install_desktop_entries() {
  local root=$1 settings=$2
  local data="${XDG_DATA_HOME:-$HOME/.local/share}"
  local desktop=io.github.LeonardNJU.VoCoType.Settings.desktop
  local icon="$data/icons/hicolor/192x192/apps/vocotype.png"
  mkdir -p "$data/applications" "$data/icons/hicolor/192x192/apps"
  install -m644 "$root/web/icon-192.png" "$icon"
  # Use an absolute Icon path so KDE does not need a refreshed icon-theme cache.
  # The configured command must work even when ~/.local/bin is absent from PATH.
  # env also avoids GLib testing an unexpanded %% in the executable filename.
  while IFS= read -r line; do
    case "$line" in
      Icon=*) printf 'Icon=%s\n' "$(desktop_string_value "$icon")" ;;
      Exec=*) printf 'Exec=/usr/bin/env %s\n' "$(desktop_exec_path "$settings")" ;;
      *) printf '%s\n' "$line" ;;
    esac
  done < "$root/resources/desktop/$desktop" > "$data/applications/$desktop"
  if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$data/applications" >/dev/null 2>&1 || true
  fi
  if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t "$data/icons/hicolor" >/dev/null 2>&1 || true
  fi
}

remove_desktop_entries() {
  local data="${XDG_DATA_HOME:-$HOME/.local/share}"
  command rm -f "$data/applications/io.github.LeonardNJU.VoCoType.Settings.desktop" \
    "$data/icons/hicolor/192x192/apps/vocotype.png"
  if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$data/applications" >/dev/null 2>&1 || true
  fi
}
