; ============================================================
;  RlonIME · 专业领域词汇助手  —— 安装向导脚本（v2.2）
;  用 Inno Setup 6 编译，产出 RlonIME_Setup_v2.2.0.exe
;
;  使用方式：
;    1. 先运行 PyInstaller 生成 dist15/ZhiyuIME（构建命令见 README）
;    2. 用 Inno Setup Compiler 打开本文件 -> Build -> Compile
;
;  设计取舍：
;  - PrivilegesRequired=lowest  → 装到 %LOCALAPPDATA%，不需要管理员权限。
;    SolidWorks / AutoCAD 用户多数没有管理员权限，per-user 安装是唯一能装上的方案。
;  - 卸载不删 {app}\data → 那里面可能有用户自己增改的行业词库，删了就找不回来。
;    用户个人数据（记忆库、使用记录）在 %APPDATA%\ZhiyuIME，本就不受影响。
;  - v2.2 仅改显示层为 RlonIME；安装目录/进程名/数据目录保留 ZhiyuIME
;    （老用户升级免迁移）；设置中心已并入候选条，不再有「设置中心」快捷方式。
; ============================================================

#define MyAppName "RlonIME · 专业领域词汇助手"
#define MyAppShortName "RlonIME"
#define MyAppVersion "3.0.0"
#define MyAppPublisher "Rlon"
#define MyAppExeName "ZhiyuIME.exe"
#define SourceDir "..\dist25\ZhiyuIME"

[Setup]
AppId={{8F3A6C21-7B4D-4E19-9C2A-5D8E1F0A73B4}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppShortName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\ZhiyuIME
DefaultGroupName={#MyAppShortName}
DisableProgramGroupPage=yes
OutputDir=..\dist25\installer
OutputBaseFilename=RlonIME_Setup_v{#MyAppVersion}
SetupIconFile=zhiyu.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppShortName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no
DisableWelcomePage=no
LicenseFile=license.txt

[Languages]
Name: "chs"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："
Name: "startupicon"; Description: "开机自动启动（常驻托盘）"; GroupDescription: "附加任务："

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppShortName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppShortName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppShortName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
    ValueType: string; ValueName: "ZhiyuIME"; ValueData: """{app}\{#MyAppExeName}"""; \
    Flags: uninsdeletevalue; Tasks: startupicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即启动 {#MyAppShortName}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 只清理运行期日志。词库（{app}\data）保留：用户可能自己增改过行业词库，
; 记忆库在 %APPDATA%\ZhiyuIME，由用户自行决定是否删除。
Type: filesandordirs; Name: "{userappdata}\ZhiyuIME\logs"

[UninstallRun]
Filename: "{cmd}"; Parameters: "/c taskkill /IM {#MyAppExeName} /F >nul 2>&1"; \
    Flags: runhidden; RunOnceId: "KillApp"
