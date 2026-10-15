# 작업 인수인계 문서 (HANDOFF)

> 이 문서는 마일스톤이 끝날 때마다 **덮어써서 최신 상태만** 남긴다. 과거 마일스톤의 상세 내역은 git 커밋 로그와 `OPEN_QUESTIONS.md`에서 확인한다.
> 다른 터미널(다른 맥, 새 세션 등)에서 이어서 작업하려면 아래 "환경 재현" 섹션만 그대로 따라 하면 된다.

## 현재 상태: M4 코드·macOS 로컬 빌드 완료, 배포 서명·Windows 빌드·실기기 검증 남음

- 원격 저장소: `https://github.com/seungmin4452-spec/rental_management_system.git` (브랜치 `main`)
- Windows 검증(CI·실기기)은 전체 개발 완료 후 일괄 진행하기로 결정 — 그 전까지 Windows 검증 완료로 보지 않는다.
- M4 미완료 항목(TASKS 미체크): Developer ID 서명·공증 실행, Windows 네이티브 빌드, Python 미설치 PC 설치 검증, Mac↔Windows 실제 왕복 복원, CP-02~CP-10.

## 환경 재현

```bash
git clone https://github.com/seungmin4452-spec/rental_management_system.git
cd rental_management_system
python3.12 -m venv .venv && source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev,build]'      # build = PyInstaller (M4부터 사용)

QT_QPA_PLATFORM=offscreen python -m pytest -q   # 현재 384 passed (Windows에서는 POSIX 권한 테스트 3개 skip 예정)
python -m rentdesk                               # 실제 GUI 실행 → 라이선스 확인 화면이 먼저 뜸
scripts/build_macos.sh                           # .app + .dmg (ad-hoc 서명, 공증 생략)
```

### 라이선스 (중요)

- 판매자 서명 키: **`~/RentDeskSellerKeys/signing_key.json`** (이 맥북에만 있음, 권한 600, 저장소 밖). 공개키는 `src/rentdesk/license_public_key.py`.
  - **잃어버리면 현재 공개키로 빌드한 앱용 라이선스를 더 발급할 수 없음** → 암호화 USB 등에 오프라인 사본 보관.
  - 다른 맥에서 작업할 때 키를 Git/메신저로 옮기지 말 것. 발급은 이 맥북에서만.
- 이 맥북용 개발 라이선스: `~/RentDeskSellerKeys/dev_macbook_license.json` (기기 코드 `33AY-UARG-2DOD-HVQS-U4CF`).
  `python -m rentdesk` 첫 화면 → "라이선스 파일 불러오기…"로 등록.
- 발급/이전 절차: `docs/license_operations.md`.

## M4에서 완료한 것 (파일 기준)

- `services/backup.py` (신규) — Online Backup API 스냅샷 → ZIP(`manifest.json`+`rentdesk.db`). manifest에 제품/포맷/스키마/앱 버전, UTC 생성 시각, SHA-256, 크기, 참고용 OS, 표별 건수·청구/금전 합계. 복원 전 7단계 검증(`inspect_backup`), 파일 교체+실패 시 원복(`restore_database`), 보호 백업 포함 전체 흐름(`restore_with_protection`), 마지막 백업 시각·7일 권장 안내(`BackupService`).
- `services/licensing.py` (신규) — Ed25519 검증, 기기 코드(20자), 상태별 한국어 사유, `LicenseService.install()`(검증 통과 시에만 원자적 저장).
- `license_public_key.py` (신규), `scripts/generate_license.py` (신규, keygen/pubkey/issue/verify, 저장소 안 키 경로 거부, 발급 대장 CSV).
- `persistence/db.py` — `integrity_problems()`, `DatabaseIntegrityError`; `open_database`가 마이그레이션 전후 `integrity_check`.
- `platform/paths.py` — `configure_app_identity()`(조직명 비움 → 경로 중복 해소), 앱 데이터 폴더 POSIX `700`, `get_preferences_path()`. `platform/preferences.py`(신규, 기기별 기본 백업 폴더).
- `app.py` — `AppPaths`, `AppController`(라이선스 게이트 → 실제/예제 모드, 복원 후 화면 재시작), 시작 시 손상 DB 복구(`recover_corrupt_database`), 패키징 자기 점검 `--self-check <json>`.
- `ui/` — 설정 화면 "데이터 백업·복원"·"라이선스" 그룹(스크롤 영역으로 변경), `ui/backup_actions.py`, `ui/dialogs/license_dialog.py`(LicensePanel/LicenseDialog), 메인 창 백업 권장 배너.
- 패키징 — `packaging/pyinstaller/rentdesk.spec`, `packaging/macos/{sign_app.sh,create_dmg.sh,notarize.sh,entitlements.plist}`, `packaging/windows/rentdesk.iss`, `scripts/{build_macos.sh,smoke_macos.sh,build_windows.ps1,smoke_windows.ps1}`, CI `package-macos`/`package-windows` 잡.
- 문서 — `docs/install_macos.md`, `docs/install_windows.md`, `docs/license_operations.md`. 결정 사항은 `OPEN_QUESTIONS.md` "M4" 표.
- 버전 단일화: `rentdesk.__version__`(0.1.0)을 pyproject·번들·설치파일·manifest가 공유.

### DB 마이그레이션

- **없음** (schema version 2 유지). `app_settings`에 `last_backup_at_utc` 키를 필요 시 upsert(키-값 테이블이라 스키마 변경 없음). 기존 `default_backup_dir` 키는 사용하지 않고 백업 스냅샷에서 null 처리.

