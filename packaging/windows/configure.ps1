$ErrorActionPreference = 'Stop'
$root = (Resolve-Path "$PSScriptRoot/../..").Path
if (-not $env:VCPKG_INSTALLATION_ROOT) { throw 'Set VCPKG_INSTALLATION_ROOT to a vcpkg checkout.' }
& "$env:VCPKG_INSTALLATION_ROOT/vcpkg.exe" install 'curl[core,schannel]:x64-windows' 'nlohmann-json:x64-windows' --recurse
if ($LASTEXITCODE -ne 0) { throw 'vcpkg install failed' }
& cmake -S "$root/src/integrations/windows" -B "$root/build/windows" "-DCMAKE_TOOLCHAIN_FILE=$env:VCPKG_INSTALLATION_ROOT/scripts/buildsystems/vcpkg.cmake" -DVCPKG_TARGET_TRIPLET=x64-windows -DBUILD_TESTING=ON
if ($LASTEXITCODE -ne 0) { throw 'Windows configure failed' }
