# RentDesk Lite 개발 진행 보고서 (M0 ~ M4)

> 기준일: 2026-10-15 · 기준 커밋: M4 `104d6ae` · 작성 환경: macOS 26.6.2 arm64(로컬 맥북)
> 이 문서는 지금까지의 개발 내용을 한 곳에 모은 요약본이다. 다음 작업을 위한 최신 상태와 환경 재현 방법은 `docs/HANDOFF.md`, 결정 근거는 `OPEN_QUESTIONS.md`, 체크리스트는 `TASKS.md`를 기준으로 한다.

## 1. 한눈에 보기

| 항목 | 상태 |
|---|---|
| 제품 | 오프라인 설치형 장비 렌탈 관리 프로그램(macOS Apple Silicon + Windows 11 x64), 크몽 반복 판매용 |
| 진행 단계 | M0~M3 완료, **M4 코드·macOS 로컬 빌드 완료**(배포 서명·Windows 빌드·실기기 검증 남음), M5 미착수 |
| 자동 테스트 | **387 passed** (macOS arm64 로컬). 테스트 파일 26개 |
| 소스 규모 | `src/` Python 약 10,100줄 |
| macOS 산출물 | `RentDeskLite_0.1.0_macos-arm64.dmg`(41MB) 로컬 빌드 성공 — **ad-hoc 서명, 공증 전, 판매 불가** |
| Windows 산출물 | 빌드 스크립트·CI 잡만 작성. **Windows에서 한 번도 빌드하지 않음** |
| 공식 지원 표기 | 아직 불가(CLAUDE.md: 양 플랫폼 출시 게이트 통과 전 "공식 지원" 주장 금지) |

## 2. 기술 구성

- Python 3.12 · PySide6 6.7.3(Qt Widgets) · SQLite(내장, 3.53.4에서 확인) · openpyxl 3.1.5 · PyNaCl 1.5.0 · tzdata · PyInstaller 6.10.0 · Inno Setup 6(Windows)
- 계층: `domain`(순수 함수: 가용성·요금·정산·표시 형식) → `persistence`(스키마·마이그레이션·트랜잭션·리포지토리) → `services`(검증·트랜잭션·정책) → `ui`(화면, SQL·계산 없음)
- OS 의존 코드는 `platform/`(경로, 기기 ID, 환경, 단일 인스턴스 잠금, 기기별 설정)에만 둔다.
- 시간: DB는 UTC `YYYY-MM-DDTHH:MM:SSZ`, 화면은 Asia/Seoul, 예약 구간 `[start, end)`. 금액: KRW 정수.
- 사용자 데이터: macOS `~/Library/Application Support/RentDeskLite/`, Windows `%LOCALAPPDATA%\RentDeskLite\` (`data/`, `backups/`, `logs/`, `license/`, `demo/`, `preferences.json`)

## 3. 마일스톤별 진행 내용

### M0 — 개발 환경·규칙 (`3a77749`, `32d4c71`)
- 프로젝트 골격, `pyproject.toml` 의존성 버전 고정, pytest 설정.
- OS 어댑터: `QStandardPaths` 경로, 기기 ID(`DeviceIdProvider`: macOS IOPlatformUUID / Windows MachineGuid 해시, 테스트용 가짜), 환경 정보, 번들 리소스 경로.
- GitHub Actions CI(`macos-14`, `windows-latest`)에서 테스트 실행 — M0 당시 양쪽 통과 확인.

### M1 — 데이터·예약 코어 (`99e832c`)
- SQLite 스키마(`001_initial.sql`), FK 강제, `BEGIN IMMEDIATE` 트랜잭션 헬퍼, 마이그레이션 전 보호 백업.
- 장비(관리번호 대소문자 무시 고유)·카테고리·고객 CRUD, 이력 있는 항목 삭제 차단.
- 가용성 엔진: 시간 겹침, 전환 버퍼, 정비 블록, 연체 미반납 차단. DRAFT는 미점유, CONFIRMED부터 점유, 저장 직전 트랜잭션 안에서 재검사.
- 예약 임시저장/확정/수정/취소, 확정 시 단가·정책 스냅샷.
- 원장(`charge_lines`, `money_entries`)은 DB 트리거로 수정·삭제 금지.

### M2 — 출고·반납·정산 (`3d6bbc7`)
- 마이그레이션 `002_checkout_note.sql`(출고 메모).
- 일 대여료(24시간 올림, 최소 1일)·할인·연체료(유예 후 24시간 단위 올림 × 배수) `Decimal` 계산.
- 원자적 출고(중복 청구 방지), 장비 단위 부분 반납, 중복 반납 차단, 반납 시 정비 블록과 후속 예약 영향 경고.
- 금전 원장: 수금/환급/보증금 수령·반환·충당·충당 취소, 초과 환급·초과 반환 차단, 미정산 판정.

### M3 — 화면·엑셀 (`42ab99e`)
- 화면 S-01~S-09: 대시보드, 장비, 고객, 예약(3단계 마법사·상세·목록), 출고/반납, 정산, 정비, 보고서, 설정. 전역 검색, 표준 단축키.
- 엑셀 보고서 5종(장비/예약/오늘 출고·반납/연체/정산). 서울 시각 서식, `#,##0"원"`, "공식 증빙 아님" 안내, 임시 파일→교체 저장.
- 예제 데이터 모드(실제 DB와 분리된 파일), 단일 인스턴스 잠금, 오류 로그(WARNING 이상, 순환).

