{
  lib,
  stdenv,
  source,
  flavor ? "universal",
  fetchFromGitHub,
  cmake,
  pkg-config,
  patchelf,
  ibusMinimal,
  patch,
  fcitx5,
  librime,
  gtk3,
  yaml-cpp,
  curl,
  openssl,
  portaudio,
  nlohmann_json,
  onnxruntime,
  glog,
}:

assert lib.assertOneOf "flavor" flavor [ "universal" "ibus" "fcitx5" ];

let
  version = lib.strings.removeSuffix "\n" (builtins.readFile ../VERSION);
  withIbus = flavor == "universal" || flavor == "ibus";
  withFcitx = flavor == "universal" || flavor == "fcitx5";
  onnxruntimeNative = onnxruntime.override {
    pythonSupport = false;
    cudaSupport = false;
    rocmSupport = false;
  };

  funasrSource = fetchFromGitHub {
    owner = "modelscope";
    repo = "FunASR";
    rev = "bd6e72142f1cca3c30b7651bf5fa567dfe969810";
    sparseCheckout = [ "runtime/onnxruntime" "LICENSE" ];
    hash = "sha256-3abFrokYBHCfoRlxXnF92pwBleypRX4E1eFL+tTXAI8=";
  };

  workers = stdenv.mkDerivation {
    pname = "vocotype-funasr-workers";
    inherit version;
    src = source;

    nativeBuildInputs = [
      cmake
      patch
      patchelf
      pkg-config
    ];
    buildInputs = [
      glog
      nlohmann_json
      onnxruntimeNative
    ];

    dontConfigure = true;
    enableParallelBuilding = true;

    buildPhase = ''
      runHook preBuild
      export HOME="$TMPDIR/home"
      mkdir -p "$HOME" "$TMPDIR/ort/include" "$TMPDIR/worker-cache"
      cp -rs ${onnxruntimeNative.dev}/include/. "$TMPDIR/ort/include/"
      ln -s ${onnxruntimeNative}/lib "$TMPDIR/ort/lib"
      export FUNASR_SOURCE_DIR=${funasrSource}
      export ONNXRUNTIME_DIR="$TMPDIR/ort"
      export NLOHMANN_JSON_INCLUDE_DIR=${nlohmann_json}/include
      export VOCOTYPE_NATIVE_CACHE_DIR="$TMPDIR/worker-cache"
      export VOCOTYPE_NATIVE_BUILD_DIR="$TMPDIR/worker-build"
      export STRIP_NATIVE_BUNDLE=0
      export JOBS="$NIX_BUILD_CORES"
      bash native/streaming_worker/build.sh
      runHook postBuild
    '';

    installPhase = ''
      runHook preInstall
      bundle="$TMPDIR/worker-build/bundle"
      mkdir -p "$out/bin" "$out/lib/vocotype" "$out/share/licenses/vocotype"
      install -m755 "$bundle/bin/vocotype-streaming-worker" "$out/bin/"
      install -m755 "$bundle/bin/vocotype-offline-worker" "$out/bin/"
      cp -a "$bundle/lib/." "$out/lib/vocotype/"
      cp -a "$bundle/share/licenses/." "$out/share/licenses/vocotype/"
      worker_rpath="$out/lib/vocotype:${lib.makeLibraryPath [ stdenv.cc.cc glog onnxruntimeNative ]}"
      patchelf --set-rpath "$worker_rpath" "$out/bin/vocotype-streaming-worker"
      patchelf --set-rpath "$worker_rpath" "$out/bin/vocotype-offline-worker"
      for library in "$out"/lib/vocotype/*.so*; do
        test -L "$library" && continue
        patchelf --set-rpath "$worker_rpath" "$library" 2>/dev/null || true
      done
      runHook postInstall
    '';

    meta = {
      description = "Pinned native FunASR workers used by VoCoType";
      license = [ lib.licenses.gpl3Only lib.licenses.mit ];
      platforms = lib.platforms.linux;
    };
  };
in
stdenv.mkDerivation (finalAttrs: {
  pname = if flavor == "universal" then "vocotype" else "vocotype-${flavor}";
  inherit version;
  src = source;

  nativeBuildInputs = [
    cmake
    pkg-config
  ];
  buildInputs = [
    curl
    gtk3
    nlohmann_json
    openssl
    portaudio
    yaml-cpp
  ] ++ lib.optionals withIbus [ ibusMinimal librime ]
    ++ lib.optionals withFcitx [ fcitx5 ];

  dontConfigure = true;
  enableParallelBuilding = true;

  buildPhase = ''
    runHook preBuild
    cmake -S native/core -B build-nix/core \
      -DCMAKE_BUILD_TYPE=Release \
      -DBUILD_TESTING=OFF
    cmake --build build-nix/core --target vocotype-core --parallel "$NIX_BUILD_CORES"

    cmake -S native/desktop -B build-nix/desktop \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX="$out" \
      -DCMAKE_INSTALL_LIBEXECDIR=libexec \
      -DVOCOTYPE_BUILD_SETTINGS=ON \
      -DVOCOTYPE_BUILD_IBUS=${if withIbus then "ON" else "OFF"} \
      -DVOCOTYPE_BUILD_RIME=${if withIbus then "ON" else "OFF"} \
      -DBUILD_TESTING=OFF
    cmake --build build-nix/desktop --parallel "$NIX_BUILD_CORES"

    ${lib.optionalString withFcitx ''
      cmake -S fcitx5/module -B build-nix/fcitx5 \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX="$out" \
        -DCMAKE_INSTALL_LIBDIR=lib \
        -DVOCOTYPE_FCITX5_RECORDER_PATH="$out/bin/vocotype-fcitx5-recorder" \
        -DVOCOTYPE_FCITX5_BACKEND_PATH="$out/bin/vocotype-fcitx5-backend"
      cmake --build build-nix/fcitx5 --parallel "$NIX_BUILD_CORES"
    ''}
    runHook postBuild
  '';

  installPhase = ''
    runHook preInstall
    cmake --install build-nix/desktop
    install -Dm755 build-nix/core/vocotype-core "$out/libexec/vocotype-core"

    mkdir -p "$out/bin"
    mv "$out/bin/vocotype-settings" "$out/libexec/vocotype-settings"
    cat > "$out/bin/vocotype-settings" <<WRAPPER
#!${stdenv.shell}
export PATH="$out/bin:$out/libexec:\${PATH:-}"
exec "$out/libexec/vocotype-settings" "\$@"
WRAPPER
    cat > "$out/bin/vocotype-core" <<WRAPPER
#!${stdenv.shell}
export VOCOTYPE_STREAMING_WORKER=${workers}/bin/vocotype-streaming-worker
export VOCOTYPE_OFFLINE_WORKER=${workers}/bin/vocotype-offline-worker
exec "$out/libexec/vocotype-core" "\$@"
WRAPPER
    cat > "$out/bin/vocotype-fcitx5-recorder" <<WRAPPER
#!${stdenv.shell}
exec "$out/libexec/vocotype-audio-recorder" "\$@"
WRAPPER
    cat > "$out/bin/vocotype-model-manager" <<WRAPPER
#!${stdenv.shell}
exec "$out/libexec/vocotype-model-manager" "\$@"
WRAPPER
    cat > "$out/bin/vocotype-fcitx5-backend" <<WRAPPER
#!${stdenv.shell}
exec "$out/bin/vocotype-core" --enable-final-asr "\$@"
WRAPPER
    chmod 0755 "$out/bin/vocotype-settings" "$out/bin/vocotype-core" \
      "$out/bin/vocotype-fcitx5-recorder" "$out/bin/vocotype-model-manager" \
      "$out/bin/vocotype-fcitx5-backend"

    install -Dm644 data/applications/io.github.LeonardNJU.VoCoType.Settings.desktop \
      "$out/share/applications/io.github.LeonardNJU.VoCoType.Settings.desktop"
    install -Dm644 data/metainfo/io.github.LeonardNJU.VoCoType.metainfo.xml \
      "$out/share/metainfo/io.github.LeonardNJU.VoCoType.metainfo.xml"
    install -Dm644 site/icon-192.png \
      "$out/share/icons/hicolor/192x192/apps/vocotype.png"
    install -Dm644 site/icon-512.png \
      "$out/share/icons/hicolor/512x512/apps/vocotype.png"
    install -Dm644 data/terms.yaml "$out/share/vocotype/terms.yaml"

    ${lib.optionalString withIbus ''
      mv "$out/libexec/vocotype-ibus-engine" \
        "$out/libexec/vocotype-ibus-engine.real"
      cat > "$out/libexec/vocotype-ibus-engine" <<WRAPPER
#!${stdenv.shell}
export PATH="$out/bin:$out/libexec:\${PATH:-}"
exec "$out/libexec/vocotype-ibus-engine.real" "\$@"
WRAPPER
      chmod 0755 "$out/libexec/vocotype-ibus-engine"
      mkdir -p "$out/share/ibus/component"
      substitute ibus/data/vocotype.xml.in \
        "$out/share/ibus/component/vocotype.xml" \
        --replace-fail VOCOTYPE_EXEC_PATH "$out/libexec/vocotype-ibus-engine" \
        --replace-fail VOCOTYPE_VERSION "${version}"
    ''}

    ${lib.optionalString withFcitx ''
      cmake --install build-nix/fcitx5
      install -Dm644 fcitx5/data/vocotype.conf \
        "$out/share/fcitx5/addon/vocotype.conf"
    ''}
    runHook postInstall
  '';

  postFixup = ''
    test -x "$out/bin/vocotype-settings"
    test -x "$out/bin/vocotype-core"
    ${lib.optionalString withIbus ''test -f "$out/share/ibus/component/vocotype.xml"''}
    ${lib.optionalString withFcitx ''test -f "$out/share/fcitx5/addon/vocotype.conf"''}
  '';

  passthru = {
    inherit flavor workers funasrSource;
  };

  meta = {
    description = "Offline native voice input and voice editing for Linux";
    homepage = "https://github.com/LeonardNJU/VocoType-linux";
    license = lib.licenses.gpl3Only;
    platforms = lib.platforms.linux;
    mainProgram = "vocotype-settings";
  };
})
