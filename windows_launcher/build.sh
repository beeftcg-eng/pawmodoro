#!/usr/bin/env bash
# build.sh - Rebuilds Pawmodoro.exe (the Windows taskbar-pin/shortcut
# launcher target) via MinGW cross-compilation from Linux. Only needed
# again if launcher.c changes or the app icon changes; the compiled
# Pawmodoro.exe is committed to the repo so most contributors never need
# to run this.
#
# Requires: mingw-w64 (x86_64-w64-mingw32-gcc, x86_64-w64-mingw32-windres)
#   Fedora/Bazzite: sudo dnf install mingw64-gcc
#   Debian/Ubuntu:  sudo apt install mingw-w64
#   macOS/Linuxbrew: brew install mingw-w64
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

cp ../resources/icon.ico .
x86_64-w64-mingw32-windres pawmodoro.rc -O coff -o pawmodoro_res.o
x86_64-w64-mingw32-gcc -mwindows -municode -O2 -o Pawmodoro.exe launcher.c pawmodoro_res.o -lshlwapi
rm -f pawmodoro_res.o icon.ico

echo "Built windows_launcher/Pawmodoro.exe"
file Pawmodoro.exe
