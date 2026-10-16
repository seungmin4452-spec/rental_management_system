# RentalDesk — macOS 개발 · macOS/Windows 이중 배포 명세

> **문서 버전:** 1.1 / **기준일:** 2026-10-15  
> **프로젝트 공식 결정:** 개발은 맥북에서, 판매용은 **macOS Apple Silicon(arm64)** 및 **Windows 11(x64)** 설치형 패키지로 별도 빌드한다. 같은 Git 커밋과 공통 도메인 로직을 사용한다.  
> Python/PySide6/SQLite 단일 코드베이스를 유지하며 클라우드 서버·브라우저 UI·자동 업데이트 서버는 MVP에 추가하지 않는다.

## A. 지원 환경 및 출시 게이트

| OS/아키텍처 | 우선순위 | 빌드 방법 | 최종 실기기 검증 |
|---|---|---|---|
| macOS Apple Silicon arm64 | P0 필수 | 맥북 macOS 네이티브 Python + PyInstaller | Python 없는 사용자 계정에서 `.dmg` 앱 설치/실행, 개인정보 저장, 백업, 재실행, 서명/공증 |
| Windows 11 x64 | P0 필수 | Windows VM/실기기 또는 Windows CI 러너 + PyInstaller + Inno Setup | Python 없는 테스트 PC에서 `.exe` 설치/실행/제거, 백업, 재실행 |
| macOS Intel x86_64 | 별도 검증 후 지원 | Intel 호스트/러너 빌드 또는 **모든 종속성이 지원하는 경우에만** universal2 검토 | Intel 실기기 설치/업무 검증 |
| Windows 10 x64 | 별도 검증 후 지원 | Windows x64 빌드 호환성 확인 | Windows 10 실기기 테스트 |

- 사용자의 맥북 칩이 M 시리즈인지 Intel인지 실행 초기에 파악한다. M 시리즈라면 로컬에서 arm64 앱을 빌드하고, Intel이라면 로컬에서 x86_64 개발·테스트 후 arm64 배포물을 위한 별도 Apple Silicon 빌드/테스트 환경을 마련한다. **소스코드가 Intel Mac을 지원한다는 것과 패키지까지 지원한다는 것은 다르다.**
- macOS 최소 버전은 Python/PySide6/Qt/PyInstaller 지원 범위와 실제 설치 결과로 결정한다. 목표 수치를 근거 없이 판매 페이지에 확정 기재하지 않는다.
- 개발환경 OS 버전보다 오래된 macOS를 지원하려면 최소 타깃 OS에서 빌드/테스트하는 방안과 macOS deployment target 문제를 검증한다.
- 지원 대상이 바뀌면 `PRD.md`, `TASKS.md`, 설치 가이드, 크몽 판매 문구를 동시에 수정한다.

## B. 단일 저장소/코드의 경계

```text
rentdesk-lite/
  PRD.md
  CROSS_PLATFORM.md
  CLAUDE.md
  TASKS.md
  WIREFRAMES.md
  START_HERE.md
  pyproject.toml
  launcher.py                # from rentdesk.app import main
  src/rentdesk/
    __main__.py
    domain/                 # 운영체제 무관: 예약/대여/반납/금액 계산
    services/               # 운영체제 무관: 트랜잭션 및 정책
    persistence/            # SQLite / migration / repository
    platform/
      paths.py              # QStandardPaths 추상화
      device_id.py          # OS별 기기 식별자 획득 + 테스트 주입
      resources.py          # 패키징된 파일의 안전한 리소스 조회
      environment.py        # OS/아키텍처/버전 확인
    ui/                     # PySide6 Qt Widgets, OS별 분기 최소화
    resources/              # 아이콘, 샘플 데이터, 번역 등
  tests/
    unit/
    integration/
    cross_platform/
    ui_smoke/
  scripts/
    build_macos.sh
    build_windows.ps1
    smoke_macos.sh
    smoke_windows.ps1
  packaging/
    pyinstaller/rentdesk.spec
    macos/create_dmg.sh
    macos/notarize.sh
    macos/RentalDesk.icns
    windows/rentdesk.iss
    windows/RentalDesk.ico
  .github/workflows/ci.yml
  docs/
    install_macos.md
    install_windows.md
    user_guide.md
    privacy_license.md
    release_notes.md
```

