#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$ROOT"
JOBS=${JOBS:-2}

find scripts/install scripts/diagnostics scripts/test packaging/scripts packaging/tests \
  -type f -name '*.sh' -print0 | xargs -0 -n1 bash -n

scripts/test/contracts.sh

cmake -S src/core -B build/native-core \
  -DCMAKE_BUILD_TYPE=RelWithDebInfo -DBUILD_TESTING=ON
cmake --build build/native-core -j"$JOBS"
ctest --test-dir build/native-core --output-on-failure

cmake -S src/desktop -B build/native-desktop \
  -DCMAKE_BUILD_TYPE=RelWithDebInfo \
  -DVOCOTYPE_BUILD_SETTINGS=ON \
  -DVOCOTYPE_BUILD_IBUS=OFF \
  -DVOCOTYPE_BUILD_RIME=OFF \
  -DBUILD_TESTING=ON
cmake --build build/native-desktop -j"$JOBS"
ctest --test-dir build/native-desktop --output-on-failure
scripts/test/hotkey-settings.sh build/native-desktop/vocotype-settings

cmake -S src/integrations/fcitx5/module -B build/fcitx-module \
  -DCMAKE_BUILD_TYPE=RelWithDebInfo
cmake --build build/fcitx-module -j"$JOBS"

cmake -S src/services/feedback -B build/feedback-service \
  -DCMAKE_BUILD_TYPE=RelWithDebInfo -DBUILD_TESTING=ON
cmake --build build/feedback-service -j"$JOBS"
ctest --test-dir build/feedback-service --output-on-failure

"$ROOT/scripts/site/build.sh" "$ROOT/build/site-test"
test -f "$ROOT/build/site-test/docs/index.html"
test -f "$ROOT/build/site-test/docs/guides/voice-editing.html"

git diff --check
echo NATIVE_TEST_SUITE_OK
