# Build a versioned Windows distribution with PyInstaller.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Python = if ($env:ZUCKER_PYTHON) { $env:ZUCKER_PYTHON } else { Join-Path $Root ".venv\Scripts\python.exe" }
if (-not (Test-Path $Python)) { throw "Python not found at $Python" }

Set-Location $Root
$Version = (& $Python -c "from core.build_info import APP_VERSION; print(APP_VERSION)").Trim()
if ([string]::IsNullOrWhiteSpace($Version) -or $Version -eq "unknown") { throw "Could not determine APP_VERSION" }
$AppName = "Zucker Editor $Version"
$Commit = if ($env:GITHUB_SHA) { $env:GITHUB_SHA.Substring(0, [Math]::Min(7, $env:GITHUB_SHA.Length)) } else { (& git rev-parse --short HEAD 2>$null).Trim() }
if ([string]::IsNullOrWhiteSpace($Commit)) { $Commit = "unknown" }

$Dist = Join-Path $Root "dist"
$PyInstallerDist = Join-Path $Root "build\pyinstaller-dist"
$PyInstallerBuild = Join-Path $Root "build\pyinstaller"
$BuildInfo = Join-Path $Root "build\build_info.json"
$Zip = Join-Path $Dist "$AppName-windows.zip"
New-Item -ItemType Directory -Force -Path (Join-Path $Root "build"), $Dist | Out-Null
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $PyInstallerDist, $PyInstallerBuild, $Zip

@{ version = $Version; git_commit = $Commit } | ConvertTo-Json | Set-Content -Encoding UTF8 $BuildInfo
$common = @(
  "--noconfirm", "--clean", "--windowed", "--name", $AppName,
  "--distpath", $PyInstallerDist, "--workpath", $PyInstallerBuild, "--specpath", $PyInstallerBuild,
  "--add-data", "$Root\web;web",
  "--add-data", "$Root\assets\models;assets\models",
  "--add-data", "$Root\assets\intro_card_watermark.png;assets",
  "--add-data", "$Root\assets\parchment_full.png;assets",
  "--add-data", "$Root\core\vendor;core/vendor",
  "--add-data", "$BuildInfo;.",
  "--add-data", "$Root\README_APP.md;.",
  "--collect-data", "faster_whisper", "--collect-data", "whisper",
  "--collect-data", "onnxruntime", "--collect-data", "tokenizers",
  "--hidden-import", "server.api", "--hidden-import", "faster_whisper",
  "--hidden-import", "ctranslate2", "--hidden-import", "onnxruntime", "--hidden-import", "tokenizers",
  "--collect-submodules", "server", "--collect-submodules", "core",
  "--exclude-module", "pytest", "--exclude-module", "tests",
  "$Root\app.py"
)
& $Python -m PyInstaller @common
$AppDir = Join-Path $PyInstallerDist $AppName
if (-not (Test-Path $AppDir)) { throw "PyInstaller output missing: $AppDir" }
Compress-Archive -Path (Join-Path $AppDir "*") -DestinationPath $Zip -Force
Write-Host "Built version $Version (git=$Commit): $Zip"