### M4 — 백업·라이선스·패키징 (`104d6ae`)
- **백업/복원**: Online Backup API 스냅샷 → ZIP(`manifest.json` + `rentdesk.db`). 복원 전 7단계 검증(ZIP 구조·manifest·크기·SHA-256·무결성·스키마·건수/합계 대조), 현재 DB 보호 백업, 교체 실패 시 원복, 복원 후 화면 재시작. 백업에 절대경로·라이선스·로그 미포함. 7일 백업 권장 안내.
- **라이선스**: Ed25519 서명 파일, 20자 기기 코드(원본 식별자 역산 불가). 판매자 발급 스크립트 `scripts/generate_license.py`(개인키는 저장소 밖 `~/RentDeskSellerKeys/`). 라이선스 실패 시 예제 모드·실제 데이터 백업만 허용, 실데이터 무변경.
- **무결성**: DB를 열 때마다 `integrity_check`, 손상 시 마이그레이션하지 않고 백업 복원 안내(손상 파일 보관).
- **경로**: `RentDeskLite/RentDeskLite` 중복 해소, macOS 앱 데이터 폴더 권한 700.
- **패키징**: PyInstaller spec(onedir), macOS 서명·DMG·공증 스크립트, Windows Inno Setup·PowerShell 빌드/스모크 스크립트, `--self-check` 자기 점검 모드, CI 패키징 잡.
- **문서**: `docs/install_macos.md`, `docs/install_windows.md`, `docs/license_operations.md`.

DB 마이그레이션 누계: `001_initial`(M1), `002_checkout_note`(M2). M3·M4는 스키마 변경 없음(현재 버전 2).

## 4. 필수 테스트 현황

### T-01 ~ T-25 (PRD 13장)

모두 자동 테스트가 있으며 **macOS arm64 로컬에서 통과**. Windows 결과는 미확인(일괄 검증 예정).

| ID | 내용 | 테스트 위치 |
|---|---|---|
| T-01 | 장비번호 중복 등록 거부 | `integration/test_assets_customers.py`, `ui_smoke/test_ui_flows.py` |
| T-02~T-06 | 중복 확정 거부, 끝 제외 규칙, 버퍼, 실물 단위, DRAFT 미점유 | `integration/test_reservations.py`, `unit/test_availability.py` |
| T-07~T-09, T-12 | 재출고 중복 청구 0, 부분/전체 반납, 재반납 차단 | `integration/test_checkout_returns.py`, `ui_smoke/test_ui_flows.py` |
| T-10, T-11 | 유예 경계 연체료 | `unit/test_pricing.py` |
| T-13, T-14, T-18 | 연체 미반납·정비 중 차단, 출고 후 취소 거부 | `integration/test_reservations.py` |
| T-15~T-17 | 보증금 충당/반환, 초과 반환·환급 거부 | `unit/test_settlement.py`, `integration/test_settlements.py` |
| T-19, T-20 | 실행 중 백업→복원 일치, 손상 백업 12종 거부·원본 보존 | `integration/test_backup.py` |
| T-21 | 모든 연결 FK 활성화 | `integration/test_db.py` |
| T-22 | 라이선스 변조·다른 기기 거부 | `unit/test_licensing.py` |
| T-23 | 25시간 → 2일 요금 | `unit/test_pricing.py`, `unit/test_settlement.py` |
| T-24 | 저장 중 실패 시 전체 롤백 | `integration/test_settlements.py` |
| T-25 | 강제 종료 후 재실행 무결성·커밋 데이터 유지 | `integration/test_crash_recovery.py` |

### CP-01 ~ CP-10 (크로스플랫폼)

