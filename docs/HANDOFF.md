# 작업 인수인계 문서 (HANDOFF)

> 이 문서는 마일스톤이 끝날 때마다 **덮어써서 최신 상태만** 남긴다. 과거 마일스톤의 상세 내역은 git 커밋 로그와 `OPEN_QUESTIONS.md`에서 확인한다.
> 다른 터미널(다른 맥, 새 세션 등)에서 이어서 작업하려면 아래 "환경 재현" 섹션만 그대로 따라 하면 된다.

## 현재 상태: M3 완료(macOS 로컬 자동 테스트 기준), M4 착수 전

- 원격 저장소: `https://github.com/seungmin4452-spec/rental_management_system.git` (브랜치 `main`)
- M3는 커밋·푸시 완료. Windows 검증(CI·실기기)은 전체 개발 완료 후 일괄 진행하기로 결정 — 그 전까지 Windows 검증 완료로 보지 않는다.
- TASKS M3 중 **수동 점검 2개 항목은 미체크**(실제 화면 사용성, Retina/Windows DPI·단축키·파일 선택창). 아래 "직접 확인해 주세요" 참고.

## 환경 재현 (다른 터미널에서 시작하는 방법)

```bash
git clone https://github.com/seungmin4452-spec/rental_management_system.git
cd rental_management_system

python3.12 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

python -m pip install --upgrade pip
python -m pip install -e '.[dev]'   # M3에서 새 의존성 없음(openpyxl은 기존 고정)

QT_QPA_PLATFORM=offscreen python -m pytest -q   # 현재 320 passed (Windows에서는 POSIX 권한 테스트 1개 skip 예정)
python -m rentdesk                               # 실제 GUI 실행
```

## M3에서 완료한 것 (파일 기준)

- `domain/`
  - `display.py` (신규) — `format_won`(1,234,000원), `format_dt`(서울 `YYYY-MM-DD HH:mm`), `format_duration`(1일 4시간), `seoul_day_range`, `seoul_today`.
  - `availability.py` — `AssetState`/`current_asset_state()`(대여 중·연체 > 정비 > 은퇴 > 사용 가능), `block_active_at()`.
- `persistence/repositories.py` — 화면/보고서용 조회(`out_item_details`, `schedule_item_rows`, `list_block_rows`, `asset_order_rows`, `search_orders_by_no`, `customer_names`), `list_orders(query=)`(예약번호·고객명).
- `services/`
  - `dashboard.py` (신규) DashboardService.snapshot(기준일) — 대여 중 장비 수, 오늘 출고/반납(대기/완료), 연체(예상 연체료·지연 시간, 원장 미기록), 후속 예약 위험, 미정산(완료 후 미정산 수), 7일 내 확정 예약, 정비 중 장비.
  - `reports.py` (신규) ReportService — 5개 보고서 빌더(`ReportTable`) + `export()`(임시 파일→`os.replace`, 덮어쓰기 확인용 `ReportExistsError`, 권한/폴더 오류 한국어 안내). 시트명·제목 한국어, 서울 시각 날짜 서식, `#,##0"원"`, "공식 증빙 아님" 문구.
  - `search.py` (신규) 전역 검색(장비/고객/예약번호, 연락처 비노출).
  - `sample_data.py` (신규) 예제 데이터 적재 — 실제 서비스 호출로 완료·미정산·연체·부분 반납·정비·임시저장·취소·후속 위험 사례 생성.
  - `reservations.py` — `OrderRow`/`list_order_rows()`, `update_order(confirm=True)`(수정+확정 원자 처리), `preview_quote(order_id=)`(확정 예약은 스냅샷 단가).
  - `availability.py` — `asset_options()`(장비별 충돌 사유), `asset_states()`.
  - `assets.py` — `asset_orders()`(장비 상세 이력). `maintenance.py` — `BlockRow`, `list_blocks()`. `settlements.py` — `SettlementRow`, `list_rows()`. `settings.py` — 사업자 정보 get/update.
- `platform/` — `instance_lock.py`(QLockFile 단일 인스턴스), `paths.get_demo_db_path()`/`get_lock_path()`.
- `context.py` (신규) — 화면에 주입하는 서비스 묶음 `AppContext`.
- `app.py` — 단일 인스턴스 잠금, 오류 로그(`logs/rentdesk.log`, 순환), 전역 예외 → 일반 안내, `AppController`(실제 ↔ 예제 데이터 모드 전환, 데모 DB는 매번 새로 생성·실제 DB 경로 거부).
- `ui/`
  - `main_window.py` — 좌측 메뉴 9개(대시보드/장비/고객/예약/출고·반납/정산/정비/보고서/설정), 상단 전역 검색, 표준 단축키(`QKeySequence.StandardKey` Find/Refresh/New/Quit), 예제 모드 배너.
  - `feedback.py`(메시지·확인 대화상자 단일 창구, 테스트용 핸들러 주입), `widgets.py`(서울 시각 입력, 원 입력, 읽기 전용 표, 필드 옆 오류, 빈 목록 안내 버튼).
  - `pages/` — dashboard, assets, customers, reservations, rentals, settlements, maintenance, reports, settings.
  - `dialogs/` — 장비 등록/상세, 고객, 예약 3단계 마법사, 예약 상세, 출고, 반납, 정산, 정비 블록.
