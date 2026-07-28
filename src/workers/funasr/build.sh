#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT_DIR=$(cd -- "$SCRIPT_DIR/../.." && pwd)
CACHE_DIR=${VOCOTYPE_NATIVE_CACHE_DIR:-"$PROJECT_DIR/.cache/native-streaming"}
BUILD_DIR=${VOCOTYPE_NATIVE_BUILD_DIR:-"$SCRIPT_DIR/build"}
FUNASR_COMMIT=${VOCOTYPE_FUNASR_COMMIT:-bd6e72142f1cca3c30b7651bf5fa567dfe969810}
FUNASR_REPOSITORY=${VOCOTYPE_FUNASR_REPOSITORY:-https://github.com/modelscope/FunASR.git}
FUNASR_SOURCE_DIR=${FUNASR_SOURCE_DIR:-"$CACHE_DIR/FunASR"}
ONNXRUNTIME_DIR=${ONNXRUNTIME_DIR:-}
ONNXRUNTIME_VERSION=${VOCOTYPE_ONNXRUNTIME_VERSION:-1.23.2}
ONNXRUNTIME_SHA256=${VOCOTYPE_ONNXRUNTIME_SHA256:-}
HOST_OS=$(uname -s)
HOST_ARCH=$(uname -m)

sha256_check() {
    local expected=$1
    local path=$2
    if command -v sha256sum >/dev/null 2>&1; then
        printf '%s  %s\n' "$expected" "$path" | sha256sum -c -
    else
        local actual
        actual=$(shasum -a 256 "$path" | awk '{print $1}')
        [[ "$actual" == "$expected" ]] || {
            echo "SHA-256 mismatch for $path: expected $expected, got $actual" >&2
            return 1
        }
    fi
}

if [[ "$HOST_OS" == Darwin ]]; then
    SHARED_LIBRARY_SUFFIX=dylib
    ORT_LIBRARY_NAME=libonnxruntime.dylib
else
    SHARED_LIBRARY_SUFFIX=so
    ORT_LIBRARY_NAME=libonnxruntime.so
fi

mkdir -p "$CACHE_DIR" "$BUILD_DIR"

# Current FunASR's bundled Kaldi/OpenFST builds most reliably with Clang on
# rolling distributions. Respect an explicit toolchain, otherwise prefer it.
if [[ -z "${CC:-}" && -z "${CXX:-}" ]] && command -v clang >/dev/null 2>&1 && command -v clang++ >/dev/null 2>&1; then
    export CC=clang
    export CXX=clang++
fi

if [[ ! -f "$FUNASR_SOURCE_DIR/runtime/onnxruntime/CMakeLists.txt" ]]; then
    rm -rf "$FUNASR_SOURCE_DIR"
    git clone --filter=blob:none --no-checkout "$FUNASR_REPOSITORY" "$FUNASR_SOURCE_DIR"
    git -C "$FUNASR_SOURCE_DIR" sparse-checkout init --cone
    git -C "$FUNASR_SOURCE_DIR" sparse-checkout set runtime/onnxruntime
    git -C "$FUNASR_SOURCE_DIR" checkout "$FUNASR_COMMIT"
fi

if [[ -z "$ONNXRUNTIME_DIR" ]]; then
    case "$HOST_OS:$HOST_ARCH" in
        Linux:x86_64|Linux:amd64)
            ort_platform=linux
            ort_arch=x64
            default_ort_sha256=1fa4dcaef22f6f7d5cd81b28c2800414350c10116f5fdd46a2160082551c5f9b
            ;;
        Linux:aarch64|Linux:arm64)
            ort_platform=linux
            ort_arch=aarch64
            default_ort_sha256=7c63c73560ed76b1fac6cff8204ffe34fe180e70d6582b5332ec094810241e5c
            ;;
        Darwin:arm64)
            ort_platform=osx
            ort_arch=arm64
            default_ort_sha256=b4d513ab2b26f088c66891dbbc1408166708773d7cc4163de7bdca0e9bbb7856
            ;;
        Darwin:x86_64)
            ort_platform=osx
            ort_arch=x86_64
            default_ort_sha256=
            ;;
        *)
            echo "Unsupported native ASR platform: $HOST_OS $HOST_ARCH" >&2
            exit 2
            ;;
    esac
    if [[ "$ONNXRUNTIME_VERSION" != "1.23.2" && -z "$ONNXRUNTIME_SHA256" ]]; then
        echo "Set VOCOTYPE_ONNXRUNTIME_SHA256 when overriding ONNX Runtime version." >&2
        exit 2
    fi
    if [[ -z "$ONNXRUNTIME_SHA256" && -z "$default_ort_sha256" ]]; then
        echo "Set VOCOTYPE_ONNXRUNTIME_SHA256 for $HOST_OS $HOST_ARCH." >&2
        exit 2
    fi
    ONNXRUNTIME_SHA256=${ONNXRUNTIME_SHA256:-$default_ort_sha256}
    ort_archive="onnxruntime-${ort_platform}-${ort_arch}-${ONNXRUNTIME_VERSION}.tgz"
    ort_archive_path="$CACHE_DIR/$ort_archive"
    ort_prefix="$CACHE_DIR/onnxruntime-${ONNXRUNTIME_VERSION}-${ort_platform}-${ort_arch}"
    if [[ ! -f "$ort_archive_path" ]]; then
        rm -f "$ort_archive_path.part"
        ort_url="https://github.com/microsoft/onnxruntime/releases/download/v${ONNXRUNTIME_VERSION}/${ort_archive}"
        if command -v curl >/dev/null 2>&1 &&
           curl -fL --retry 3 --retry-all-errors "$ort_url" -o "$ort_archive_path.part"; then
            mv "$ort_archive_path.part" "$ort_archive_path"
        elif command -v wget >/dev/null 2>&1 &&
             wget -O "$ort_archive_path.part" "$ort_url"; then
            mv "$ort_archive_path.part" "$ort_archive_path"
        elif command -v gh >/dev/null 2>&1; then
            rm -f "$ort_archive_path.part"
            gh release download "v${ONNXRUNTIME_VERSION}" \
                --repo microsoft/onnxruntime --pattern "$ort_archive" \
                --dir "$CACHE_DIR"
        else
            echo "Unable to download the pinned ONNX Runtime SDK." >&2
            exit 2
        fi
    fi
    sha256_check "$ONNXRUNTIME_SHA256" "$ort_archive_path"
    if [[ ! -f "$ort_prefix/include/onnxruntime_cxx_api.h" ||
          ! -e "$ort_prefix/lib/$ORT_LIBRARY_NAME" ]]; then
        rm -rf "$ort_prefix.tmp" "$ort_prefix"
        mkdir -p "$ort_prefix.tmp"
        tar -xzf "$ort_archive_path" -C "$ort_prefix.tmp"
        if [[ ! -d "$ort_prefix.tmp/include" ]]; then
            extracted_root=$(find "$ort_prefix.tmp" -mindepth 1 -maxdepth 1 -type d | head -n 1)
            extracted_count=$(find "$ort_prefix.tmp" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')
            if [[ "$extracted_count" != 1 || ! -d "$extracted_root/include" ]]; then
                echo "Unexpected ONNX Runtime archive layout: $ort_archive" >&2
                exit 2
            fi
            staging="$ort_prefix.tmp.normalized"
            rm -rf "$staging"
            mv "$extracted_root" "$staging"
            rm -rf "$ort_prefix.tmp"
            mv "$staging" "$ort_prefix.tmp"
        fi
        mv "$ort_prefix.tmp" "$ort_prefix"
    fi
    ONNXRUNTIME_DIR="$ort_prefix"
fi

if [[ ! -f "$ONNXRUNTIME_DIR/include/onnxruntime_cxx_api.h" ]]; then
    ort_include=""
    if [[ -f "$ONNXRUNTIME_DIR/include/onnxruntime/onnxruntime_cxx_api.h" ]]; then
        ort_include="$ONNXRUNTIME_DIR/include/onnxruntime"
    elif [[ -f "$ONNXRUNTIME_DIR/include/onnxruntime/core/session/onnxruntime_cxx_api.h" ]]; then
        ort_include="$ONNXRUNTIME_DIR/include/onnxruntime/core/session"
    fi
    if [[ -n "$ort_include" && -e "$ONNXRUNTIME_DIR/lib/$ORT_LIBRARY_NAME" ]]; then
        original_prefix="$ONNXRUNTIME_DIR"
        rm -rf "$CACHE_DIR/ort-compat"
        mkdir -p "$CACHE_DIR/ort-compat/include"
        cp -a "$ort_include"/. "$CACHE_DIR/ort-compat/include/"
        ln -sfn "$original_prefix/lib" "$CACHE_DIR/ort-compat/lib"
        ONNXRUNTIME_DIR="$CACHE_DIR/ort-compat"
    fi
fi

if [[ ! -f "$ONNXRUNTIME_DIR/include/onnxruntime_cxx_api.h" ||
      ! -e "$ONNXRUNTIME_DIR/lib/$ORT_LIBRARY_NAME" ]]; then
    echo "Invalid ONNXRUNTIME_DIR: $ONNXRUNTIME_DIR" >&2
    exit 2
fi
# FunASR includes ONNX Runtime as <onnxruntime/...>, while official SDKs
# expose headers directly under include/. Provide a local compatibility view.
if [[ ! -e "$ONNXRUNTIME_DIR/include/onnxruntime" ]]; then
    ln -s . "$ONNXRUNTIME_DIR/include/onnxruntime"
fi

RUNTIME_SOURCE="$FUNASR_SOURCE_DIR/runtime/onnxruntime"
OVERLAY="$BUILD_DIR/vocotype-worker-overlay.cmake"
sed \
    -e "s#@VOCOTYPE_STREAMING_WORKER_SOURCE@#$SCRIPT_DIR/worker.cpp#g" \
    -e "s#@VOCOTYPE_OFFLINE_WORKER_SOURCE@#$SCRIPT_DIR/offline_worker.cpp#g" \
    "$SCRIPT_DIR/funasr-bin-overlay.cmake" > "$OVERLAY"

# Work in a copy so an upstream checkout is never modified.  Appending one
# target keeps all inference code in the official libfunasr implementation.
SOURCE_COPY="$BUILD_DIR/funasr-source"
rm -rf "$SOURCE_COPY"
cp -a "$RUNTIME_SOURCE" "$SOURCE_COPY"
# Fixed-output sources in the Nix store are read-only. Project overlays are
# applied only to this disposable copy, so make the copy writable explicitly.
chmod -R u+w "$SOURCE_COPY"
cat "$OVERLAY" >> "$SOURCE_COPY/bin/CMakeLists.txt"
# Upstream's bundled Kaldi/OpenFST snapshot predates modern GCC/Clang. Apply
# only mechanical toolchain fixes in the disposable source copy; online ASR
# inference code remains byte-for-byte upstream.
patch -d "$SOURCE_COPY" -p1 < "$SCRIPT_DIR/funasr-toolchain-compat.patch"
# Prefer an explicitly supplied or installed nlohmann-json include root;
# otherwise upstream CMake will fetch its pinned release as usual.
NLOHMANN_JSON_INCLUDE_DIR=${NLOHMANN_JSON_INCLUDE_DIR:-}
if [[ -z "$NLOHMANN_JSON_INCLUDE_DIR" && -f /usr/include/nlohmann/json.hpp ]]; then
    NLOHMANN_JSON_INCLUDE_DIR=/usr/include
elif [[ -z "$NLOHMANN_JSON_INCLUDE_DIR" ]] && command -v brew >/dev/null 2>&1; then
    brew_json=$(brew --prefix nlohmann-json 2>/dev/null || true)
    if [[ -n "$brew_json" && -f "$brew_json/include/nlohmann/json.hpp" ]]; then
        NLOHMANN_JSON_INCLUDE_DIR="$brew_json/include"
    fi
fi
if [[ -n "$NLOHMANN_JSON_INCLUDE_DIR" &&
      -f "$NLOHMANN_JSON_INCLUDE_DIR/nlohmann/json.hpp" ]]; then
    mkdir -p "$SOURCE_COPY/third_party/json/include/nlohmann"
    cp -a "$NLOHMANN_JSON_INCLUDE_DIR/nlohmann/."         "$SOURCE_COPY/third_party/json/include/nlohmann/"
    touch "$SOURCE_COPY/third_party/json/ChangeLog.md"
fi

rm -rf "$BUILD_DIR/cmake"
cmake -S "$SOURCE_COPY" -B "$BUILD_DIR/cmake" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_CXX_STANDARD=17 \
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
    -DONNXRUNTIME_DIR="$ONNXRUNTIME_DIR" \
    -DENABLE_FFMPEG=OFF \
    -DGPU=OFF \
    -DBUILD_TESTING=OFF \
    -DWITH_GFLAGS=OFF \
    -DWITH_UNWIND=OFF \
    -DHAVE_BIN=OFF \
    -DHAVE_SCRIPT=OFF \
    -DHAVE_FAR=OFF \
    -DHAVE_GRM=OFF \
    -DHAVE_PDT=OFF \
    -DHAVE_MPDT=OFF \
    -DHAVE_NGRAM=OFF
cmake --build "$BUILD_DIR/cmake" \
    --target vocotype-streaming-worker vocotype-offline-worker \
    -j"${JOBS:-2}"

BUNDLE_DIR="$BUILD_DIR/bundle"
rm -rf "$BUNDLE_DIR"
mkdir -p "$BUNDLE_DIR/bin" "$BUNDLE_DIR/lib"
cp "$BUILD_DIR/cmake/bin/vocotype-streaming-worker" "$BUNDLE_DIR/bin/"
cp "$BUILD_DIR/cmake/bin/vocotype-offline-worker" "$BUNDLE_DIR/bin/"
if [[ "$HOST_OS" == Darwin ]]; then
    cp "$BUILD_DIR/cmake/src/libfunasr.dylib" "$BUNDLE_DIR/lib/"
    cp -a "$BUILD_DIR/cmake/third_party/yaml-cpp"/libyaml-cpp*.dylib "$BUNDLE_DIR/lib/"
    cp -a "$BUILD_DIR/cmake/third_party/openfst/src/lib"/libfst*.dylib "$BUNDLE_DIR/lib/"
    cp -a "$BUILD_DIR/cmake/third_party/glog"/libglog*.dylib "$BUNDLE_DIR/lib/"
    cp -a "$ONNXRUNTIME_DIR/lib"/libonnxruntime*.dylib "$BUNDLE_DIR/lib/"
else
    cp "$BUILD_DIR/cmake/src/libfunasr.so" "$BUNDLE_DIR/lib/"
    cp -a "$BUILD_DIR/cmake/third_party/yaml-cpp"/libyaml-cpp.so* "$BUNDLE_DIR/lib/"
    cp -a "$BUILD_DIR/cmake/third_party/openfst/src/lib"/libfst.so* "$BUNDLE_DIR/lib/"
    cp -a "$BUILD_DIR/cmake/third_party/glog"/libglog.so* "$BUNDLE_DIR/lib/"
    cp -a "$ONNXRUNTIME_DIR/lib"/libonnxruntime.so* "$BUNDLE_DIR/lib/"
    shopt -s nullglob
    provider_libraries=("$ONNXRUNTIME_DIR/lib"/libonnxruntime_providers_shared.so*)
    cpuinfo_libraries=("$ONNXRUNTIME_DIR/lib"/libcpuinfo.so*)
    if ((${#provider_libraries[@]})); then
        cp -a "${provider_libraries[@]}" "$BUNDLE_DIR/lib/"
    fi
    if ((${#cpuinfo_libraries[@]})); then
        cp -a "${cpuinfo_libraries[@]}" "$BUNDLE_DIR/lib/"
    fi
    shopt -u nullglob
fi
mkdir -p "$BUNDLE_DIR/share/licenses/onnxruntime" "$BUNDLE_DIR/share/licenses/funasr"
for notice in LICENSE ThirdPartyNotices.txt Privacy.md; do
    [[ -f "$ONNXRUNTIME_DIR/$notice" ]] &&
        cp "$ONNXRUNTIME_DIR/$notice" "$BUNDLE_DIR/share/licenses/onnxruntime/"
done
if [[ -f "$FUNASR_SOURCE_DIR/LICENSE" ]]; then
    cp "$FUNASR_SOURCE_DIR/LICENSE" "$BUNDLE_DIR/share/licenses/funasr/LICENSE"
elif [[ -f "$RUNTIME_SOURCE/../../LICENSE" ]]; then
    cp "$RUNTIME_SOURCE/../../LICENSE" "$BUNDLE_DIR/share/licenses/funasr/LICENSE"
else
    echo "FunASR license file missing from source checkout" >&2
    exit 2
fi

if [[ "${STRIP_NATIVE_BUNDLE:-1}" == "1" ]]; then
    if [[ "$HOST_OS" == Darwin ]]; then
        strip -x "$BUNDLE_DIR/bin/vocotype-streaming-worker" \
            "$BUNDLE_DIR/bin/vocotype-offline-worker" \
            "$BUNDLE_DIR/lib/libfunasr.dylib" 2>/dev/null || true
    else
        strip --strip-unneeded \
            "$BUNDLE_DIR/bin/vocotype-streaming-worker" \
            "$BUNDLE_DIR/bin/vocotype-offline-worker" \
            "$BUNDLE_DIR/lib/libfunasr.so" 2>/dev/null || true
    fi
fi
if [[ "$HOST_OS" == Darwin ]]; then
    for binary in "$BUNDLE_DIR/bin/vocotype-streaming-worker" \
                  "$BUNDLE_DIR/bin/vocotype-offline-worker"; do
        file "$binary" | grep -q 'Mach-O 64-bit executable arm64' || {
            echo "Unexpected macOS worker architecture: $binary" >&2
            exit 2
        }
        otool -L "$binary"
    done
else
    audit_arguments=()
    if [[ ${VOCOTYPE_BUNDLE_AUDIT_MODE:-portable} == nix-store ]]; then
        audit_arguments+=(--nix-store)
    fi
    bash "$SCRIPT_DIR/audit_bundle.sh" "${audit_arguments[@]}" "$BUNDLE_DIR"
fi
printf '%s\n' \
    "$BUNDLE_DIR/bin/vocotype-streaming-worker" \
    "$BUNDLE_DIR/bin/vocotype-offline-worker"
