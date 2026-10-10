# Build a versioned Windows distribution with PyInstaller.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Python = if ($env:ZUCKER_PYTHON) { $env:ZUCKER_PYTHON } elseif (Test-Path (Join-Path $Root ".venv\Scripts\python.exe")) { Join-Path $Root ".venv\Scripts\python.exe" } else { (Get-Command python -ErrorAction Stop).Source }
if (-not (Test-Path $Python)) { throw "Python not found at $Python" }

Set-Location $Root
$Version = (& $Python -c "from core.build_info import APP_VERSION; print(APP_VERSION)").Trim()
if ([string]::IsNullOrWhiteSpace($Version) -or $Version -eq "unknown") { throw "Could not determine APP_VERSION" }
$AppName = "Zucker Editor $Version"
$BundleName = "Zucker Editor"
$Commit = if ($env:GITHUB_SHA) { $env:GITHUB_SHA } else { (& git rev-parse HEAD 2>$null).Trim() }
if ([string]::IsNullOrWhiteSpace($Commit)) { $Commit = "unknown" }

$Dist = Join-Path $Root "dist"
$PyInstallerDist = Join-Path $Root "build\pyinstaller-dist"
$PyInstallerBuild = Join-Path $Root "build\pyinstaller"
$BuildInfo = Join-Path $Root "build\build_info.json"
$Zip = Join-Path $Dist "$AppName-windows.zip"
New-Item -ItemType Directory -Force -Path (Join-Path $Root "build"), $Dist | Out-Null
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $PyInstallerDist, $PyInstallerBuild, $Zip

@{ version = $Version; git_commit = $Commit } | ConvertTo-Json | Set-Content -Encoding UTF8 $BuildInfo

