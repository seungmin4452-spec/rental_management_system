"""예제 데이터 모드용 시드 (PRD 5 '예제 데이터 모드를 제공하되 실제 DB와 분리', M3).

- 실제 사용자 DB가 아닌 **별도 데모 DB 파일**에만 적재한다(호출자가 보장, `app.py`).
- SQL 직접 조작 없이 실제 서비스(예약→출고→반납→정산)를 순서대로 호출해 만든다.
  업무 규칙(시작 시각, 출고/반납 시각 ≤ 현재)을 지키도록 내부 시계를 과거부터 진행시킨다.
- 실존 인물·업체로 오인되지 않도록 모든 이름에 '예제'를 붙이고 연락처를 넣지 않는다.
- 결과에는 완료·정산 완료, 완료·미정산, 연체, 부분 반납, 오늘 출고, 미래 확정, 임시저장,
  취소, 정비 중 장비, 연체로 인한 후속 예약 위험이 모두 포함된다(S-01~S-09 시연용).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from rentdesk.domain.extra_charges import ExtraChargeReason
from rentdesk.domain.kits import KitDiscountKind
from rentdesk.domain.models import CustomerType, MoneyKind, PaymentMethod, ReturnCondition
from rentdesk.domain.timeutil import SEOUL, to_utc
from rentdesk.services.assets import AssetInput, AssetService
from rentdesk.services.checkout import CheckoutInput, CheckoutService
from rentdesk.services.customers import CustomerInput, CustomerService
from rentdesk.services.kits import KitInput, KitPick, KitService
from rentdesk.services.maintenance import MaintenanceService
from rentdesk.services.reservations import ReservationInput, ReservationService
from rentdesk.services.returns import ReturnItemInput, ReturnService
from rentdesk.services.settings import SettingsService
from rentdesk.services.settlements import MoneyInput, SettlementService
from rentdesk.services.starter import StarterService

SAMPLE_ASSETS = [
    # (code, name, category, brand, model, rate, deposit)
    ("CAM-0001", "미러리스 카메라 A", "카메라", "예제브랜드", "A7", 30_000, 100_000),
    ("CAM-0002", "미러리스 카메라 A", "카메라", "예제브랜드", "A7", 30_000, 100_000),
    ("CAM-0003", "시네마 카메라 B", "카메라", "예제브랜드", "C1", 80_000, 300_000),
    ("LEN-0001", "표준 줌 렌즈 24-70", "렌즈", "예제브랜드", "Z2470", 15_000, 50_000),
    ("LEN-0002", "망원 줌 렌즈 70-200", "렌즈", "예제브랜드", "Z70200", 20_000, 80_000),
    ("LEN-0003", "단렌즈 50mm", "렌즈", "예제브랜드", "P50", 10_000, 30_000),
    ("LGT-0001", "LED 조명 300W", "조명", "예제조명", "L300", 25_000, 70_000),
    ("LGT-0002", "LED 조명 300W", "조명", "예제조명", "L300", 25_000, 70_000),
    ("AUD-0001", "무선 핀마이크 세트", "음향", "예제음향", "W2", 12_000, 40_000),
    ("AUD-0002", "샷건 마이크", "음향", "예제음향", "S1", 10_000, 30_000),
    ("TRP-0001", "비디오 삼각대", "삼각대", "예제지지", "T75", 8_000, 20_000),
    ("TRP-0002", "비디오 삼각대", "삼각대", "예제지지", "T75", 8_000, 20_000),
]

# M4.6 R7: 여러 업종이 섞인 가게. 카테고리는 시작 도우미 데이터(공구·캠핑·행사·유아)로 추가한다.
# 카메라 장비는 위 카테고리(가게 기본 설정)를 그대로 써서 기존 시연 금액이 바뀌지 않게 한다.
SAMPLE_STARTER_KINDS = ("tools", "camping", "event", "baby")
SAMPLE_MULTI_ASSETS = [
    ("TOL-0001", "전동 해머드릴", "전동공구", "예제공구", "HD20", 15_000, 50_000),
    ("TOL-0002", "레이저 레벨기", "측정·측량", "예제공구", "LV3", 12_000, 50_000),
    ("TOL-0003", "발전기 3kW", "발전기·컴프레서", "예제동력", "G3000", 40_000, 150_000),
    ("CMP-0001", "4인용 텐트", "텐트·타프", "예제캠프", "T4", 30_000, 50_000),
    ("CMP-0002", "렉타 타프", "텐트·타프", "예제캠프", "R1", 15_000, 30_000),
    ("CMP-0003", "동계 침낭", "침낭·매트", "예제캠프", "S-10", 8_000, 20_000),
    ("CMP-0004", "동계 침낭", "침낭·매트", "예제캠프", "S-10", 8_000, 20_000),
    ("CMP-0005", "자충 매트", "침낭·매트", "예제캠프", "M5", 5_000, 10_000),
    ("CMP-0006", "자충 매트", "침낭·매트", "예제캠프", "M5", 5_000, 10_000),
    ("EVT-0001", "액티브 스피커 12인치", "스피커", "예제음향", "A12", 40_000, 100_000),
    ("EVT-0002", "액티브 스피커 12인치", "스피커", "예제음향", "A12", 40_000, 100_000),
    ("EVT-0003", "아날로그 믹서 12채널", "믹서", "예제음향", "MX12", 30_000, 80_000),
    ("EVT-0004", "무선 핸드마이크", "마이크", "예제음향", "H1", 10_000, 30_000),
    ("EVT-0005", "무선 핸드마이크", "마이크", "예제음향", "H1", 10_000, 30_000),
    ("BBY-0001", "휴대용 유모차", "유모차", "예제베이비", "S1", 8_000, 50_000),
    ("BBY-0002", "회전형 카시트", "카시트", "예제베이비", "C360", 7_000, 50_000),
]
# (이름, 구성 장비, 구성품 체크리스트, 정액 할인, 비율 할인)
SAMPLE_KITS = [
    ("카메라 기본 세트", ["CAM-0002", "LEN-0001"], ["배터리 2개", "충전기", "메모리 카드"], 5_000, None),
    ("캠핑 2인 세트", ["CMP-0001", "CMP-0002", "CMP-0003", "CMP-0004", "CMP-0005", "CMP-0006"],
     ["텐트 폴·팩", "망치", "수납 가방"], 0, "10"),
    ("행사 음향 세트", ["EVT-0001", "EVT-0002", "EVT-0003", "EVT-0004", "EVT-0005"],
     ["스피커 스탠드 2개", "전원 케이블", "마이크 건전지"], 20_000, None),
]

SAMPLE_CUSTOMERS = [
    ("예제고객 김하늘", CustomerType.INDIVIDUAL),
    ("예제고객 이바다", CustomerType.INDIVIDUAL),
    ("예제고객 박구름", CustomerType.INDIVIDUAL),
    ("예제 영상제작소", CustomerType.BUSINESS),
    ("예제 스튜디오", CustomerType.BUSINESS),
    ("예제고객 최노을", CustomerType.INDIVIDUAL),
]


class _StepClock:
    def __init__(self, now: datetime) -> None:
        self.now = to_utc(now)

    def __call__(self) -> datetime:
        return self.now


def _at_hour(base: datetime, day_offset: int, hour: int, minute: int = 0) -> datetime:
    """`base`의 서울 날짜 기준 상대 일자의 서울 시각."""
    d = base.astimezone(SEOUL).date() + timedelta(days=day_offset)
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=SEOUL)


def load_sample_data(conn: sqlite3.Connection, now: datetime) -> None:
    """빈 데모 DB에 예제 데이터를 적재한다. `now`는 데모의 '현재' 시각."""
    if conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0]:
        raise RuntimeError("예제 데이터는 빈 데모 데이터베이스에만 적재할 수 있습니다.")
    real_now = to_utc(now)
    clock = _StepClock(real_now - timedelta(days=30))
    assets = AssetService(conn, clock)
    customers = CustomerService(conn, clock)
    reservations = ReservationService(conn, clock)
    checkout = CheckoutService(conn, clock)
    returns = ReturnService(conn, clock)
    settlements = SettlementService(conn, clock)
    maintenance = MaintenanceService(conn, clock)
    SettingsService(conn, clock).update_business_info(name="예제 렌탈샵", phone="")

    a = {}
    for code, name, category, brand, model, rate, deposit in SAMPLE_ASSETS:
        a[code] = assets.create_asset(AssetInput(
            name=name, category_name=category, daily_rate_won=rate, deposit_won=deposit,
            asset_code=code, brand=brand, model=model, location="예제 선반",
        )).id
    c = [
        customers.create_customer(CustomerInput(name=name, customer_type=kind)).id
        for name, kind in SAMPLE_CUSTOMERS
    ]

    def book(customer: int, codes: list[str], start: datetime, due: datetime,
             *, confirm: bool = True, discount: int = 0, notes: str | None = None) -> int:
        clock.now = min(to_utc(start) - timedelta(days=1), real_now)
        data = ReservationInput(
            customer_id=customer, start_at=start, due_at=due,
            asset_ids=[a[x] for x in codes], discount_won=discount, notes=notes,
        )
        order = reservations.create_confirmed(data) if confirm else reservations.create_draft(data)
        return order.id

    def go_out(order_id: int, at: datetime, deposit: int = 0, receipt: int = 0) -> None:
        clock.now = to_utc(at)
        checkout.checkout(order_id, CheckoutInput(
            checkout_at=at, note="외관 이상 없음, 구성품 확인", deposit_in_won=deposit,
            rental_receipt_won=receipt,
            payment_method=PaymentMethod.TRANSFER if deposit or receipt else None,
        ))

    def give_back(order_id: int, codes: list[str], at: datetime, **kwargs: object) -> None:
        clock.now = to_utc(at)
        order = reservations.get_order(order_id)
        ids = {i.asset_id: i.id for i in order.items}
        returns.return_items(order_id, [
            ReturnItemInput(item_id=ids[a[x]], returned_at=at, **kwargs) for x in codes
        ])

    def pay(order_id: int, kind: MoneyKind, amount: int, at: datetime,
            method: PaymentMethod | None = PaymentMethod.TRANSFER) -> None:
        clock.now = to_utc(at)
        settlements.record(order_id, MoneyInput(kind=kind, amount_won=amount, payment_method=method))

    # 1) 반납 완료 + 정산 완료
    o1 = book(c[0], ["CAM-0002", "LEN-0001"], _at_hour(real_now, -12, 10), _at_hour(real_now, -10, 10))
    go_out(o1, _at_hour(real_now, -12, 10, 10), deposit=150_000, receipt=90_000)
    give_back(o1, ["CAM-0002", "LEN-0001"], _at_hour(real_now, -10, 9, 40))
    pay(o1, MoneyKind.DEPOSIT_OUT, 150_000, _at_hour(real_now, -10, 9, 50))

    # 2) 반납 완료(연체 1일 발생) + 미정산(보증금 보유 중, 연체료 미수)
    o2 = book(c[3], ["LGT-0001", "LGT-0002", "TRP-0001"], _at_hour(real_now, -6, 9),
              _at_hour(real_now, -4, 18), discount=10_000)
    go_out(o2, _at_hour(real_now, -6, 9, 20), deposit=160_000)
    give_back(o2, ["LGT-0001", "LGT-0002", "TRP-0001"], _at_hour(real_now, -3, 11))
    pay(o2, MoneyKind.RENTAL_RECEIPT, 164_000, _at_hour(real_now, -3, 11, 10))

    # 3) 미래 확정 예약(연체 장비 CAM-0001에 잡힘 → 후속 예약 위험)
    book(c[4], ["CAM-0001"], _at_hour(real_now, 2, 10), _at_hour(real_now, 3, 10))

    # 4) 대여 중 + 연체(어제 반납 예정)
    o4 = book(c[1], ["CAM-0001", "AUD-0001"], _at_hour(real_now, -3, 10), _at_hour(real_now, -1, 10))
    go_out(o4, _at_hour(real_now, -3, 10, 5), deposit=140_000)

    # 5) 대여 중 + 부분 반납(오늘 반납 예정)
    o5 = book(c[2], ["CAM-0003", "LEN-0002", "AUD-0002"], _at_hour(real_now, -2, 9),
              _at_hour(real_now, 0, 23, 30))
    go_out(o5, _at_hour(real_now, -2, 9, 15), deposit=410_000, receipt=200_000)
    give_back(o5, ["AUD-0002"], min(_at_hour(real_now, -1, 17), real_now))

    # 6) 반납 시 정비 필요(렌즈 파손) → 정비 블록
    o6 = book(c[5], ["LEN-0003"], _at_hour(real_now, -5, 13), _at_hour(real_now, -4, 13))
    go_out(o6, _at_hour(real_now, -5, 13, 5), deposit=30_000)
    give_back(o6, ["LEN-0003"], _at_hour(real_now, -4, 12, 30),
              condition=ReturnCondition.MAINTENANCE_REQUIRED, damage_fee_won=20_000,
              extra_charge_memo="앞면 코팅 수리", note="렌즈 앞면 긁힘",
              maintenance_reason="렌즈 앞면 긁힘 점검")

    # 7) 오늘 출고 예정(확정) — 출고 예정 시각은 오늘 23:00(서울)
    clock.now = real_now
    today_start = max(_at_hour(real_now, 0, 23), real_now + timedelta(minutes=10))
    book(c[0], ["TRP-0002", "LEN-0001"], today_start, today_start + timedelta(days=1))

    # 8) 3일 뒤 확정, 9) 임시저장, 10) 취소
    book(c[4], ["CAM-0002", "LGT-0001"], _at_hour(real_now, 3, 9), _at_hour(real_now, 5, 18),
         notes="예제: 촬영 일정 확정")
    book(c[1], ["LGT-0002"], _at_hour(real_now, 4, 10), _at_hour(real_now, 5, 10), confirm=False,
         notes="예제: 고객 확인 대기")
    o10 = book(c[2], ["TRP-0001"], _at_hour(real_now, 6, 10), _at_hour(real_now, 7, 10))
    clock.now = real_now
    reservations.cancel(o10)

    # 11) 수동 정비 블록(종료 예정 있음)
    clock.now = real_now
    maintenance.create_block(
        a["TRP-0001"], real_now, real_now + timedelta(days=1), "예제: 정기 점검"
    )

    _load_multi_industry(
        conn, real_now, clock, a, c, assets, reservations, checkout, returns, settlements
    )


def _next_weekday(base: datetime, weekday: int, min_days: int) -> int:
    """`base`(서울) 기준 `min_days`일 뒤 이후 처음 오는 `weekday`(0=월)까지의 일수."""
    start = base.astimezone(SEOUL).date() + timedelta(days=min_days)
    return min_days + (weekday - start.weekday()) % 7


def _load_multi_industry(conn, real_now, clock, a, c, assets, reservations, checkout, returns,
                         settlements) -> None:
    """여러 업종 장비·카테고리 설정·세트 시연(M4.6 R7)."""
    clock.now = real_now - timedelta(days=30)
    StarterService(conn, clock).apply(SAMPLE_STARTER_KINDS)
    for code, name, category, brand, model, rate, deposit in SAMPLE_MULTI_ASSETS:
        a[code] = assets.create_asset(AssetInput(
            name=name, category_name=category, daily_rate_won=rate, deposit_won=deposit,
            asset_code=code, brand=brand, model=model, location="예제 창고",
        )).id
    kits = KitService(conn, clock)
    kit_ids = {}
    for name, codes, checklist, fixed, percent in SAMPLE_KITS:
        kit_ids[name] = kits.create_kit(KitInput(
            name=name, asset_ids=[a[x] for x in codes], checklist=checklist,
            discount_kind=KitDiscountKind.PERCENT if percent else KitDiscountKind.FIXED,
            discount_won=fixed, discount_percent=percent,
        )).id

    def book_kit(customer: int, kit: str, start: datetime, due: datetime, notes: str | None = None) -> int:
        clock.now = min(to_utc(start) - timedelta(days=1), real_now)
        ids = kits.get_kit(kit_ids[kit]).asset_ids
        return reservations.create_confirmed(ReservationInput(
            customer_id=customer, start_at=start, due_at=due, asset_ids=list(ids),
            kits=[KitPick(kit_id=kit_ids[kit], asset_ids=list(ids))], notes=notes,
        )).id

    # 12) 행사 음향 세트: 반납 완료(마이크 건전지 누락 → 추가 비용) + 정산 마무리
    o12 = book_kit(c[4], "행사 음향 세트", _at_hour(real_now, -8, 10), _at_hour(real_now, -7, 22))
    order = reservations.get_order(o12)
    kit_id = order.kits[0].id
    clock.now = to_utc(_at_hour(real_now, -8, 10, 10))
    checkout.checkout(o12, CheckoutInput(
        checkout_at=clock.now, note="스피커 외관 이상 없음", deposit_in_won=340_000,
        payment_method=PaymentMethod.TRANSFER, kit_checks={kit_id: [True, True, True]},
    ))
    clock.now = to_utc(_at_hour(real_now, -7, 21, 30))
    mic = next(i.id for i in order.items if i.asset_id == a["EVT-0005"])
    returns.return_items(o12, [
        ReturnItemInput(
            item_id=i.id, returned_at=clock.now,
            damage_fee_won=5_000 if i.id == mic else 0,
            extra_charge_reason=ExtraChargeReason.MISSING_PART if i.id == mic else ExtraChargeReason.DAMAGE,
        )
        for i in order.items
    ], kit_checks={kit_id: [True, True, False]})
    clock.now = to_utc(_at_hour(real_now, -7, 21, 40))
    settlements.finalize(o12, settlements.final_plan(o12), payment_method=PaymentMethod.TRANSFER)

    # 13) 공구 대여 중(10일 → 주 단가 1주 + 3일), 내일 반납 예정
    clock.now = min(to_utc(_at_hour(real_now, -10, 9)), real_now)
    o13 = reservations.create_confirmed(ReservationInput(
        customer_id=c[5], start_at=_at_hour(real_now, -9, 9), due_at=_at_hour(real_now, 1, 9),
        asset_ids=[a["TOL-0001"], a["TOL-0002"]], notes="예제: 인테리어 현장",
    )).id
    clock.now = to_utc(_at_hour(real_now, -9, 9, 10))
    checkout.checkout(o13, CheckoutInput(
        checkout_at=clock.now, note="드릴 비트 세트 포함", deposit_in_won=100_000,
        payment_method=PaymentMethod.CARD,
    ))

    # 14) 캠핑 2인 세트 주말 예약(금 14:00 ~ 월 11:00, 날짜 기준 3일보다 주말 요금이 쌈)
    friday = _next_weekday(real_now, 4, 3)
    book_kit(c[0], "캠핑 2인 세트", _at_hour(real_now, friday, 14), _at_hour(real_now, friday + 3, 11),
             notes="예제: 주말 캠핑")

    # 15) 유모차 2주 예약(주 단가 2주)
    clock.now = real_now
    reservations.create_confirmed(ReservationInput(
        customer_id=c[2], start_at=_at_hour(real_now, 2, 10), due_at=_at_hour(real_now, 16, 10),
        asset_ids=[a["BBY-0001"]], notes="예제: 여행 기간 대여",
    ))
    clock.now = real_now
