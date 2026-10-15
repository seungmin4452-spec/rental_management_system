; RentDesk Lite — Inno Setup 6 설치 스크립트 (Windows 11 x64). Windows에서만 빌드한다.
;   iscc /DAppVersion=0.1.0 /DSourceDir=..\..\dist\RentDeskLite /DOutputDir=..\..\dist packaging\windows\rentdesk.iss
; 사용자 데이터(%LOCALAPPDATA%\RentDeskLite)는 설치 폴더와 분리되어 있으며 제거 시 삭제하지 않는다.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\dist\RentDeskLite"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\dist"
#endif

[Setup]
; AppId는 출시 후 바꾸지 않는다(업데이트·제거가 같은 앱으로 인식되는 기준)
AppId={{49D58144-6585-44F3-AC73-C9CAA89020EF}
AppName=RentDesk Lite
AppVersion={#AppVersion}
AppVerName=RentDesk Lite {#AppVersion}
AppPublisher=RentDesk Lite
DefaultDirName={autopf}\RentDesk Lite
DefaultGroupName=RentDesk Lite
DisableProgramGroupPage=yes
; 관리자 권한 없이 사용자 단위 설치 가능, 원하면 모든 사용자용 설치 선택
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.22000
OutputDir={#OutputDir}
OutputBaseFilename=RentDeskLite_{#AppVersion}_win-x64_setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=RentDesk Lite
UninstallDisplayIcon={app}\RentDeskLite.exe
CloseApplications=yes
; Authenticode 서명은 선택. 서명할 때만 SignTool을 설정(인증서는 저장소 밖, CI 비밀값)
; SignTool=rentdesksign

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Tasks]
Name: "desktopicon"; Description: "바탕 화면에 바로가기 만들기"; GroupDescription: "추가 작업:"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\RentDesk Lite"; Filename: "{app}\RentDeskLite.exe"
Name: "{autodesktop}\RentDesk Lite"; Filename: "{app}\RentDeskLite.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\RentDeskLite.exe"; Description: "RentDesk Lite 실행"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 설치 폴더 안의 파일만 정리한다. 사용자 데이터(%LOCALAPPDATA%\RentDeskLite)는 지우지 않는다.
Type: dirifempty; Name: "{app}"

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and (not UninstallSilent) then
    MsgBox('프로그램을 제거했습니다.' + #13#10 +
           '장비·고객·예약 데이터는 삭제하지 않았습니다(' + ExpandConstant('{localappdata}') + '\RentDeskLite).' + #13#10 +
           '완전히 지우려면 먼저 백업 여부를 확인한 뒤 해당 폴더를 직접 삭제하세요.',
           mbInformation, MB_OK);
end;
