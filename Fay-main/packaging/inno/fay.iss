#define MyAppName "Fay"
#define MyAppVersion "4.8.1"
#define MyAppPublisher "Fay"
#define MyAppExeName "fay.exe"

[Setup]
AppId={{8C6DE291-07A5-4D9D-99E5-C0A33B98C781}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Fay
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\..\dist\installer
OutputBaseFilename=FaySetup-{#MyAppVersion}
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
Source: "..\..\dist\fay\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "startup.out,startup.err,logs\*,memory\*,samples\*,cache_data\*,system.conf,system.conf.bak,config.json,cache_data\system.conf,cache_data\config.json,faymcp\data\mcp_servers.json,faymcp\data\mcp_prestart_tools.json,faymcp\data\mcp_tool_states.json"
Source: "..\..\dist\fay\memory\fay.db"; DestDir: "{app}\memory"; Flags: ignoreversion onlyifdoesntexist
Source: "..\..\dist\fay\memory\user_profiles.db"; DestDir: "{app}\memory"; Flags: ignoreversion onlyifdoesntexist
Source: "..\..\dist\fay\faymcp\data\mcp_servers.json"; DestDir: "{app}\faymcp\data"; Flags: ignoreversion onlyifdoesntexist
Source: "..\..\dist\fay\faymcp\data\mcp_prestart_tools.json"; DestDir: "{app}\faymcp\data"; Flags: ignoreversion onlyifdoesntexist
Source: "..\..\dist\fay\faymcp\data\mcp_tool_states.json"; DestDir: "{app}\faymcp\data"; Flags: ignoreversion onlyifdoesntexist

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Parameters: "{code:GetLaunchParameters}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Parameters: "{code:GetLaunchParameters}"; Tasks: desktopicon; WorkingDir: "{app}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Parameters: "{code:GetLaunchParameters}"; Description: "安装完成后立即启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent; WorkingDir: "{app}"

[Code]
function InitializeSetup(): Boolean;
var
  Version: TWindowsVersion;
begin
  Result := True;
  GetWindowsVersionEx(Version);

  if (Version.Major < 6) or ((Version.Major = 6) and (Version.Minor <= 1)) then
  begin
    MsgBox(
      '当前安装包要求 Windows 8 或更高版本。' + #13#10#13#10 +
      '本正式版基于 Python 3.12 构建，在 Windows 7 / Server 2008 R2 等旧系统上会因为缺少 api-ms-win-core-path-l1-1-0.dll 等依赖而无法启动。' + #13#10#13#10 +
      '请升级系统，或改用单独的 legacy 安装包。',
      mbCriticalError,
      MB_OK
    );
    Result := False;
  end;
end;

#include "fay-config-pages.iss"