- UI는 OS별로 두 개 만들지 않는다. 같은 Qt UI, 공통 모델과 서비스, 공통 테스트를 사용한다.
- `platform/` 내 코드도 UI나 DB에 전역적으로 `sys.platform` 분기를 퍼뜨리지 않는다.
- `pyproject.toml`에 의존성 버전을 고정하고 빌드 러너에서 동일 lock/version을 사용한다. 플랫폼별 wheels은 다를 수 있으며 이를 릴리스 로그에 기록한다.
- Mac/Windows용 명칭, 앱 아이콘, 앱 버전, DB migration 버전을 동일하게 유지한다.

## C. 맥북 개발환경 초기화

macOS 터미널 기준(예시):

```bash
# Python 3.12가 이미 설치되어 있다는 전제. 설치 경로는 환경에 따라 다르다.
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
# Claude가 pyproject.toml의 dev extras를 작성한 뒤 실행
python -m pip install -e '.[dev]'
python -m pytest -q
python -m rentdesk
```

- Xcode Command Line Tools 및 macOS용 앱 서명 도구 사용 가능 여부를 확인한다.
- `Qt/PySide6` UI가 실제 맥에서 열리는지 확인한다. 서버나 `npm`을 강제로 추가하지 않는다.
- `python -m rentdesk`가 성공하기 전까지 배포용 `.app` 빌드를 먼저 진행하지 않는다.
- 초기 단계에서 Windows CI 테스트를 준비하되, Mac 빌드에 Windows 패키지를 억지로 포함하지 않는다.

## D. Windows 빌드 준비 (Mac이 주 개발환경이어도 필요)

선택지 1: GitHub Actions Windows runner에서 동일 커밋 체크아웃 → Windows Python 의존성 설치 → pytest → PyInstaller → Inno Setup으로 설치파일 생성 → 아티팩트 보관.

선택지 2: Windows 11 VM(정상 라이선스) 또는 실제 Windows PC에서 동일 작업.

**필수:** PyInstaller는 크로스컴파일러가 아니다. Windows `.exe`는 Windows에서 빌드하고 Mac `.app`은 Mac에서 빌드한다. `macOS에서 Windows용 EXE 빌드 성공`이라고 보고하면 안 된다.

Windows PowerShell 명령 예시 (`pyproject.toml`과 패키지가 생성된 뒤):

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --windowed --onedir --paths src --name RentalDesk launcher.py
# 위 단계 후 패키징 스크립트에서 Inno Setup 실행
```

macOS 터미널에서의 초기 개발 빌드 예시 (배포용 서명 전):

```bash
source .venv/bin/activate
python -m PyInstaller --noconfirm --clean --windowed --onedir \
  --name RentalDesk --osx-bundle-identifier com.example.rentaldesk \
  --paths src launcher.py
# com.example.rentaldesk 는 임시값. 실제 배포 전에 고유 bundle identifier를 확정.
```

- `launcher.py`는 상대 import 문제를 피하는 얇은 진입점으로 생성한다. 예: `from rentdesk.app import main; raise SystemExit(main())`.
- CLI는 패키징 경로/리소스, `.spec` 파일의 Qt 플러그인 수집·PySide6 번들·데이터 파일 포함 여부를 확인해야 한다.
- `--onefile`보다 `--onedir`을 초기 기본으로 두어 Qt 동적 라이브러리·플러그인·서명 문제를 조사하기 쉽게 한다.
- Mac ARM과 Intel은 **같은 macOS라도 별도 아키텍처**다. Apple Silicon에서 빌드한 앱을 Intel Mac에서 실행할 수 있다고 가정하지 않는다.
- 가능하면 CI `macos-*` 및 `windows-*` 환경에 코어 테스트 매트릭스를 구성하고 실패 시 배포를 차단한다. CI GUI 테스트는 디스플레이 권한/러너 환경에 좌우되므로 실제 OS의 UI 수동 스모크 테스트를 별도로 유지한다.

## E. OS별 데이터/설정/리소스 경로

Qt 앱 초기화 시 OrganizationName과 ApplicationName을 안정적으로 설정하고 다음처럼 저장 루트를 구한다.

```python
from pathlib import Path
from PySide6.QtCore import QStandardPaths