# CLIP image encoder for filler scene tagging (not in git): pinned revision + SHA-256.
$ClipModel = Join-Path $Root "assets\models\clip\vision_model_quantized.onnx"
$ClipSha = "583fd1110a514667812fee7d684952aaf82a99b959760c8d7dca7e0ab9839299"
if (-not (Test-Path $ClipModel) -or (Get-FileHash -Algorithm SHA256 $ClipModel).Hash.ToLower() -ne $ClipSha) {
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $ClipModel) | Out-Null
  Invoke-WebRequest -UseBasicParsing -OutFile "$ClipModel.part" `
    -Uri "https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/d15189d7028b43f1d3e65039190477f6af591c2a/onnx/vision_model_quantized.onnx"
  if ((Get-FileHash -Algorithm SHA256 "$ClipModel.part").Hash.ToLower() -ne $ClipSha) { throw "CLIP model SHA-256 mismatch" }
  Move-Item -Force "$ClipModel.part" $ClipModel
}
$common = @(
  "--noconfirm", "--clean", "--noupx", "--windowed", "--name", $BundleName,
  "--distpath", $PyInstallerDist, "--workpath", $PyInstallerBuild, "--specpath", $PyInstallerBuild,
  "--add-data", "$Root\web;web",
  "--add-data", "$Root\assets\models;assets\models",
  "--add-data", "$Root\captions\presets.json;captions",
  "--add-data", "$Root\assets\intro_card_watermark.png;assets",
  "--add-data", "$Root\assets\parchment_full.png;assets",
  "--add-data", "$Root\core\vendor;core/vendor",
  "--add-data", "$BuildInfo;.",
  "--add-data", "$Root\README_APP.md;.",
  "--collect-submodules", "demucs", "--collect-data", "demucs", "--hidden-import", "torchaudio",
  "--collect-data", "faster_whisper", "--collect-data", "whisper",
  "--collect-data", "onnxruntime", "--collect-data", "tokenizers",
  "--hidden-import", "librosa", "--hidden-import", "cv2", "--hidden-import", "scipy.signal",
  "--hidden-import", "soundfile", "--hidden-import", "audioread", "--hidden-import", "numba", "--hidden-import", "llvmlite",
  "--hidden-import", "server.api", "--hidden-import", "faster_whisper",
  "--hidden-import", "ctranslate2", "--hidden-import", "onnxruntime", "--hidden-import", "tokenizers",
  "--hidden-import", "webview.platforms.winforms", "--hidden-import", "clr",
  "--collect-data", "pythonnet", "--collect-binaries", "pythonnet",
  "--collect-submodules", "clr_loader",
  "--collect-submodules", "server", "--collect-submodules", "core",
  "--exclude-module", "pytest", "--exclude-module", "tests",
  "$Root\app.py"
)
$Ffmpeg = (Get-Command ffmpeg -ErrorAction Stop).Source
$Ffprobe = (Get-Command ffprobe -ErrorAction Stop).Source
# Chocolatey shims are not redistributable media tools; resolve the installed binaries.
if ($Ffmpeg -like "*chocolatey\bin*") {
  $Ffmpeg = (Get-ChildItem "C:\ProgramData\chocolatey\lib\ffmpeg*" -Recurse -Filter ffmpeg.exe | Select-Object -First 1).FullName
  $Ffprobe = (Get-ChildItem "C:\ProgramData\chocolatey\lib\ffmpeg*" -Recurse -Filter ffprobe.exe | Select-Object -First 1).FullName
}
if (-not (Test-Path $Ffmpeg) -or -not (Test-Path $Ffprobe)) { throw "Actual ffmpeg/ffprobe binaries missing" }
$common += @("--add-binary", "$Ffmpeg;bin", "--add-binary", "$Ffprobe;bin")
foreach ($font in @("arial.ttf", "arialbd.ttf", "verdana.ttf", "verdanab.ttf")) {
  $fontPath = Join-Path $env:WINDIR "Fonts\$font"
  if (Test-Path $fontPath) { $common += @("--add-data", "$fontPath;assets/fonts") }
}
& $Python -m PyInstaller @common
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
$AppDir = Join-Path $PyInstallerDist $BundleName
if (-not (Test-Path $AppDir)) { throw "PyInstaller output missing: $AppDir" }
if (-not $env:ZUCKER_SELFTEST_AUDIO -or -not (Test-Path $env:ZUCKER_SELFTEST_AUDIO)) { throw "Set ZUCKER_SELFTEST_AUDIO for the mandatory frozen self-test" }
# A stale/mismatched managed assembly must never be released.
$RuntimeSource = (& $Python -c "import importlib.util; from pathlib import Path; print(Path(importlib.util.find_spec('pythonnet').origin).parent / 'runtime' / 'Python.Runtime.dll')").Trim()
$RuntimeBundled = Join-Path $AppDir "_internal\pythonnet\runtime\Python.Runtime.dll"
if (-not (Test-Path $RuntimeBundled)) { throw "Packaged Python.Runtime.dll missing" }
if ((Get-FileHash $RuntimeSource).Hash -ne (Get-FileHash $RuntimeBundled).Hash) { throw "Packaged Python.Runtime.dll differs from installed pythonnet" }
$Exe = Join-Path $AppDir "$BundleName.exe"
$env:ZUCKER_WHISPER_BACKEND = "faster-whisper"
$Log = Join-Path $Root "build\packaged-selftest-windows.log"
$env:ZUCKER_SELFTEST_REPORT = $Log
$test = Start-Process -FilePath $Exe -ArgumentList "--selftest" -Wait -PassThru
if ($test.ExitCode -ne 0) { Get-Content $Log; throw "Frozen Windows self-test failed" }
$result = Get-Content $Log | Select-Object -Last 1 | ConvertFrom-Json
if (-not $result.ok -or -not $result.transcription -or -not $result.intro_rendered -or -not $result.windows_ui) { throw "Incomplete frozen self-test" }
$infoPath = Join-Path $AppDir "_internal\build_info.json"
$info = Get-Content $infoPath | ConvertFrom-Json
if ($info.version -ne $Version -or $info.git_commit -ne $Commit) { throw "Packaged build metadata mismatch" }
Copy-Item $Log $Dist
Copy-Item (Join-Path $Root "README_APP.md") $AppDir
# FFmpeg redistribution information travels with its bundled executables.
"FFmpeg: https://ffmpeg.org/ (GPL build supplied by Chocolatey ffmpeg). Build source: https://github.com/GyanD/codexffmpeg. FFmpeg licensing: https://ffmpeg.org/legal.html" | Set-Content (Join-Path $AppDir "FFmpeg-NOTICE.txt")
Compress-Archive -Path (Join-Path $AppDir "*") -DestinationPath $Zip -Force
Write-Host "Built version $Version (git=$Commit): $Zip"
