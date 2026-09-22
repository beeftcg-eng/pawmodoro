# uninstall_prep.ps1 - stops any running Pawmodoro process before the installer
# removes %LOCALAPPDATA%\Pawmodoro. Pawmodoro keeps running in the system tray
# after its window is closed (by design), so it can still be holding files open
# at uninstall time - mirrors the same kill step install.ps1 runs before an
# update.
Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe' OR Name = 'Pawmodoro.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*Pawmodoro*main.py*" -or $_.Name -eq 'Pawmodoro.exe' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 1
