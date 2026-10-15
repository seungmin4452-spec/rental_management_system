# 작업 인수인계 문서 (HANDOFF)

> 이 문서는 마일스톤이 끝날 때마다 **덮어써서 최신 상태만** 남긴다. 과거 마일스톤의 상세 내역은 git 커밋 로그와 `OPEN_QUESTIONS.md`에서 확인한다.
> 다른 터미널(다른 맥, 새 세션 등)에서 이어서 작업하려면 아래 "환경 재현" 섹션만 그대로 따라 하면 된다.

## 현재 상태: M2 완료(macOS 로컬 기준), M3 착수 전

- 원격 저장소: `https://github.com/seungmin4452-spec/rental_management_system.git` (브랜치 `main`)
- M2는 커밋·푸시 완료. Windows 검증(CI·실기기)은 전체 개발 완료 후 일괄 진행하기로 결정 — 그 전까지 Windows 검증 완료로 보지 않는다.

## 환경 재현 (다른 터미널에서 시작하는 방법)

```bash
git clone https://github.com/seungmin4452-spec/rental_management_system.git
cd rental_management_system

python3.12 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

python -m pip install --upgrade pip
python -m pip install -e '.[dev]'   # M2에서 새 의존성 없음

QT_QPA_PLATFORM=offscreen python -m pytest -q   # 현재 243 passed
```

## M2에서 완료한 것 (파일 기준)

- `domain/`
  - `settlement.py` (신규, 순수 함수) — PRD 7.4 공식(`SettlementSummary`: 청구 합계·순수금·순충당·`amount_due`·`deposit_held`·`is_settled`), `summarize()`, 미리보기용 `apply_money()`, `validate_money_entry()`(양의 정수, 환급 ≤ 순수금, 보증금 반환 ≤ 보유잔액, 충당 ≤ min(보유잔액, 미수), 충당 취소 ≤ 순충당, 역거래 대응 종류·원거래 잔액 검증).
  - `pricing.py` — `rental_charge_won()` 추가(스냅샷 일 대여료 × 약정 대여일수).
  - `models.py` — `ChargeLine`, `MoneyEntry` 데이터형, `RentalOrder.checkout_note`.
- `persistence/`
  - `migrations/002_checkout_note.sql` — `rental_orders.checkout_note` 추가.
  - `repositories.py` — 출고/반납 상태 갱신, 다른 주문의 `OUT` 품목 조회, 청구·금전 원장 insert/list, 역거래 누적, 주문별 원장 합계.
- `services/`
  - `checkout.py` CheckoutService — `preview()`(받을 대여료·보증금 + 출고 불가 사유), `checkout()`: CONFIRMED만, `시작 ≤ 출고 시각 < 약정 반납`·`≤ 현재`, 다른 주문에서 미반납/정비 중 장비면 거부, 품목 전체 OUT, 품목별 `RENTAL` + 주문 `DISCOUNT`(음수) 1회, 선택적 수금/보증금 수령을 같은 트랜잭션에서 기록, 출고 메모.
  - `returns.py` ReturnService — `returnable_items()`, `preview_late_fee()`(원장 미기록, 계산 근거 문자열), `return_items()`: 부분/다건 반납, 재반납 거부, 반납 시각 검증, `LATE`(품목 스냅샷 기준)·`DAMAGE` 청구, `MAINTENANCE_REQUIRED` 시 정비 블록 생성, 영향 예약 경고(정비 블록 + 반납 시각+버퍼 내 후속 예약), 마지막 품목 반납 시 `COMPLETED`.
  - `settlements.py` SettlementService — `get_settlement()`/`summary()`/`preview()`/`record()`/`unsettled_orders()`, `record_in_txn()`(출고에서 재사용). 출고 이후 주문만 금전 거래 허용.
  - `reservations.py` — `_quote_items` → `quote_items`로 공개(출고에서 재사용).
- `tests/support.py` — `force_checkout`/`force_return`(SQL 직접 조작) 삭제, 실제 서비스 호출 `checkout()`/`return_asset()`로 교체. 기존 사용처 모두 변경.
- 결정 사항은 `OPEN_QUESTIONS.md`의 "M2" 표에 기록.

