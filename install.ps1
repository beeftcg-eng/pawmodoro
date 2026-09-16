# install.ps1 - Sets up Pawmodoro on Windows: creates a private virtual
# environment under %LOCALAPPDATA%\Pawmodoro, installs dependencies, and
# adds a Desktop shortcut. Safe to re-run to update to a newer version.

$ErrorActionPreference = "Stop"
$SrcDir = $PSScriptRoot
$InstallDir = Join-Path $env:LOCALAPPDATA "Pawmodoro"
$VenvDir = Join-Path $InstallDir "venv"

Write-Host "Installing Pawmodoro to $InstallDir ..."

# Pawmodoro keeps running in the system tray after you close its window
# (by design), so an old process can quietly keep running under the OLD
# code even after you reinstall. Stop it first so an update actually
# takes effect.
Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*Pawmodoro*main.py*" } |
    ForEach-Object {
        Write-Host "Stopping a running Pawmodoro instance (PID $($_.ProcessId))..."
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
Start-Sleep -Seconds 1

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

# Clean file wipe (but keep the venv, so we don't reinstall deps every time)
Get-ChildItem -Path $InstallDir -Filter "*.py" -ErrorAction SilentlyContinue | Remove-Item -Force
Remove-Item -Recurse -Force -Path (Join-Path $InstallDir "resources") -ErrorAction SilentlyContinue

Copy-Item -Path (Join-Path $SrcDir "*.py") -Destination $InstallDir
Copy-Item -Recurse -Path (Join-Path $SrcDir "resources") -Destination $InstallDir

if (-not (Test-Path $VenvDir)) {
    Write-Host "Creating virtual environment..."
    $pythonCmd = Get-Command py -ErrorAction SilentlyContinue
    if ($pythonCmd) {
        py -3 -m venv $VenvDir
    } else {
        python -m venv $VenvDir
    }
}

& "$VenvDir\Scripts\python.exe" -m pip install --upgrade pip --quiet
& "$VenvDir\Scripts\pip.exe" install -r (Join-Path $SrcDir "requirements.txt") --quiet

# Launcher: uses pythonw.exe (no console window) for double-click use
$RunScript = Join-Path $InstallDir "run.bat"
@"
@echo off
cd /d "$InstallDir"
start "" "$VenvDir\Scripts\pythonw.exe" main.py
"@ | Out-File -Encoding ascii -FilePath $RunScript -Force

# Console launcher too, for troubleshooting (shows print/[debug] output)
$RunConsoleScript = Join-Path $InstallDir "run_console.bat"
@"
@echo off
cd /d "$InstallDir"
"$VenvDir\Scripts\python.exe" main.py
pause
"@ | Out-File -Encoding ascii -FilePath $RunConsoleScript -Force

# Desktop shortcut
$IconPath = Join-Path $InstallDir "resources\icon.ico"
$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut("$env:USERPROFILE\Desktop\Pawmodoro.lnk")
$Shortcut.TargetPath = $RunScript
$Shortcut.WorkingDirectory = $InstallDir
if (Test-Path $IconPath) {
    $Shortcut.IconLocation = $IconPath
}
$Shortcut.Save()

Write-Host ""
Write-Host "Done! A 'Pawmodoro' shortcut was added to your Desktop."
Write-Host "Double-click it to launch. (If something looks wrong, run"
Write-Host "$InstallDir\run_console.bat instead to see error output.)"

if (-not (Get-Command ffplay -ErrorAction SilentlyContinue)) {
    Write-Host ""
    Write-Host "Note: 'ffplay' (ffmpeg) wasn't found. Ambient sounds will still work"
    Write-Host "via a fallback audio backend, but for the smoothest/gapless looping"
    Write-Host "and support for non-WAV custom sound files, install ffmpeg:"
    Write-Host "  winget install ffmpeg"
}
