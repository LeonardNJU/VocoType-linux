#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
BUILD=${VOCOTYPE_FEEDBACK_BUILD_DIR:-"$ROOT/build/feedback-service-release"}
PREFIX=${VOCOTYPE_FEEDBACK_PREFIX:-/opt/vocotype-feedback}
cmake -S "$ROOT/feedback_service" -B "$BUILD"   -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$PREFIX"   -DBUILD_TESTING=OFF
cmake --build "$BUILD" --parallel "${JOBS:-2}"
cmake --install "$BUILD"