### DB 마이그레이션

- `002_checkout_note.sql` 신규 (schema version 2). 기존 v1 DB는 앱 시작 시 `pre_migration_*.db` 백업 후 자동 적용.

### 실행한 테스트 명령 및 결과

| 명령 | 환경 | 결과 |
|---|---|---|
| `QT_QPA_PLATFORM=offscreen python -m pytest -q` | macOS 26.6.2 (arm64, 로컬 맥북, Python 3.12.14, SQLite 3.53.4) | `243 passed` |
| 동시성 테스트(`-k concurrent`, 확정 + 출고) 5회 반복 | 동일 | 5/5 통과 |
| Windows CI / 실기기 | — | **미확인** (전체 개발 완료 후 일괄 검증 예정) |

PRD 테스트 ID ↔ 테스트 위치 (M2 신규/변경분):

| ID | 테스트 |
|---|---|
| T-07 | `tests/integration/test_checkout_returns.py::test_t07_*` (반복 클릭, 두 연결 동시 출고) |
| T-08 | `test_checkout_returns.py::test_t08_*` |
| T-09 | `test_checkout_returns.py::test_t09_*` |
| T-10 / T-11 | `test_checkout_returns.py::test_us05_t10_*`, `test_us05_t11_*` (+ 기존 `tests/unit/test_pricing.py::TestLateFee`) |
| T-12 | `test_checkout_returns.py::test_t12_*` (2개) |
| T-15 | `tests/integration/test_settlements.py::test_t15_*`, `tests/unit/test_settlement.py::TestT15DepositFlow` |
| T-16 | `test_settlements.py::test_t16_*`, `test_settlement.py::TestValidation::test_t16_*` |
| T-17 | `test_settlements.py::test_t17_*`, `test_settlement.py::TestValidation::test_t17_*` |
| T-18 | `tests/integration/test_reservations.py::test_t18_*` (이제 실제 CheckoutService로 출고) |
| T-23 | `tests/unit/test_pricing.py::test_t23_*`, `test_settlement.py::test_rental_charge_uses_rounded_up_days` |
| T-24 | `test_settlements.py::test_t24_*` (출고 3지점, 반납 4지점, 금전 원장 1지점 예외 주입 → 6개 테이블 완전 동일 확인) |
| US-03/05/06 | `test_checkout_returns.py::test_us03_*`, `test_us05_*`, `test_us06_*` |

### 미완료/주의사항

- UI 미연결: 출고/반납/정산 화면(S-05~S-07)은 M3. 서비스는 화면용 `preview()` 계열 API를 제공한다.
- 조기 반납 대여료 환급, 부분 출고, 과거 거래 정정 UI는 P1(구현하지 않음).
- `ADJUSTMENT` 청구 종류는 스키마에만 있고 P0에서 생성하지 않는다.
- 단일 인스턴스 실행 잠금(PRD 8.1) 여전히 미구현 → M3/M4.
- 실제 GUI 창(`python -m rentdesk`)은 이번에도 직접 띄워 보지 않음.
- Windows 실기기 검증은 여전히 없음(M4/M5).

## 다음 마일스톤 (M3) 작업

TASKS.md M3 기준:

1. 대시보드 S-01 — 오늘 출고/반납, 연체(`AvailabilityService.overdue_items`, `overdue_followup_risks`), 미정산(`SettlementService.unsettled_orders`).
2. 장비/고객 화면 S-02~S-03, 예약 등록/상세/목록 S-04.
3. 출고/부분 반납/정산 S-05~S-07 — `CheckoutService.preview/checkout`, `ReturnService.preview_late_fee/return_items`, `SettlementService.preview/record` 연결. 영향 예약 경고 대화상자.
4. 정비/설정/보고서 S-08~S-09, 5개 표준 엑셀 보고서(openpyxl).
5. 화면에 SQL·요금 계산 로직을 넣지 않는다(서비스만 호출). 단일 인스턴스 잠금 구현 검토.
