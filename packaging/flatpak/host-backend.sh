#!/bin/sh
# Only start the dedicated VoCoType user service.
exec flatpak-spawn --host /usr/bin/systemctl --user start vocotype-flatpak-backend.service
