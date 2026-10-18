param([string]$NativeBundle, [string]$Destination = '', [switch]$SkipArchive)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path "$PSScriptRoot/../..").Path
if (-not $Destination) { $Destination = "$root/dist/windows-preview" }
$exeRoot = "$root/build/windows/Release"
foreach ($name in @('vocotype-windows.exe', 'vocotype-core.exe', 'vocotype-wasapi-recorder.exe')) {
  if (-not (Test-Path "$exeRoot/$name")) { throw "Missing application executable: $name" }
}
if (-not (Test-Path "$NativeBundle/vocotype-offline-worker.exe")) { throw 'A tested native FunASR bundle is required; test doubles cannot be packaged.' }
New-Item -ItemType Directory -Force $Destination | Out-Null
foreach ($name in @('vocotype-windows.exe', 'vocotype-core.exe', 'vocotype-wasapi-recorder.exe')) { Copy-Item "$exeRoot/$name" $Destination -Force }
Get-ChildItem "$exeRoot/*.dll" | Copy-Item -Destination $Destination -Force
Get-ChildItem $NativeBundle -File | Copy-Item -Destination $Destination -Force
foreach ($file in @('setup-models.ps1', 'models.json')) { Copy-Item "$PSScriptRoot/$file" $Destination -Force }
Copy-Item "$root/docs/integrations/windows.md" "$Destination/README.md" -Force
Copy-Item "$root/LICENSE" "$Destination/LICENSE.txt" -Force
Copy-Item "$root/THIRD_PARTY_NOTICES.md" $Destination -Force
# Include dependency notices from vcpkg; no test helper executables are shipped.
$notices = "$Destination/share/licenses"
New-Item -ItemType Directory -Force $notices | Out-Null
Copy-Item "$root/resources/licenses/windows-runtime/*" $notices -Force
Get-ChildItem "$env:VCPKG_INSTALLATION_ROOT/installed/x64-windows/share" -Recurse -Filter copyright | ForEach-Object { Copy-Item $_.FullName "$notices/$($_.Directory.Name).txt" -Force }
# Include the MSVC runtime app-locally when the redistributable files are present.
$vswhere = "${env:ProgramFiles(x86)}/Microsoft Visual Studio/Installer/vswhere.exe"
$vs = & $vswhere -latest -products '*' -property installationPath
$crt = Get-ChildItem "$vs/VC/Redist/MSVC" -Recurse -Directory -Filter 'Microsoft.VC*.CRT' | Where-Object { $_.FullName -match '[\\/]x64[\\/]' } | Sort-Object FullName | Select-Object -Last 1
if ($crt) { Copy-Item "$($crt.FullName)/*.dll" $Destination -Force }
$sha = (& git -C $root rev-parse HEAD).Trim()
@{channel='experimental';git_commit=$sha;stable_release_unchanged=$true;models_bundled=$false} | ConvertTo-Json | Set-Content "$Destination/build-info.json"
& "$Destination/vocotype-windows.exe" --self-test
if ($LASTEXITCODE) { throw 'Packaged app failed to load' }
& "$Destination/vocotype-core.exe" --help
if ($LASTEXITCODE) { throw 'Packaged core failed to load' }
& "$Destination/vocotype-wasapi-recorder.exe" --help
if ($LASTEXITCODE) { throw 'Packaged recorder failed to load' }
Get-ChildItem $Destination -Recurse -File | Where-Object Name -ne 'SHA256SUMS' | Sort-Object FullName | ForEach-Object { "$((Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant())  $([IO.Path]::GetRelativePath($Destination, $_.FullName))" } | Set-Content "$Destination/SHA256SUMS" -Encoding utf8NoBOM
if (-not $SkipArchive) { Compress-Archive "$Destination/*" "$root/dist/VocoType-Windows-x64-preview-$($sha.Substring(0,7)).zip" -Force }
