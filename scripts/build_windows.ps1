# Windows 네이티브 빌드: 테스트 → PyInstaller(onedir) → 스모크 → Inno Setup 설치파일.
# Windows 11 x64(또는 GitHub Actions windows-latest)에서만 실행한다. macOS에서 실행 불가.
#   powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
# 환경변수: PYTHON(기본 .venv\Scripts\python.exe), SKIP_TESTS=1, ISCC(Inno Setup 컴파일러 경로)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

if ($env:OS -ne "Windows_NT") { throw "Windows에서만 실행합니다." }
if ($env:PYTHON) {
    $Python = $env:PYTHON
} else {
    $Python = ".venv\Scripts\python.exe"
    if (-not (Test-Path $Python)) { py -3.12 -m venv .venv }
}
$Arch = & $Python -c "import platform; print(platform.machine())"
if ($Arch -ne "AMD64") { throw "x64(AMD64) Python이 필요합니다: $Arch" }
$Version = & $Python -c "import sys; sys.path.insert(0, 'src'); import rentdesk; print(rentdesk.__version__)"

& $Python -m pip install -q --upgrade pip
& $Python -m pip install -q -e ".[dev,build]"
if ($LASTEXITCODE -ne 0) { throw "의존성 설치 실패" }
if ($env:SKIP_TESTS -ne "1") {
    $env:QT_QPA_PLATFORM = "offscreen"
    & $Python -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "테스트 실패" }
    Remove-Item Env:QT_QPA_PLATFORM
}

Remove-Item -Recurse -Force build\pyinstaller, dist\RentalDesk -ErrorAction SilentlyContinue
& $Python -m PyInstaller --noconfirm --clean --workpath build\pyinstaller --distpath dist packaging\pyinstaller\rentdesk.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 실패" }

& (Join-Path $PSScriptRoot "smoke_windows.ps1") -Exe "dist\RentalDesk\RentalDesk.exe"

$Iscc = if ($env:ISCC) { $env:ISCC } else { "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" }
if (-not (Test-Path $Iscc)) { throw "Inno Setup 6(ISCC.exe)을 찾을 수 없습니다: $Iscc" }
& $Iscc "/DAppVersion=$Version" "/DSourceDir=$(Resolve-Path dist\RentalDesk)" "/DOutputDir=$(Resolve-Path dist)" packaging\windows\rentdesk.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup 실패" }

$Setup = "dist\RentalDesk_${Version}_win-x64_setup.exe"
Get-FileHash -Algorithm SHA256 $Setup | Format-List
Write-Host "== Windows 설치파일 생성: $Setup (Authenticode 미서명 시 SmartScreen 경고 가능)"