- 결정 사항은 `OPEN_QUESTIONS.md`의 "M3" 표에 기록.

### DB 마이그레이션

- **없음** (schema version 2 유지). 사업자 정보는 기존 `app_settings`의 `business_name`/`business_phone` 키를 사용.

### 실행한 테스트 명령 및 결과

| 명령 | 환경 | 결과 |
|---|---|---|
| `QT_QPA_PLATFORM=offscreen python -m pytest -q` | macOS 26.6.2 (arm64, 로컬 맥북, Python 3.12.14, SQLite 3.53.4) | `320 passed` (3회 반복 모두 통과) |
| `app.main()` 실제 진입점 실행(Qt 테스트 모드 경로, offscreen) | 동일 | 창 생성, DB 생성, 두 번째 인스턴스 거부, 종료 시 잠금 해제 확인 |
| 모든 화면/대화상자 offscreen 렌더링 스크린샷(1280×800, 1100×700) | 동일 | 잘림 없음 확인(오프스크린 렌더러 기준, 실제 Retina 화면 아님) |
| Windows CI / 실기기 | — | **미확인** (전체 개발 완료 후 일괄 검증 예정) |

M3 신규 테스트 위치:

| 범위 | 테스트 |
|---|---|
| 표시 형식·장비 상태 순수 함수 | `tests/unit/test_display.py` |
| S-01 대시보드 집계 | `tests/integration/test_dashboard.py` |
| 화면용 조회 API·검색·설정·수정+확정 원자성 | `tests/integration/test_m3_read_models.py` |
| 5개 엑셀 보고서 내용·서식·덮어쓰기·권한 오류·한글/이모지 경로 | `tests/integration/test_reports.py` |
| 예제 데이터 모드 | `tests/integration/test_sample_data.py` |
| 단일 인스턴스 잠금 | `tests/cross_platform/test_instance_lock.py` |
| 화면 흐름(T-01/02/07/08/09/12/16, US-02/06 화면 경로 포함) | `tests/ui_smoke/test_ui_flows.py`, `test_main_window_smoke.py` |

### 직접 확인해 주세요 (자동화로 대체 불가)

- 맥북에서 `python -m rentdesk` 실행 → 설정 > "예제 데이터 모드 시작"으로 예약→출고→부분 반납→정산→엑셀 저장까지 한 번 진행(PRD 1.4 "15분 이내" 목표).
- Retina 화면에서 글자/표/대화상자 잘림, 다크 모드 가독성, `Cmd+F`(검색)·`Cmd+N`(새 예약)·`Cmd+R`(새로 고침) 동작, Finder 저장 대화상자.

### 미완료/주의사항

- 백업/복원 화면·BackupService는 M4. 설정 화면에 아직 없음.
- 고객 목록 엑셀 출력은 넣지 않음(OPEN_QUESTIONS M3 참고). 필요하면 알려주세요.
- 앱 데이터 경로가 macOS에서 `…/Application Support/RentDeskLite/RentDeskLite/`로 조직명·앱명이 중복됨 → M4 경로 점검 때 확정.
- 다크 모드에서 일부 고정 색상(빨강 경고, 노란 배너) 대비는 수동 확인 필요.
- 조기 반납 환급, 부분 출고, 과거 거래 정정 UI, 캘린더는 P1(구현하지 않음).
- Windows 실기기 검증은 여전히 없음(M4/M5).

## 다음 마일스톤 (M4) 작업

TASKS.md M4 기준:

1. BackupService: SQLite Online Backup API, ZIP(manifest.json + DB, 해시), 복원 전 integrity_check·스키마 호환·해시 검증, 현재 DB 보호 백업, 손상 백업 시 기존 DB 보존(T-19, T-20). 설정 화면에 백업/복원 UI 연결.
2. Ed25519 라이선스(판매자 개인키 분리 `scripts/generate_license.py`), 앱 공개키 검증, 실패 시 데모 모드 안내(T-22).
3. 사용자 데이터 경로·로그 보호 점검(위 경로 중복 이슈 결정), 비정상 종료 후 재실행 무결성 검사(T-25).
4. macOS `.app/.dmg` 빌드 스크립트·서명/공증 단계, Windows PyInstaller + Inno Setup 스크립트(Windows에서 빌드).
