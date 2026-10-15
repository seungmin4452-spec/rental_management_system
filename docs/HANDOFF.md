# 작업 인수인계 문서 (HANDOFF)

> 이 문서는 마일스톤이 끝날 때마다 **덮어써서 최신 상태만** 남긴다. 과거 마일스톤의 상세 내역은 git 커밋 로그와 `OPEN_QUESTIONS.md`에서 확인한다.
> 다른 터미널(다른 맥, 새 세션 등)에서 이어서 작업하려면 아래 "환경 재현" 섹션만 그대로 따라 하면 된다.

## 현재 상태: M1 완료(macOS 로컬 기준), M2 착수 전

- 원격 저장소: `https://github.com/seungmin4452-spec/rental_management_system.git` (브랜치 `main`)
- M1은 커밋·푸시 완료. Windows 검증(CI·실기기)은 전체 개발 완료 후 일괄 진행하기로 결정 — 그 전까지 Windows 검증 완료로 보지 않는다.

## 환경 재현 (다른 터미널에서 시작하는 방법)

```bash
git clone https://github.com/seungmin4452-spec/rental_management_system.git
cd rental_management_system

python3.12 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

python -m pip install --upgrade pip
python -m pip install -e '.[dev]'   # M1에서 tzdata 의존성이 추가됨 → 기존 venv도 재설치 필요

QT_QPA_PLATFORM=offscreen python -m pytest -q   # 현재 166 passed
```

## M1에서 완료한 것 (파일 기준)

- `pyproject.toml` — `tzdata==2024.2` 추가(Windows의 `ZoneInfo("Asia/Seoul")`용), 마이그레이션 SQL을 package-data로 포함(wheel에 `001_initial.sql` 포함 확인).
- `domain/`
  - `errors.py` — `DomainError`/`ValidationError(field)`/`NotFoundError`/`DuplicateError`/`InvalidStateError`/`ConflictError(conflicts)`, 한국어 메시지.
  - `timeutil.py` — aware datetime 강제, UTC `YYYY-MM-DDTHH:MM:SSZ` 직렬화, 서울 표시 변환.
  - `models.py` — 주문/품목/원장 Enum, 상태 전이 표, dataclass, `RentalPolicy`.
  - `availability.py` — `[start,end)` 겹침, 품목 점유 구간(DRAFT/CANCELED 미점유, RETURNED는 실제 반납+버퍼, OUT 연체는 무기한), 충돌 탐색, 예약 시간 검증.
  - `pricing.py` — 대여일수(24h 올림, 최소 1일), 견적, 할인 검증, 연체료(`Decimal`, ROUND_HALF_UP). 연체료 함수는 M2 ReturnService에서 그대로 사용.
- `persistence/`
  - `db.py` — `connect()`(FK ON, autocommit, busy_timeout), `transaction()`(`BEGIN IMMEDIATE`, 예외 시 롤백), 마이그레이션 실행기(버전별 원자 적용, 기존 DB면 사전 백업, 상위 버전 DB 거부), `open_database()`.
  - `migrations/001_initial.sql` — PRD 8.1 전체 테이블 + CHECK(금액 정수/부호, UTC 형식, 상태값) + 핵심 인덱스 + 트리거(원장 append-only, 주문 삭제 금지, 출고 후 품목 삭제 금지, 장비번호 불변). 기본 설정값(유예 60, 배수 "1", 버퍼 60) 시드.
  - `repositories.py` — 파라미터 바인딩 SQL CRUD만(정책 판단 없음).
- `services/`
  - `assets.py` AssetService — 장비 등록(자동번호 `AST-000001`)/수정(번호 변경 불가)/검색/은퇴(대여 중·확정 예약 있으면 거부)/재활성/이력 없는 장비 삭제, 카테고리.
  - `customers.py` CustomerService — 생성/수정/검색/보관/이력 없는 고객 삭제. 감사 로그에 연락처 미기록.
  - `availability.py` AvailabilityService — 충돌 조회, 기간 내 예약 가능 장비, 연체 품목, 연체로 인한 후속 예약 위험 목록.
  - `reservations.py` ReservationService — 임시저장/바로 확정/확정/수정/취소, 확정 시 스냅샷, 트랜잭션 내 충돌 재검사, 예약번호 `R-YYYYMMDD-0001`(서울 날짜).
  - `maintenance.py` MaintenanceService — 정비 블록 등록(영향받는 확정 예약 경고)/종료. `insert_block_in_txn()`은 M2 반납 처리에서 재사용.
  - `settings.py` SettingsService — 유예/배수/버퍼 조회·변경(검증 포함).
