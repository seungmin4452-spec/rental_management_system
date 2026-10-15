# 작업 인수인계 문서 (HANDOFF)

> 이 문서는 마일스톤이 끝날 때마다 **덮어써서 최신 상태만** 남긴다. 과거 마일스톤의 상세 내역은 git 커밋 로그와 `OPEN_QUESTIONS.md`에서 확인한다.
> 다른 터미널(다른 맥, 새 세션 등)에서 이어서 작업하려면 아래 "환경 재현" 섹션만 그대로 따라 하면 된다.

## 현재 상태: M0 완료, M1 착수 전

- 원격 저장소: `https://github.com/seungmin4452-spec/rental_management_system.git` (브랜치 `main`)
- 최신 커밋: `M0: project skeleton, platform adapters, minimal main window, CI`
- CI: GitHub Actions에서 `macos-14`(arm64) / `windows-latest`(x64) 매트릭스로 `pytest` 실행, **둘 다 success** 확인됨 (run id 38708389649).

## 환경 재현 (다른 터미널에서 시작하는 방법)

```bash
git clone https://github.com/seungmin4452-spec/rental_management_system.git
cd rental_management_system

# Python 3.12 가상환경
python3.12 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

python -m pip install --upgrade pip
python -m pip install -e '.[dev]'

python -m pytest -q            # 현재 6 passed 상태
```

- `git status`로 깨끗한 상태인지 먼저 확인하고 시작할 것.
- GitHub 인증은 macOS 키체인(`osxkeychain` credential helper)에 저장된 `seungmin4452-spec` 계정 토큰을 사용 중. 다른 머신에서는 `gh auth login` 또는 별도 로그인이 필요할 수 있음.

## M0에서 완료한 것 (파일 기준)

- `pyproject.toml` — Python ">=3.12,<3.13", 의존성 고정: `PySide6==6.7.3`, `openpyxl==3.1.5`, `PyNaCl==1.5.0`, dev: `pytest==8.3.3`/`pytest-cov==5.0.0`/`freezegun==1.5.1`, build extra: `pyinstaller==6.10.0`.
- `src/rentdesk/` 패키지 골격: `domain/`, `services/`, `persistence/` (`migrations/` 포함), `platform/`, `ui/` (`pages/`, `dialogs/`), `resources/sample_data/`.
- `src/rentdesk/platform/paths.py` — `QStandardPaths.AppLocalDataLocation` 기반 앱 데이터/DB/로그/라이선스/백업 경로.
- `src/rentdesk/platform/device_id.py` — `DeviceIdProvider` Protocol, macOS(`ioreg`)/Windows(`winreg`) 구현 + 테스트용 `FakeDeviceIdProvider`(SHA-256 해시, 원본 미보관).
- `src/rentdesk/platform/environment.py` — OS/아키텍처 조회, `is_supported_target()`(macOS arm64, Windows x64만 P0 지원).
- `src/rentdesk/platform/resources.py` — PyInstaller 번들/소스 실행 겸용 리소스 경로.
- `src/rentdesk/ui/main_window.py` — 좌측 메뉴 + 우측 스택 위젯 골격(1280×800, 최소 1100×700). **SQL/요금 계산 로직 없음.**
- `src/rentdesk/app.py`, `__main__.py`, `launcher.py` — 앱 진입점(`QApplication` 생성 시 OrganizationName/ApplicationName 고정).
- `.github/workflows/ci.yml` — macOS arm64 + Windows x64 매트릭스, `QT_QPA_PLATFORM=offscreen`으로 헤드리스 pytest.
- `OPEN_QUESTIONS.md` — M0 결정 사항(Windows 빌드 환경, 의존성 버전, 샘플데이터/데모 모드 계획 등) 기록.
- `TASKS.md` — M0 체크박스 전부 `[x]`로 갱신(실제 테스트/CI 통과 확인 후).

### DB 마이그레이션

- 없음. SQLite 스키마는 M1에서 `001_initial.sql`로 처음 작성.

### 실행한 테스트 명령 및 결과

| 명령 | 환경 | 결과 |
|---|---|---|
| `python -m pytest -q` | macOS 26.6.2 (arm64, 로컬 맥북) | `6 passed` |
| GitHub Actions `pytest -q` (`QT_QPA_PLATFORM=offscreen`) | `macos-14` (arm64) | success |
| GitHub Actions `pytest -q` (`QT_QPA_PLATFORM=offscreen`) | `windows-latest` (x64) | success |
| 수동 스모크: `MainWindow` 생성 + `show()` (offscreen) | macOS arm64 로컬 | "OK: main window created" 출력, 정상 |

- 테스트 목록: `tests/unit/test_device_id.py`(2), `tests/cross_platform/test_environment.py`(1), `tests/cross_platform/test_paths.py`(2), `tests/ui_smoke/test_main_window_smoke.py`(1).

### 미완료/주의사항

- `python -m rentdesk`를 실제 GUI 창으로 띄워 직접 본 적은 없음(헤드리스 offscreen 검증만 완료). 로컬 맥북에서 실제 창 띄우기는 사용자가 터미널에서 `python -m rentdesk` 직접 실행해 확인 권장.
- Windows는 CI(가상 러너)에서만 테스트됨. **실기기 설치 검증(CP-04, CP-10 등)은 아직 전혀 하지 않음** — M4/M5 전까지 "Windows 지원 완료"라고 말하지 않는다.
- `services/`, `domain/models.py` 등은 아직 빈 패키지(`__init__.py`만 존재). 실제 로직은 M1부터.
- macOS 서명/공증용 Apple Developer 계정 보유 여부 미확인 — M4 착수 전 사용자 확인 필요.

## 다음 마일스톤 (M1) 작업 — 여기서부터 이어서 하면 됨

TASKS.md M1 항목 기준:

1. `src/rentdesk/persistence/db.py` — SQLite 연결 헬퍼, `PRAGMA foreign_keys=ON`, 트랜잭션 컨텍스트 매니저(`BEGIN IMMEDIATE`).
2. `src/rentdesk/persistence/migrations/001_initial.sql` — PRD 8.1의 전체 테이블(categories/assets/customers/rental_orders/rental_items/maintenance_blocks/charge_lines/money_entries/app_settings/audit_events/schema_migrations), FK/CHECK/UNIQUE/인덱스 포함.
3. `src/rentdesk/domain/models.py` — 주문 상태(`DRAFT/CONFIRMED/IN_RENTAL/COMPLETED/CANCELED`)·품목 상태(`RESERVED/OUT/RETURNED/CANCELED`) Enum/dataclass.
4. `src/rentdesk/domain/availability.py` — `[start, end)` 겹침 순수 함수부터 pytest 선작성 (T-01~T-06, T-13~T-14 목표).
5. `src/rentdesk/domain/pricing.py` — 대여일수/대여료 `Decimal` 계산 순수 함수(T-23 등).
6. `services/assets.py`, `services/customers.py`, `services/reservations.py` — 리포지토리 위에 서비스 계층 구성, DB 트랜잭션 내 재검증 포함.
7. 게이트: **T-01~T-06, T-13~T-14 통과 확인 후** M1 완료 보고.

다른 터미널에서 이어서 하려면: 위 "환경 재현" 명령으로 clone/venv/설치 후 `git log --oneline -5`로 현재 커밋 확인 → TASKS.md M1 체크박스를 보며 1번부터 순서대로 진행.
