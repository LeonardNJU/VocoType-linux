param(
  [string]$InstallDir = $PSScriptRoot,
  [string]$ModelRoot = "$env:LOCALAPPDATA/VocoType/models",
  [string]$ConfigPath = "$env:LOCALAPPDATA/VocoType/windows.json",
  [switch]$OfflineOnly,
  [switch]$SkipPunctuation,
  [switch]$Force
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$InstallDir = (Resolve-Path $InstallDir).Path
if ((Test-Path $ConfigPath) -and -not $Force) { throw "Configuration already exists: $ConfigPath. Use -Force only to replace it deliberately." }
foreach ($exe in @('vocotype-core.exe', 'vocotype-offline-worker.exe', 'vocotype-windows.exe', 'vocotype-wasapi-recorder.exe')) {
  if (-not (Test-Path "$InstallDir/$exe")) { throw "Incomplete portable build: missing $exe" }
}
$manifest = Get-Content "$PSScriptRoot/models.json" -Raw | ConvertFrom-Json
$selected = @($manifest[0])
if (-not $OfflineOnly) {
  if (-not (Test-Path "$InstallDir/vocotype-streaming-worker.exe")) { throw 'Missing streaming worker' }
  $selected += @($manifest[1])
  if (-not $SkipPunctuation) { $selected += @($manifest[3]) }
}
New-Item -ItemType Directory -Force $ModelRoot | Out-Null
$ModelRoot = (Resolve-Path $ModelRoot).Path
foreach ($model in $selected) {
  $folder = Join-Path $ModelRoot ($model.id -replace '/', '__')
  New-Item -ItemType Directory -Force $folder | Out-Null
  foreach ($file in $model.files) {
    $path = Join-Path $folder $file.name
    if ((Test-Path $path) -and ((Get-FileHash $path -Algorithm SHA256).Hash.ToLowerInvariant() -eq $file.sha256)) { continue }
    $url = "https://modelscope.cn/models/$($model.id)/resolve/$($file.revision)/$($file.name)"
    Write-Host "Downloading pinned $($model.id)/$($file.name)"
    Invoke-WebRequest $url -OutFile "$path.part" -MaximumRetryCount 3 -RetryIntervalSec 3 -TimeoutSec 600
    if ((Get-FileHash "$path.part" -Algorithm SHA256).Hash.ToLowerInvariant() -ne $file.sha256) { Remove-Item "$path.part"; throw "Model checksum mismatch: $path" }
    Move-Item "$path.part" $path -Force
  }
}
$config = @{
  asr = @{
    native_enabled = $true
    worker_path = "$InstallDir/vocotype-offline-worker.exe"
    model_dir = (Join-Path $ModelRoot ($manifest[0].id -replace '/', '__'))
    use_vad = $false
    use_punc = ((-not $OfflineOnly) -and (-not $SkipPunctuation))
    punc_model_dir = (Join-Path $ModelRoot ($manifest[3].id -replace '/', '__'))
    intra_op_num_threads = 2
    startup_timeout_s = 120
    request_timeout_s = 120
    idle_timeout_s = 300
  }
  asr_streaming = @{
    enabled = (-not $OfflineOnly)
    worker_path = "$InstallDir/vocotype-streaming-worker.exe"
    model_dir = (Join-Path $ModelRoot ($manifest[1].id -replace '/', '__'))
    startup_timeout_s = 180
    idle_timeout_s = 300
  }
  slm = @{ enabled = $false }
  windows = @{ device_id = '' }
}
New-Item -ItemType Directory -Force (Split-Path $ConfigPath) | Out-Null
$config | ConvertTo-Json -Depth 10 | Set-Content "$ConfigPath.part" -Encoding utf8NoBOM
Move-Item "$ConfigPath.part" $ConfigPath -Force
Write-Host "Ready. Configuration: $ConfigPath"
Write-Host 'Run vocotype-windows.exe; wait for the ready message, then hold F9 to speak.'
Write-Host 'Speech recognition is local. No administrator privileges or online recognition service is required.'
