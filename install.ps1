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
# takes effect. The actual long-running process is always pythonw.exe
# (Pawmodoro.exe, see $AppExe below, just launches that and exits), but
# match all three names for safety across install versions.
Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe' OR Name = 'Pawmodoro.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*Pawmodoro*main.py*" } |
    ForEach-Object {
        Write-Host "Stopping a running Pawmodoro instance (PID $($_.ProcessId))..."
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
Start-Sleep -Seconds 1

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

# Full wipe of the previous install's app files (but keep the venv, so we
# don't reinstall dependencies every time) - removes everything, including
# any file/folder from an older version that no longer exists in this one,
# so nothing stale from a previous install can linger. Safe: this is
# %LOCALAPPDATA%\Pawmodoro (app code only) - actual data (notes, settings)
# lives separately under %APPDATA%\Pawmodoro and is never touched here.
Get-ChildItem -Path $InstallDir -Force -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -ne "venv" } |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

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

# A real, custom-built Pawmodoro.exe (windows_launcher/, see build.sh)
# that just launches venv\Scripts\pythonw.exe main.py and exits. Earlier
# attempts pointed the shortcut at pythonw.exe directly, or a same-file
# copy of it renamed to Pawmodoro.exe - Windows identifies a shortcut's
# target partly by its embedded resources (icon, version info), not just
# its filename, so a renamed copy of Python's own interpreter still
# carried Python's identity and could get confused with an unrelated,
# already-installed Python entry (reported: pinning it showed up as
# "Idle Python"). This binary shares no bytes with any Python
# interpreter and has its own embedded icon/version info, so it can't be
# confused with one. It lives at the install root (not inside
# venv\Scripts) since, unlike a renamed pythonw.exe, it doesn't need to
# be colocated with the interpreter to work.
$AppExe = Join-Path $InstallDir "Pawmodoro.exe"
Copy-Item -Path (Join-Path $SrcDir "windows_launcher\Pawmodoro.exe") -Destination $AppExe -Force

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

# Desktop shortcut. Targets the custom Pawmodoro.exe launcher built above
# (it takes no arguments - it hardcodes the venv\Scripts\pythonw.exe
# main.py invocation itself) rather than run.bat or pythonw.exe directly
# - see the comment above $AppExe for why both of those block "Pin to
# taskbar" and/or get misidentified once pinned.
$IconPath = Join-Path $InstallDir "resources\icon.ico"
$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut("$env:USERPROFILE\Desktop\Pawmodoro.lnk")
$Shortcut.TargetPath = $AppExe
$Shortcut.WorkingDirectory = $InstallDir
if (Test-Path $IconPath) {
    $Shortcut.IconLocation = $IconPath
}
$Shortcut.Save()

Write-Host ""
Write-Host "Done! A 'Pawmodoro' shortcut was added to your Desktop."
Write-Host "Double-click it to launch. (If something looks wrong, run"
Write-Host "$InstallDir\run_console.bat instead to see error output.)"
Write-Host "Want it on your taskbar? Right-click the Desktop shortcut and choose"
Write-Host "'Pin to taskbar'."

if (-not (Get-Command ffplay -ErrorAction SilentlyContinue)) {
    Write-Host ""
    Write-Host "Note: 'ffplay' (ffmpeg) wasn't found. Ambient sounds will still work"
    Write-Host "via a fallback audio backend, but for the smoothest/gapless looping"
    Write-Host "and support for non-WAV custom sound files, install ffmpeg:"
    Write-Host "  winget install ffmpeg"
}
