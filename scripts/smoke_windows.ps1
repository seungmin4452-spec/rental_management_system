# 빌드/설치된 RentDeskLite.exe 구동 검증: 자기 점검 + 번들 안 비밀정보/DB 미포함 확인.
# 사용자 데이터 폴더를 건드리지 않는다(자기 점검은 임시 폴더에서만 동작).
#   scripts\smoke_windows.ps1 -Exe "dist\RentDeskLite\RentDeskLite.exe"
#   scripts\smoke_windows.ps1 -Exe "$env:LOCALAPPDATA\Programs\RentDesk Lite\RentDeskLite.exe"
param([string]$Exe = "dist\RentDeskLite\RentDeskLite.exe")
$ErrorActionPreference = "Stop"
if (-not (Test-Path $Exe)) { throw "실행 파일 없음: $Exe" }

$Out = Join-Path ([System.IO.Path]::GetTempPath()) ("rentdesk_selfcheck_{0}.json" -f [guid]::NewGuid())
# windowed exe는 콘솔에 붙지 않으므로 종료를 기다린 뒤 결과 파일을 읽는다
$p = Start-Process -FilePath $Exe -ArgumentList @("--self-check", "`"$Out`"") -Wait -PassThru
if (-not (Test-Path $Out)) { throw "자기 점검 결과 파일이 없습니다(exit=$($p.ExitCode))" }
$Result = Get-Content $Out -Raw -Encoding UTF8 | ConvertFrom-Json
Get-Content $Out -Raw -Encoding UTF8 | Write-Host
if (-not $Result.ok -or $p.ExitCode -ne 0) { throw "자기 점검 실패" }

$Dir = Split-Path $Exe -Parent
$Bad = Get-ChildItem -Path $Dir -Recurse -File -Include *.db, *.sqlite, *.pem, *.p12, *.pfx, signing_key*
if ($Bad) { throw "번들에 DB/인증서/키 파일이 있습니다: $($Bad.FullName -join ', ')" }
Write-Host "== Windows 스모크 통과: $Exe"
