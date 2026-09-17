; ============================================================
; VoltageAir.iss —— Inno Setup 打包脚本
; 把 dist\VoltageAir\ 打包成一个 Windows 安装程序
; ============================================================

#define MyAppName      "VoltageAir"
#define MyAppVersion   "1.0.0"
#define MyAppPublisher "lxxvah"
#define MyAppURL       "https://github.com/lxxvah/VoltageAir"
#define MyAppExeName   "VoltageAir.exe"

; PyInstaller 打包产物目录（相对本 .iss 文件所在目录）
#define MyDistDir      "dist\VoltageAir"

[Setup]
; AppId 是安装包的唯一标识：升级版本时保持不变，卸载识别、覆盖安装都靠它
; 首次生成后不要改，否则会变成"两个不同的软件"
AppId={{8B6E2C41-9A3F-4E57-B0D1-7C5F2A9E4B31}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}

; 默认安装到 Program Files\VoltageAir
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes

; 输出目录和文件名
OutputDir=installer
OutputBaseFilename=VoltageAir_Setup_{#MyAppVersion}

; 安装包图标
SetupIconFile=app.ico

; 压缩（lzma2/max 体积最小，压缩慢一点）
Compression=lzma2/max
SolidCompression=yes

; 64 位程序（PyInstaller 打包的 Python 通常是 64 位）
ArchitecturesInstallIn64BitMode=x64
ArchitecturesAllowed=x64

; 权限：普通用户目录无需管理员；Program Files 需要管理员
; 这里默认按“当前用户”安装，免 UAC 弹窗；要装 Program Files 改成 admin
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

; 卸载时不清空用户产生的数据（logs）
UninstallDisplayIcon={app}\{#MyAppExeName}

; 中文界面
[Languages]
Name: "chinese"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
; 桌面快捷方式（可选，默认勾选）
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; \
      GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce

[Files]
; ★ 只打包 dist\VoltageAir 下的内容，但排除运行时生成的 logs 目录
Source: "{#MyDistDir}\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#MyDistDir}\_internal\*";    DestDir: "{app}\_internal"; \
        Flags: ignoreversion recursesubdirs createallsubdirs

; 顺带把 README 和 LICENSE 装到安装目录，方便用户查看
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "LICENSE";   DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist

[Icons]
; 开始菜单
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
; 桌面快捷方式（受 Tasks 控制）
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; \
      Tasks: desktopicon

[Run]
; 安装完成后勾选“立即启动”
Filename: "{app}\{#MyAppExeName}"; \
  Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; \
  Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 卸载时删掉运行过程可能产生的日志目录（如果留在安装目录里）
Type: filesandordirs; Name: "{app}\logs"