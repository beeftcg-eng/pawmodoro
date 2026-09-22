# install.ps1 - Sets up Pawmodoro on Windows: creates a private virtual
# environment under %LOCALAPPDATA%\Pawmodoro, installs dependencies, and
# adds a Desktop shortcut. Safe to re-run to update to a newer version.
#
# -Relaunch: start Pawmodoro again once the install is done. Used by the app's
# own "Update" button (update_checker.py), which quits, runs this, and expects
# the new version to come back up by itself.
param([switch]$Relaunch)

$ErrorActionPreference = "Stop"
$SrcDir = $PSScriptRoot
$InstallDir = Join-Path $env:LOCALAPPDATA "Pawmodoro"
$VenvDir = Join-Path $InstallDir "venv"

Write-Host "Installing Pawmodoro to $InstallDir ..."

# Pawmodoro keeps running in the system tray after you close its window
# (by design), so an old process can quietly keep running under the OLD
# code even after you reinstall. Stop it first so an update actually
# takes effect. The actual long-running process is always pythonw.exe
# (the launcher exe, see $AppExe below, just launches that and exits
# immediately), but the LIKE match covers it too, in case a version-
# specific launcher exe from an install predating this script's switch
# to a stable filename is still what's actually running.
Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe' OR Name LIKE 'Pawmodoro%.exe'" -ErrorAction SilentlyContinue |
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

# Optional: lets the player bar control a browser tab (YouTube Music,
# etc.) via Windows' own media-control API. Deliberately NOT in
# requirements.txt and installed as its own isolated, best-effort step -
# it's a niche, infrequently-updated package that doesn't always have a
# prebuilt wheel for every Python version, and without one, pip falls
# back to compiling it from source, which needs a full Visual Studio C++
# toolchain most machines don't have. A failure here must never take
# down the rest of the install - the app already runs fine without it
# (Spotify Connect still works either way), so this is wrapped to swallow
# any failure rather than letting it escalate given $ErrorActionPreference
# above.
$PreviousErrorAction = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& "$VenvDir\Scripts\pip.exe" install "winsdk>=1.0.0b1" --quiet *>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "Note: couldn't install the optional 'winsdk' package (used for controlling"
    Write-Host "a browser tab's music from the player bar). This is likely because there's"
    Write-Host "no prebuilt version of it for your Python version yet. Pawmodoro will still"
    Write-Host "install and run fine - Spotify Connect in the Music tab still works either way."
}
$ErrorActionPreference = $PreviousErrorAction

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
# confused with one.
#
# The filename is stable ("Pawmodoro.exe") rather than version-suffixed,
# so a Desktop/Start Menu pin (which stores its own resolved target path,
# separate from the shortcut it was pinned from) keeps working across
# updates instead of turning into a dangling reference to a deleted file
# every release. This is safe against the old version-suffix's original
# stale-shell-icon-cache concern: the "wipe everything except venv" step
# above deletes the previous install's exe (whatever its name) before
# this one is copied in, and main.py now also sets an explicit, constant
# AppUserModelID on the running process, which is what Explorer actually
# keys pin/grouping identity off of - a leftover cached icon would be
# cosmetic at worst, not an identity mix-up.
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

# Start Menu shortcut too (per-user, no admin rights needed) - on modern
# Windows, "Pin to taskbar" is most reliably offered from a Start Menu
# entry rather than an arbitrary Desktop shortcut.
$StartMenuDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
New-Item -ItemType Directory -Force -Path $StartMenuDir | Out-Null
$StartShortcut = $Shell.CreateShortcut((Join-Path $StartMenuDir "Pawmodoro.lnk"))
$StartShortcut.TargetPath = $AppExe
$StartShortcut.WorkingDirectory = $InstallDir
if (Test-Path $IconPath) {
    $StartShortcut.IconLocation = $IconPath
}
$StartShortcut.Save()

Write-Host ""
Write-Host "Done! A 'Pawmodoro' shortcut was added to your Desktop and Start Menu."
Write-Host "Double-click it to launch. (If something looks wrong, run"
Write-Host "$InstallDir\run_console.bat instead to see error output.)"
Write-Host "Want it on your taskbar? Find it in the Start Menu and choose"
Write-Host "'Pin to taskbar', or right-click its icon in the taskbar while"
Write-Host "it's running and choose the same."

if (-not (Get-Command ffplay -ErrorAction SilentlyContinue)) {
    Write-Host ""
    Write-Host "Note: 'ffplay' (ffmpeg) wasn't found. Ambient sounds will still work"
    Write-Host "via a fallback audio backend, but for the smoothest/gapless looping"
    Write-Host "and support for non-WAV custom sound files, install ffmpeg:"
    Write-Host "  winget install ffmpeg"
}

if ($Relaunch) {
    Write-Host ""
    Write-Host "Restarting Pawmodoro..."
    Start-Process -FilePath $AppExe -WorkingDirectory $InstallDir
}
