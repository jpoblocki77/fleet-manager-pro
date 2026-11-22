; Fleet Manager Pro - one-file installer for one buyer's copy. Compiled by "Build Installer.py" (defines: FmpSrc, FmpVer, FmpNum, FmpOut).
; Per user, no admin: the program updates itself and keeps its data folder next to the .exe, so the folder must be writable.
[Setup]
AppId={{7C0B1B0E-5B0D-4C1E-9D0B-6B7F3A52F3D1}
AppName=Fleet Manager Pro
AppVersion={#FmpVer}
AppVerName=Fleet Manager Pro {#FmpVer}
AppPublisher=Fleet Manager Pro
VersionInfoVersion={#FmpNum}
DefaultDirName={localappdata}\Programs\Fleet Manager Pro
DisableProgramGroupPage=yes
DefaultGroupName=Fleet Manager Pro
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile={#FmpSrc}\Licence Agreement.txt
OutputDir={#FmpOut}
OutputBaseFilename=Fleet Manager Pro Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
UninstallDisplayIcon={app}\Fleet Manager Pro.exe

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"

[Files]
; never ship or touch a data folder: an existing company is left exactly as it is
Source: "{#FmpSrc}\*"; DestDir: "{app}"; Excludes: "\data,\data\*"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\Fleet Manager Pro"; Filename: "{app}\Fleet Manager Pro.exe"; WorkingDir: "{app}"
Name: "{group}\Fleet Manager Pro (LAN)"; Filename: "{app}\Fleet Manager Pro.exe"; Parameters: "--lan"; WorkingDir: "{app}"
Name: "{group}\Installation Guide"; Filename: "{app}\Installation Guide.pdf"
Name: "{group}\Your Data (read me)"; Filename: "{app}\READ ME - Your Data.txt"
Name: "{userdesktop}\Fleet Manager Pro"; Filename: "{app}\Fleet Manager Pro.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\Fleet Manager Pro.exe"; WorkingDir: "{app}"; Description: "Start Fleet Manager Pro"; Flags: nowait postinstall skipifsilent
; uninstalling removes only what was installed: the data folder (company, licence) stays