### 실행한 테스트 명령 및 결과

| 명령 | 환경 | 결과 |
|---|---|---|
| `QT_QPA_PLATFORM=offscreen python -m pytest -q` | macOS 26.6.2 arm64 로컬 맥북, Python 3.12.14, SQLite 3.53.4, PySide6 6.7.3 | `384 passed` (3회 반복 모두 통과) |
| `SKIP_TESTS=1 scripts/build_macos.sh` | 동일, PyInstaller 6.10.0 | `.app`(103MB) + `RentDeskLite_0.1.0_macos-arm64.dmg`(41MB) 생성, ad-hoc 서명 `codesign --verify --deep --strict` 통과, 번들 `--self-check` ok, `hdiutil verify` 통과. 첫 빌드에서 `_cffi_backend` 누락을 스모크가 잡아 spec 수정 |
| `generate_license.py issue/verify` | 동일 | 이 맥북 기기 코드로 발급 → 앱 내장 공개키 기준 `VALID` |
| Developer ID 서명·공증·Gatekeeper | — | **미실행**(인증서·Apple 계정 없음) |
| Windows CI / 빌드 / 실기기 | — | **미확인** (전체 개발 완료 후 일괄 검증 예정) |

M4 신규 테스트:

| 범위 | 테스트 |
|---|---|
| T-19 실행 중 백업→복원 건수·합계·한글·UTC 시각, manifest·절대경로 미포함, 덮어쓰기·권한 오류, 이전 스키마 백업 마이그레이션, 다른 OS 표기 백업 | `tests/integration/test_backup.py` |
| T-20 손상 백업 12종(ZIP 아님/잘림/해시 불일치/해시 맞는 손상 DB/manifest 누락·변조/제품·포맷·스키마/합계 변조/경로 조작) → 현재 DB 바이트 동일, 교체 후 열기 실패 원복 | `tests/integration/test_backup.py` |
| T-22 서명·내용·기기 코드 변조, 다른 키, 다른 기기, 만료, 형식 오류, 설치 실패 시 기존 유지, Mac 라이선스를 Windows에 복사, 판매자 스크립트 | `tests/unit/test_licensing.py` |
| T-25 쓰기 트랜잭션 중 프로세스 강제 종료 → 재실행 무결성·커밋 데이터만 유지, 손상 DB 감지·원본 보존·백업 복구 | `tests/integration/test_crash_recovery.py` |
| 경로 중복 해소·사용자 전용 권한·로그 수준·기기별 설정 | `tests/cross_platform/test_paths.py` |
| 설정 화면 백업/복원·라이선스 패널, 라이선스 게이트(실데이터 무변경), 복원 후 화면 재시작, 자기 점검 | `tests/ui_smoke/test_m4_ui.py` |

### 직접 확인해 주세요 (자동화로 대체 불가)

- `python -m rentdesk` → 라이선스 화면 → 개발 라이선스 등록 → 설정 > 백업 파일 만들기 → 장비 추가 → 백업에서 복원(추가한 장비가 사라지고 `backups/pre_restore_*.zip` 생성) 흐름.
- `dist/RentDeskLite_0.1.0_macos-arm64.dmg`를 열어 앱을 응용 프로그램에 복사 후 실행(ad-hoc 서명이라 Gatekeeper가 막을 수 있음 — 개발 확인용, 고객 배포 금지).
- 개발 중 생긴 옛 데이터 폴더 `~/Library/Application Support/RentDeskLite/RentDeskLite/`는 더 이상 쓰지 않음(자동 이전 안 함). 필요 없으면 삭제.
- M3에서 남은 수동 점검 2개(실제 화면 사용성, Retina/다크 모드/단축키)도 여전히 미체크.

### 미완료/주의사항

- **결정 필요(사용자)**: Apple Developer 계정·Developer ID 인증서, 정식 번들 ID(현재 `com.example.rentdesklite`), 앱 아이콘 원본, Windows 코드서명 인증서 구매 여부, 라이선스 이전 약관.
- `entitlements.plist`의 `allow-unsigned-executable-memory`는 Developer ID 서명 후 실제 필요 여부 확인 필요.
- Inno Setup 스크립트의 `Korean.isl`이 CI의 Inno Setup 버전에 포함되어 있는지 Windows 빌드 때 확인(없으면 언어 항목 조정).
- Windows: `build_windows.ps1`/`smoke_windows.ps1`/`.iss`는 Windows에서 한 번도 실행하지 않았음.
- 고객 목록 엑셀(M3 보류)·조기 반납 환급·부분 출고·캘린더·자동/암호화 백업은 P1(구현하지 않음).

## 다음 단계

1. (Windows 일괄 검증 시) CI `package-windows` 실행 → setup.exe로 Windows 11 x64 Python 미설치 PC 설치/실행/제거/재설치, `smoke_windows.ps1`, CP-02/04/07/09/10.
2. Mac → Windows / Windows → Mac 백업 왕복 복원(CP-05/06), Windows 기기 코드로 재발급(CP-08).
3. Developer ID 확보 후 `MACOS_SIGN_IDENTITY=... NOTARY_PROFILE=... scripts/build_macos.sh` → 깨끗한 사용자 계정에서 DMG 설치(CP-03/10).
4. M5: 500장비/3000고객/10000예약 규모 테스트(시작 시 integrity_check 시간 포함), 1366×768 화면, 사용 설명서·오픈소스 고지(PySide6 LGPL), 릴리스.
