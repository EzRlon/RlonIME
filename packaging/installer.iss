; ============================================================
;  RlonIME · 工程领域中文输入法  —— 安装向导脚本
;  用 Inno Setup 6 编译，产出 RlonIME_Setup_vX.Y.Z.exe
;
;  使用方式：
;    1. 先运行 packaging/build.py（PyInstaller 产出 distN/RlonIME）
;    2. build.py 会自动改写 SourceDir / OutputDir 后调用本脚本
;
;  设计取舍：
;  - PrivilegesRequired=lowest → 装到 %LOCALAPPDATA%\Programs，不需要管理员。
;    SolidWorks / AutoCAD 用户多数没有管理员权限，per-user 安装是唯一能装上的方案。
;  - 卸载不删 {app}\data → 那里面可能有用户自己增改的行业词库，删了就找不回来。
;    用户个人数据（记忆库、使用记录）在 %APPDATA%\RlonIME，由程序做一次性迁移。
;  - v4.0 品牌统一：进程名 / 安装目录 / 数据目录全部改为 RlonIME。
;    升级时由程序把旧目录的 config.json 与 %APPDATA%\ZhiyuIME 的用户数据搬过来；
;    本脚本负责关掉旧进程、清掉旧的开机自启项、删掉旧的可执行文件（保留 data）。
; ============================================================

#define MyAppName "RlonIME · 工程领域中文输入法"
#define MyAppShortName "RlonIME"
#define MyAppVersion "4.0.3"
#define MyAppPublisher "Rlon"
#define MyAppExeName "RlonIME.exe"
#define LegacyExeName "ZhiyuIME.exe"
; SourceDir / OutputDir 由 packaging/build.py 在编译前动态改写
#define SourceDir "..\dist33\RlonIME"

[Setup]
; AppId 保持不变：让新版被识别为「同一软件升级」，而不是并存安装两份。
AppId={{8F3A6C21-7B4D-4E19-9C2A-5D8E1F0A73B4}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppShortName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\RlonIME
DefaultGroupName={#MyAppShortName}
DisableProgramGroupPage=yes
OutputDir=..\dist33\installer
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

[InstallDelete]
; 只删旧版的可执行文件与 _internal（程序本体），**保留 data/** ——
; 用户可能在里面改过词库；配置迁移由程序启动时完成。
Type: files; Name: "{localappdata}\Programs\ZhiyuIME\ZhiyuIME.exe"
Type: filesandordirs; Name: "{localappdata}\Programs\ZhiyuIME\_internal"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppShortName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppShortName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppShortName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; v4.0：无条件清掉旧版的开机自启项（ValueName 从 ZhiyuIME 改成了 RlonIME，
; 不清就会留下两个自启项，启动时两个进程抢键盘钩子）。
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
    ValueName: "ZhiyuIME"; Flags: deletevalue noerror
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
    ValueType: string; ValueName: "RlonIME"; ValueData: """{app}\{#MyAppExeName}"""; \
    Flags: uninsdeletevalue; Tasks: startupicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即启动 {#MyAppShortName}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 只清理运行期日志。词库（{app}\data）保留：用户可能自己增改过行业词库，
; 记忆库在 %APPDATA%\RlonIME，由用户自行决定是否删除。
Type: filesandordirs; Name: "{userappdata}\RlonIME\logs"

[UninstallRun]
Filename: "{cmd}"; Parameters: "/c taskkill /IM {#MyAppExeName} /F >nul 2>&1"; \
    Flags: runhidden; RunOnceId: "KillApp"

[Code]
{ 升级前先结束旧版进程：它的可执行文件名与新版本不同，Inno 的
  CloseApplications 检测不到，留着会继续占键盘钩子与单实例。 }
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{cmd}'),
       '/c taskkill /IM {#LegacyExeName} /F >nul 2>&1',
       '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(ExpandConstant('{cmd}'),
       '/c taskkill /IM {#MyAppExeName} /F >nul 2>&1',
       '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := '';
end;
