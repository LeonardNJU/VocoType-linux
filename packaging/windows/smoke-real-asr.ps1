param([Parameter(Mandatory)][string]$Bundle, [Parameter(Mandatory)][string]$Config)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$root = (Resolve-Path "$PSScriptRoot/../..").Path
$Bundle = (Resolve-Path $Bundle).Path
$Config = (Resolve-Path $Config).Path
$manifest = Get-Content "$PSScriptRoot/speech-fixture.json" -Raw | ConvertFrom-Json
$fixture = Join-Path (Split-Path $Config) '公开中文语音.wav'
Invoke-WebRequest $manifest.url -OutFile $fixture -MaximumRetryCount 3 -TimeoutSec 60
if ((Get-FileHash $fixture -Algorithm SHA256).Hash.ToLowerInvariant() -ne $manifest.sha256) { throw 'Public speech fixture checksum mismatch' }
& "$root/build/windows/Release/vocotype-windows-real-asr-tests.exe" $Bundle $Config $fixture
if ($LASTEXITCODE -ne 0) { throw 'Real native offline/streaming inference failed' }
