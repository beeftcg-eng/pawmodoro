; pawmodoro.iss - Inno Setup script that produces a real, silently-installable
; Pawmodoro-Setup.exe (needed for winget, and any store/package manager that
; requires a proper Windows installer rather than a zip + script). Built on
; GitHub's windows-latest Actions runners, which ship Inno Setup preinstalled
; - see .github/workflows/build-windows-installer.yml.
;
; Deliberately does NOT reimplement install.ps1's setup logic (venv creation,
; pip install, shortcut creation with the correct AppUserModelID-consistent
; launcher) - that stays the single source of truth, used identically whether
; someone runs install.bat by hand or arrives via this installer. This script
; only stages the app's source files into a scratch directory, runs
; install.ps1 from there (exactly like a manual zip extract + install.bat
; double-click), and registers a real Add/Remove Programs entry so winget
; and Windows itself recognize it as installed/upgradable/uninstallable.
;
; AppVersion is passed in at compile time: ISCC.exe /DAppVersion=X.Y.Z
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{A85DB4A1-3651-47BC-B887-2B4055C6E86A}
AppName=Pawmodoro
AppVersion={#AppVersion}
AppPublisher=beeftcg
AppPublisherURL=https://github.com/beeftcg-eng/pawmodoro
AppSupportURL=https://github.com/beeftcg-eng/pawmodoro/issues
; A separate scratch directory, not %LOCALAPPDATA%\Pawmodoro itself -
; install.ps1 copies FROM its own script directory INTO %LOCALAPPDATA%\Pawmodoro
; (wiping the destination except venv\ first), so the two must never be the
; same directory or the wipe step would delete install.ps1's own source
; files out from under itself before it can copy them.
DefaultDirName={localappdata}\Pawmodoro-src
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
PrivilegesRequired=lowest
OutputDir=Output
OutputBaseFilename=Pawmodoro-Setup
SetupIconFile=..\resources\icon.ico
UninstallDisplayIcon={localappdata}\Pawmodoro\resources\icon.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\*.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\install.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\install.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\resources\*"; DestDir: "{app}\resources"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\windows_launcher\Pawmodoro.exe"; DestDir: "{app}\windows_launcher"; Flags: ignoreversion
Source: "uninstall_prep.ps1"; DestDir: "{app}"; Flags: ignoreversion

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\install.ps1"""; WorkingDir: "{app}"; StatusMsg: "Setting up Pawmodoro (this can take a minute - it's creating a private Python environment)..."; Flags: waituntilterminated runhidden

[UninstallRun]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\uninstall_prep.ps1"""; Flags: waituntilterminated runhidden

[UninstallDelete]
Type: filesandordirs; Name: "{localappdata}\Pawmodoro"
Type: files; Name: "{userdesktop}\Pawmodoro.lnk"
Type: files; Name: "{userappdata}\Microsoft\Windows\Start Menu\Programs\Pawmodoro.lnk"