app_data_dir = Path(
    QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
)
app_data_dir.mkdir(parents=True, exist_ok=True)
db_file = app_data_dir / "data" / "rentdesk.db"
```

이 코드는 유효한 Qt 애플리케이션 초기화 이후 실행하며, 반환된 경로가 비어있거나 쓰기 불가하면 친절한 오류 메시지와 복구 안내를 제공한다.

예상 표준 경로(반환값은 제품의 조직·앱 설정에 따라 달라질 수 있음):

| 데이터 종류 | macOS 예시 | Windows 예시 |
|---|---|---|
| 앱 데이터 | `~/Library/Application Support/RentalDesk/` | `%LOCALAPPDATA%/RentalDesk/` |
| DB | `<app_data>/data/rentdesk.db` | 동일 상대경로 |
| 로그 | `<app_data>/logs/` | 동일 상대경로 |
| 라이선스 | `<app_data>/license/` | 동일 상대경로 |
| 백업 | 사용자가 선택한 경로/기본 백업 폴더 | 동일 정책 |

- Mac `.app/Contents/Resources`, Windows exe 옆 경로는 **읽기 전용 번들 리소스**만 담는다.
- `.app` 안에 DB를 만들거나 `/Applications`/`Program Files`에 DB를 쓰지 않는다.
- CSV·엑셀 업로드/내보내기는 Qt 파일 다이얼로그와 `Path`를 사용한다. 한국어 경로/공백/이모지·긴 경로에 대한 유닛/GUI 테스트를 포함한다.
- macOS 샌드박스/App Store 배포를 MVP 목표로 가정하지 않는다. 사용자 폴더 접근 권한 오류는 사용자가 파일을 직접 선택하게 하고 처리 실패 이유를 표시한다.
- SQLite 파일을 iCloud Drive, OneDrive, NAS 공유 폴더에서 열거나 다중 앱에서 동시 수정하지 않는다. 플랫폼 간 이동은 **내보낸 백업/복원**으로만 지원한다.

## F. 라이선스/백업 이식성

- DB schema 및 백업 ZIP(manifest JSON + SQLite DB)은 동일 버전 포맷을 유지해 Mac→Windows, Windows→Mac 복원을 지원한다.
- 백업 ZIP 안에는 Mac/Windows의 **절대경로, 장비 인증키, 설치파일, 서명 비밀정보, 앱 로그**를 포함하지 않는다.
- `manifest.json`에 `backup_format_version`, `schema_version`, `created_at_utc`, `sha256`, `product`를 포함하며 OS 이름은 참고 메타데이터일 뿐 포맷 판정 근거로 사용하지 않는다.
- SQLite 백업은 `sqlite3.Connection.backup` 호출을 통해 일관되게 생성하고 복원 시 파일 검증/기존 DB 보호 백업/재기동을 수행한다.
- Ed25519 검증 공개키는 앱에만 넣고, 서명 개인키는 판매자 PC/비밀 저장소에만 둔다.
- 기기 ID 생성은 `DeviceIdProvider` 프로토콜로 분리하고 테스트에서 가짜 ID 공급자를 사용한다. Windows 전용 API나 macOS 전용 명령을 다른 OS에서 호출하지 않는다.
- 동일 구매자의 맥북에서 윈도우로 이전 시 기존 라이선스를 무효화/이전 처리할 운영 정책을 문서화하고, 오프라인 구조에서 완전한 자동 비활성화는 불가능하다는 한계를 고지한다.
- 사용권은 '한 개 기기에서 사용, macOS/Windows 선택 가능'을 기본 가설로 두되 판매 직전 약관 확정. OS 변경에 따른 재발급 절차와 재발급 횟수/검증 조건을 공지한다.

## G. UI 크로스플랫폼 품질

- `QKeySequence.StandardKey.Copy`, `Paste`, `Save`, `Quit` 등 OS별 단축키 매핑 활용. `Ctrl+C` 같은 하드코딩 최소화.
- macOS Retina의 논리 픽셀·Windows 100/150% DPI, Mac 창 상단 메뉴바, 창닫기, 종료 확인, 다이얼로그 포커스, 다크 모드를 직접 테스트.
- 기본 글꼴은 Qt 시스템 폰트 우선 사용. 특정 Windows 글꼴 강제 사용 금지. 한국어 금액·날짜·검색/정렬이 두 OS에서 동일한지 검증.
- Windows 1366×768에서 주요 버튼이 보이는지, Mac 13인치 노트북의 기본 화면 크기에서 스크롤 가능한지 확인.
- OS별 파일 권한 실패, 경로 없음, DB 잠금, USB 드라이브로 백업, 다른 OS 복원 오류를 사용자 친화적인 메시지로 표시.
- Finder/Explorer에 맞는 앱 아이콘(`.icns` / `.ico`)을 별도 제공하되 동일한 브랜드 이미지 유지.

## H. macOS 앱 코드서명·공증과 Windows 설치

### macOS 배포용 필수 단계

1. 맥에서 PyInstaller로 `RentalDesk.app` 생성.
2. 앱 번들 내 실행파일/라이브러리·Qt 플러그인이 배포용 서명에 문제없는지 확인.
3. Apple Developer Program의 **Developer ID Application** 인증서로 앱에 **Hardened Runtime** 및 필요한 entitlements 설정 후 코드 서명.
4. 배포용 `.dmg`를 구성하고 Apple 공증 서비스에 `xcrun notarytool` 등 공식 도구로 제출.
5. 공증 승인 확인, 필요 시 ticket stapling, `codesign --verify`, `spctl --assess` 및 Gatekeeper 다운로드/첫 실행 테스트.
6. 별도 사용자 계정 또는 별도 Mac에서 Python을 설치하지 않은 상태로 실행하고 백업/복원까지 검증.

Apple 서명/공증은 **개발 시 매번 필요하지 않지만 외부 판매용 배포를 위해 반드시 계획**한다. 인증서, Apple 계정 비밀정보는 Git에 추가 금지. 애플리케이션 보안 경고를 억지로 우회시키도록 고객에게 권유하는 것을 기본 설치 방법으로 삼지 않는다.

### Windows 배포용 필수 단계

1. Windows에서 PyInstaller `onedir` 출력 생성, 필요한 Qt DLL 및 플러그인 확인.
2. Inno Setup 등 설치도구로 `RentalDesk_x.y.z_win-x64_setup.exe` 생성.
3. 가능하면 Authenticode 코드 서명 수행; 미서명 배포 시 SmartScreen 경고 및 사용자 신뢰·유지보수 이슈를 평가.
4. Python 없는 Windows 11 x64 환경에서 설치·첫 실행·DB 경로·예약/반납/정산·복원·업데이트·제거 확인.
5. 제거 프로그램은 기본적으로 고객 데이터베이스를 자동 삭제하지 않으며, 완전 삭제가 필요할 때에는 별도 안내와 명시적 동의를 받는다.

### 릴리스 명명 규칙

```text
RentalDesk_1.0.0_macos-arm64.dmg
RentalDesk_1.0.0_win-x64_setup.exe
# 추가 검증한 경우만:
RentalDesk_1.0.0_macos-x64.dmg
```

## I. 필수 테스트 및 릴리스 체크리스트

- [ ] Python/Qt/PyInstaller 버전이 macOS 및 Windows 양쪽에서 호환됨.
- [ ] 코드베이스 하나, DB 스키마/백업 포맷 하나, OS별 빌드 스크립트 두 개.
- [ ] Mac Python 개발환경에서 T-01~T-25 통과.
- [ ] Windows 네이티브 테스트 환경에서 T-01~T-25 통과.
- [ ] Mac 및 Windows 각각 Python 없이 설치하여 핵심 사용자 시나리오 1회 이상 완료.
- [ ] Mac↔Windows 백업 복원 2방향 검사(건수/원장 합계/시간대/한글).
- [ ] macOS의 코드서명/공증/Gatekeeper 기본 설치 성공.
- [ ] Windows 설치/제거/재설치 중 로컬 DB 보존.
- [ ] 맥 사용권 파일을 Windows에 무작정 복사해 활성화된다고 가정하지 않음: 재발급/이전 UX 검증.
- [ ] OS별 DPI, 한글 폰트, 다크모드, 단축키, 파일 접근 오류 확인.
- [ ] 지원하지 않는 운영체제/아키텍처는 크몽 판매 문구에 명확히 표시.

## J. Claude에게 요청할 구현 순서

1. macOS 개발환경/의존성 및 Windows 네이티브 빌드 계획 수립.
2. OS 독립 도메인/예약/정산 규칙과 SQLite schema 구현.
3. 경로·리소스·기기 ID 관련 OS 어댑터 구현.
4. Mac에서 PySide6 UI와 핵심 업무 시나리오 구현.
5. Windows CI/VM의 단위·통합 테스트 및 네이티브 PyInstaller 빌드 구성.
6. 맥북에서 macOS `.app` 구현 및 수동 GUI/권한 검증.
7. Windows 설치 EXE와 Mac 서명·공증된 DMG 릴리스 패키지 마련.
8. CP-01~CP-10과 PRD Definition of Done을 플랫폼별로 증거와 함께 확인.

## K. 신뢰 가능한 공식 문서

- PyInstaller — 운영체제별 패키징: https://pyinstaller.org/en/stable/usage.html
- PyInstaller — macOS 아키텍처 설명: https://pyinstaller.org/en/stable/feature-notes.html
- Qt — QStandardPaths: https://doc.qt.io/qt-6/qstandardpaths.html
- Apple Developer — 앱 배포: https://developer.apple.com/macos/distribution/
- Apple Developer — Developer ID: https://developer.apple.com/developer-id/
- Apple Developer — 공증: https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution
