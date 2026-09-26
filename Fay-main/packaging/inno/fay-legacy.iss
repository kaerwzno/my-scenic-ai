#define MyAppName "Fay Legacy"
#define MyAppVersion "4.8.1"
#define MyAppPublisher "Fay"
#define MyAppExeName "fay.exe"

[Setup]
AppId={{4B53F5C6-89B3-4FB1-89CD-0C6CC4DB9C66}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Fay Legacy
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\..\dist\installer
OutputBaseFilename=FaySetup-{#MyAppVersion}-legacy
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile=..\..\favicon.ico
UninstallDisplayIcon={app}\favicon.ico

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Default.isl,ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："; Flags: unchecked

[Dirs]
Name: "{app}\logs"; Flags: uninsneveruninstall
Name: "{app}\memory"; Flags: uninsneveruninstall
Name: "{app}\samples"; Flags: uninsneveruninstall
Name: "{app}\cache_data"; Flags: uninsneveruninstall

[Files]
Source: "..\..\dist\fay-legacy\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "startup.out,startup.err,logs\*,memory\*,samples\*,cache_data\*,system.conf,system.conf.bak,config.json,cache_data\system.conf,cache_data\config.json,faymcp\data\mcp_servers.json,faymcp\data\mcp_prestart_tools.json,faymcp\data\mcp_tool_states.json"
Source: "..\..\dist\fay-legacy\memory\fay.db"; DestDir: "{app}\memory"; Flags: ignoreversion onlyifdoesntexist
Source: "..\..\dist\fay-legacy\memory\user_profiles.db"; DestDir: "{app}\memory"; Flags: ignoreversion onlyifdoesntexist
Source: "..\..\dist\fay-legacy\faymcp\data\mcp_servers.json"; DestDir: "{app}\faymcp\data"; Flags: ignoreversion onlyifdoesntexist
Source: "..\..\dist\fay-legacy\faymcp\data\mcp_prestart_tools.json"; DestDir: "{app}\faymcp\data"; Flags: ignoreversion onlyifdoesntexist
Source: "..\..\dist\fay-legacy\faymcp\data\mcp_tool_states.json"; DestDir: "{app}\faymcp\data"; Flags: ignoreversion onlyifdoesntexist

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Parameters: "{code:GetLaunchParameters}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Parameters: "{code:GetLaunchParameters}"; Tasks: desktopicon; WorkingDir: "{app}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Parameters: "{code:GetLaunchParameters}"; Description: "安装完成后立即启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent; WorkingDir: "{app}"

[Code]
function InitializeSetup(): Boolean;
begin
  Result := True;
end;

#include "fay-config-pages.iss"