| ID | 상태 |
|---|---|
| CP-01 Mac 개발환경 T-01~T-25 | **통과**(macOS 26.6.2 arm64 로컬) |
| CP-02 Windows 테스트 환경 T-01~T-25 | 미확인 — M0 시점 CI 통과 이후 M1~M4 결과는 확인하지 않음 |
| CP-03 Python 없는 Mac에서 `.dmg` 설치·업무 | 미검증(로컬 빌드·자기 점검만 통과) |
| CP-04 Python 없는 Windows 11에서 `.exe` 설치·업무 | 미검증(Windows 빌드 미실행) |
| CP-05/06 Mac↔Windows 백업 왕복 | 미검증(포맷 OS 독립성은 자동 테스트로 확인) |
| CP-07 업데이트·삭제·재설치 시 데이터 보존 | 미검증(설계: 데이터 폴더를 설치 경로와 분리, 제거 시 보존) |
| CP-08 다른 OS에서 라이선스 변조/이전 | 자동 테스트로 로직 확인(Mac 라이선스를 Windows 기기에 복사 → 무효, 재발급 → 유효). 실기기 미검증 |
| CP-09 Retina / Windows 100·150% / 키보드 | 미검증(오프스크린 렌더링만 확인) |
| CP-10 Gatekeeper·서명·공증 / Windows 설치·제거 | 미검증(스크립트만 작성) |

## 5. 남은 작업

### M4 잔여
- Developer ID 서명·Hardened Runtime·공증 실행과 Gatekeeper 확인(인증서 필요).
- Windows 네이티브 빌드(`scripts/build_windows.ps1` 또는 CI `package-windows`)와 Python 없는 Windows 11 PC 설치·제거·재설치.
- Mac↔Windows 백업 왕복 복원, Windows 기기 코드로 라이선스 재발급.

### M3에서 이월된 수동 점검
- 실제 화면 사용성(에러/빈 상태/필터/검색), Retina·다크 모드·단축키·파일 대화상자.

### M5 (출시)
- 장비 500대 / 고객 3,000명 / 예약 10,000건 규모 테스트(시작 시 무결성 검사 시간 포함).
- Windows 1366×768, macOS Retina 주요 화면 확인.
- 실제 렌탈업체 담당자 2~3명 피드백.
- 사용 설명서·빠른 시작·오픈소스 라이선스 고지(PySide6/Qt LGPL 검토)·개인정보 안내.
- OS별 지원 매트릭스 기록, `setup.exe`와 서명·공증된 `.dmg` 동시 릴리스.

## 6. 사용자 결정이 필요한 사항

| 항목 | 영향 |
|---|---|
| Apple Developer 계정·Developer ID Application 인증서 | 없으면 macOS 판매용 서명·공증 불가 |
| 정식 번들 ID(현재 임시 `com.example.rentdesklite`) | 출시 후 변경 어려움 |
| 라이선스 이전·재발급 약관(횟수, 동시 사용) | `docs/license_operations.md`에 가설로만 기재 |
| 고객 목록 엑셀 출력 포함 여부 | 개인정보 최소화 원칙으로 P0에서 제외해 둠 |

결정 완료: 앱 아이콘 제작·적용(파란 타일 + 달력 + 체크, `scripts/make_icons.py`). **Windows는 코드 서명 없이 출시 시작**(2026-10-15). 설치 안내서(`docs/install_windows.md`)에 브라우저 다운로드 경고, SmartScreen "추가 정보 → 실행", 백신 오탐 대응, SHA-256 확인 절차를 추가했다. 판매 시 설치 파일 SHA-256을 함께 안내하고 판매 페이지에 보안 경고 가능성을 명시한다.

## 7. 보안·비밀정보 관리 현황

- 라이선스 서명 개인키: `~/RentDeskSellerKeys/signing_key.json`(권한 600, 저장소 밖). 저장소·설치파일에는 공개키만 있음. **분실 대비 오프라인 사본 필요.**
- 발급 대장: `~/RentDeskSellerKeys/issued_licenses.csv`(개발용 맥북 라이선스 1건 발급).
- 빌드 스모크가 번들 안의 개인키 흔적·DB·인증서 파일을 검사.
- 로그에는 고객 연락처·원장 상세를 기록하지 않음. 라이선스 실패 로그는 상태 코드만 남김.

## 8. 관련 문서

| 문서 | 내용 |
|---|---|
| `PRD.md`, `CROSS_PLATFORM.md`, `CLAUDE.md` | 요구사항·크로스플랫폼 명세·작업 규칙 |
| `TASKS.md` | 마일스톤 체크리스트(실제 통과 항목만 체크) |
| `OPEN_QUESTIONS.md` | 마일스톤별 결정과 이유 |
| `docs/HANDOFF.md` | 최신 상태·환경 재현·다음 단계 |
| `docs/install_macos.md`, `docs/install_windows.md` | OS별 설치 안내(초안) |
| `docs/license_operations.md` | 판매자 라이선스 발급·이전 절차 |