- `app.py` — 시작 시 사용자 데이터 폴더 DB 열기+마이그레이션, 실패 시 한국어 오류창. (UI 화면은 아직 서비스에 연결하지 않음)
- 결정 사항은 `OPEN_QUESTIONS.md`의 "M1" 표에 기록.

### DB 마이그레이션

- `001_initial.sql` 신규 작성 (schema version 1).

### 실행한 테스트 명령 및 결과

| 명령 | 환경 | 결과 |
|---|---|---|
| `QT_QPA_PLATFORM=offscreen python -m pytest -q` | macOS 26 (arm64, 로컬 맥북, Python 3.12.14, SQLite 3.53.4) | `166 passed` |
| 동시 확정 테스트 5회 반복 | 동일 | 5/5 통과 |
| Windows CI | — | **미확인** (push 후 GitHub Actions에서 확인 필요) |

PRD 테스트 ID ↔ 테스트 위치:

| ID | 테스트 |
|---|---|
| T-01 | `tests/integration/test_assets_customers.py::TestAssets::test_t01_*` |
| T-02 | `tests/integration/test_reservations.py::test_t02_*` (2개) |
| T-03 | `test_reservations.py::test_t03_*`, `tests/unit/test_availability.py::TestOverlaps::test_half_open_*` |
| T-04 | `test_reservations.py::test_t04_*` (2개), `test_availability.py::TestFindConflicts::test_t04_*` |
| T-05 | `test_reservations.py::test_t05_*` |
| T-06 | `test_reservations.py::test_t06_*` |
| T-13 | `test_reservations.py::test_t13_*` |
| T-14 | `test_reservations.py::test_t14_*` |
| (선행) T-18, T-21, T-23, T-10/T-11(순수함수) | `test_reservations.py::test_t18_*`, `test_db.py::test_t21_*`, `test_pricing.py::test_t23_*`, `test_pricing.py::TestLateFee` |
| 동시성 | `test_reservations.py::test_concurrent_confirmations_*`, `test_stale_ui_precheck_*` |

### 미완료/주의사항

- **출고/반납은 M2 범위**라 M1 테스트에서는 `tests/support.py`의 `force_checkout`/`force_return`이 SQL로 상태를 직접 만든다. M2에서 CheckoutService/ReturnService가 생기면 해당 테스트를 실제 서비스 호출로 바꿀 것.
- 단일 인스턴스 실행 잠금(PRD 8.1) 미구현 → M3/M4.
- 가용성 조회는 장비별 전체 이력을 읽어 도메인 함수로 판정한다. 500장비/10,000예약 성능 테스트는 M5에서 확인(필요 시 SQL 사전 필터 추가).
- 실제 GUI 창(`python -m rentdesk`)은 이번에도 직접 띄워 보지 않음. 이제 실행하면 `~/Library/Application Support/RentDeskLite/data/rentdesk.db`가 생성된다.
- Windows 실기기 검증은 여전히 없음(M4/M5).

## 다음 마일스톤 (M2) 작업

TASKS.md M2 기준:

1. `services/checkout.py` — CONFIRMED → IN_RENTAL 원자적 전이(시작 시각 이후·약정 반납 전만), 품목 OUT, `RENTAL` 청구(품목별 1회, unique index로 보장) + `DISCOUNT`(음수, 주문당 1회), 선택적 수금/보증금 입력. T-07(재출고 중복 청구 0건).
2. `services/returns.py` — OUT 품목 부분 반납, 실제 반납 시각 검증(출고 이후·미래 불가), `domain.pricing.late_fee`로 `LATE`, 수동 `DAMAGE`, `MAINTENANCE_REQUIRED` 시 `maintenance.insert_block_in_txn` + 영향 예약 경고, 마지막 품목 반납 시 `COMPLETED`. T-08~T-12.
3. `domain/settlement.py`(순수 함수) + `services/settlements.py` — PRD 7.4 공식, 과다 환급/초과 충당/역거래 검증. T-15~T-17.
4. T-24: 출고/반납/원장 저장 중 예외 주입 시 전체 롤백 테스트.
5. `tests/support.py`의 `force_*` 헬퍼 사용처를 실제 서비스로 교체.
