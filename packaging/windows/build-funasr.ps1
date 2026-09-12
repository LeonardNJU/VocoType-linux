param([string]$Destination = '', [int]$Jobs = 3)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path "$PSScriptRoot/../..").Path
if (-not $Destination) { $Destination = "$root/build/windows-runtime/bundle" }
$work = "$root/build/windows-runtime"
$commit = 'bd6e72142f1cca3c30b7651bf5fa567dfe969810'
$source = "$work/FunASR"
New-Item -ItemType Directory -Force $work | Out-Null
if (-not (Test-Path "$source/.git")) {
  git clone --filter=blob:none --no-checkout https://github.com/modelscope/FunASR.git $source
  if ($LASTEXITCODE) { throw 'FunASR clone failed' }
  git -C $source sparse-checkout init --cone
  git -C $source sparse-checkout set runtime/onnxruntime
}
git -C $source checkout --detach $commit
if ($LASTEXITCODE) { throw 'Pinned FunASR checkout failed' }
$zip = "$work/onnxruntime-win-x64-1.23.2.zip"
if (-not (Test-Path $zip)) { Invoke-WebRequest 'https://github.com/microsoft/onnxruntime/releases/download/v1.23.2/onnxruntime-win-x64-1.23.2.zip' -OutFile $zip }
$expected = '0b38df9af21834e41e73d602d90db5cb06dbd1ca618948b8f1d66d607ac9f3cd'
if ((Get-FileHash $zip -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected) { throw 'ONNX Runtime SDK checksum mismatch' }
$ort = "$work/onnxruntime-win-x64-1.23.2"
if (-not (Test-Path "$ort/include/onnxruntime_cxx_api.h")) { Expand-Archive $zip -DestinationPath $work -Force }
New-Item -ItemType Directory -Force "$ort/include/onnxruntime" | Out-Null
Copy-Item "$ort/include/*.h" "$ort/include/onnxruntime/" -Force
$runtime = "$work/source"
if (Test-Path $runtime) { Remove-Item -Recurse -Force $runtime }
Copy-Item -Recurse "$source/runtime/onnxruntime" $runtime
# Use single-quoted template to preserve CMake variable syntax under PowerShell.
$bin = @'
function(add_vocotype_worker target source)
  add_executable(${target} "${source}")
  target_include_directories(${target} PRIVATE "${CMAKE_SOURCE_DIR}/include" "${CMAKE_SOURCE_DIR}/src" "${CMAKE_SOURCE_DIR}/third_party/json/include")
  target_compile_features(${target} PRIVATE cxx_std_17)
  target_link_libraries(${target} PRIVATE funasr)
  target_link_options(${target} PRIVATE /MANIFEST:EMBED "/MANIFESTINPUT:@ROOT@/src/integrations/windows/utf8.manifest")
endfunction()
add_vocotype_worker(vocotype-offline-worker "@ROOT@/src/workers/funasr/offline_worker.cpp")
add_vocotype_worker(vocotype-streaming-worker "@ROOT@/src/workers/funasr/worker.cpp")
'@
$bin.Replace('@ROOT@', $root.Replace('\','/')) | Set-Content "$runtime/bin/CMakeLists.txt" -Encoding utf8
$p = "$runtime/src/CMakeLists.txt"
$s = Get-Content $p -Raw
$s = $s.Replace('target_compile_definitions(funasr PUBLIC -D_FUNASR_API_EXPORT -DNOMINMAX -DYAML_CPP_DLL)', 'target_compile_definitions(funasr PRIVATE _FUNASR_API_EXPORT PUBLIC NOMINMAX YAML_CPP_DLL)')
$s | Set-Content $p -Encoding utf8
cmake -S $runtime -B "$work/build" -A x64 "-DONNXRUNTIME_DIR=$ort" -DENABLE_FFMPEG=OFF -DCMAKE_CXX_STANDARD=17 '-DCMAKE_POLICY_VERSION_MINIMUM=3.5' '-DCMAKE_CXX_FLAGS=/EHsc /utf-8 /bigobj'
if ($LASTEXITCODE) { throw 'FunASR configure failed' }
cmake --build "$work/build" --config Release --target vocotype-offline-worker vocotype-streaming-worker --parallel $Jobs
if ($LASTEXITCODE) { throw 'FunASR native worker build failed' }
New-Item -ItemType Directory -Force $Destination | Out-Null
Get-ChildItem "$work/build" -Recurse -Filter '*.dll' | Where-Object { $_.FullName -match 'Release' } | Copy-Item -Destination $Destination -Force
Copy-Item "$work/build/bin/Release/vocotype-*-worker.exe" $Destination -Force
Copy-Item "$ort/lib/*.dll" $Destination -Force
Copy-Item "$root/LICENSE" "$Destination/VocoType-LICENSE.txt" -Force
Copy-Item "$source/LICENSE" "$Destination/FunASR-LICENSE.txt" -Force
@{ funasr_commit=$commit; onnxruntime_version='1.23.2'; onnxruntime_sha256=$expected } | ConvertTo-Json | Set-Content "$Destination/runtime-versions.json"
& "$Destination/vocotype-offline-worker.exe" --help
if ($LASTEXITCODE) { throw 'Offline worker cannot load runtime DLLs' }
& "$Destination/vocotype-streaming-worker.exe" --help
if ($LASTEXITCODE) { throw 'Streaming worker cannot load runtime DLLs' }
