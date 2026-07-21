#!/bin/sh
set -eu

HOST=feedback.vocotype-linux.lsamc.website
SOURCE_CONFIG=${1:-/tmp/vocotype-feedback-nginx-tls.conf}
TARGET_CONFIG=/etc/nginx/sites-available/$HOST

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this script with sudo." >&2
    exit 1
fi

/usr/bin/certbot certonly --nginx \
    -d "$HOST" \
    --cert-name "$HOST" \
    --non-interactive \
    --agree-tos

install -o root -g root -m 0644 "$SOURCE_CONFIG" "$TARGET_CONFIG"
ln -sfn "$TARGET_CONFIG" "/etc/nginx/sites-enabled/$HOST"
/usr/sbin/nginx -t
/usr/sbin/service nginx reload

curl --fail --silent --show-error "https://$HOST/healthz"
echo
