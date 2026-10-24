$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

if (-not $env:FFMPEG_BIN) {
    $wingetPackages = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages"
    $installed = Get-ChildItem -Path $wingetPackages -Directory -Filter "Gyan.FFmpeg.Shared*" -ErrorAction SilentlyContinue |
        ForEach-Object { Get-ChildItem -Path $_.FullName -Directory -Filter "ffmpeg-*-full_build-shared" -ErrorAction SilentlyContinue } |
        Sort-Object Name -Descending |
        Select-Object -First 1
    if ($installed) { $env:FFMPEG_BIN = Join-Path $installed.FullName "bin" }
}

if ($env:FFMPEG_BIN -and (Test-Path $env:FFMPEG_BIN)) {
    $env:Path = "$env:FFMPEG_BIN;$env:Path"
}

if (-not (Get-Command ffprobe -ErrorAction SilentlyContinue)) {
    throw "FFprobe was not found. Install FFmpeg, then restart this script."
}

& (Join-Path $root ".venv\Scripts\python.exe") -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000