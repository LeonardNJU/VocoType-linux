#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
OUTPUT=${1:-"$ROOT/build/site"}
BUILD_DIR=${VOCOTYPE_DOCS_BUILD_DIR:-"$ROOT/build/docs-builder"}
mkdir -p "$BUILD_DIR"
${CXX:-c++} -std=c++20 -O2 -Wall -Wextra -Wpedantic \
  "$ROOT/tools/docs_builder.cpp" -o "$BUILD_DIR/vocotype-docs-builder"
rm -rf "$OUTPUT"
mkdir -p "$OUTPUT"
cp -a "$ROOT/site/." "$OUTPUT/"
rm -rf "$OUTPUT/docs"
"$BUILD_DIR/vocotype-docs-builder" "$ROOT/docs" "$OUTPUT/docs"
test -f "$OUTPUT/index.html"
test -f "$OUTPUT/docs/index.html"
test -f "$OUTPUT/docs/guides/settings-center.html"
if find "$OUTPUT" -type f -name '*.md' -print -quit | grep -q .; then
  echo "Static site unexpectedly contains Markdown sources" >&2
  exit 1
fi
if rg -n '<p>(```|===|!!!|\?\?\?|:::)|=== &quot;|```</p>' "$OUTPUT/docs"; then
  echo "Static documentation leaked unrendered Markdown syntax" >&2
  exit 1
fi
installation="$OUTPUT/docs/getting-started/installation.html"
grep -Fq '<pre><code class="language-bash">sudo apt install ./vocotype-linux-fcitx5_*.deb' "$installation"
grep -Fq '<pre><code class="language-bash">sudo dnf install ./vocotype-linux-fcitx5-*.rpm' "$installation"
grep -Fq '<pre><code class="language-bash">sudo pacman -U ./vocotype-linux-fcitx5-*.pkg.tar.zst' "$installation"
hibernate="$OUTPUT/docs/troubleshooting/hibernate.html"
grep -Fq '<pre><code class="language-bash">systemctl --user restart vocotype-fcitx5-backend.service' "$hibernate"
echo "STATIC_SITE_OK $OUTPUT"
