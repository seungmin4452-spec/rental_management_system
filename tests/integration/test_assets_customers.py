"""AssetService / CustomerService (PRD S-02, S-03, T-01)."""

from datetime import timedelta

import pytest

from rentdesk.domain.errors import DuplicateError, InvalidStateError, ValidationError
from rentdesk.domain.models import CustomerType, LifecycleStatus
from rentdesk.services.assets import AssetInput
from rentdesk.services.customers import CustomerInput
from rentdesk.services.reservations import ReservationInput
from tests.support import kst


def _input(**overrides):
    base = dict(name="미러리스 카메라 A", category_name="카메라", daily_rate_won=30_000,
                deposit_won=100_000, asset_code="CAM-0001")
    base.update(overrides)
    return AssetInput(**base)


class TestAssets:
    def test_t01_duplicate_asset_code_rejected_existing_kept(self, svc):
        first = svc.assets.create_asset(_input())
        with pytest.raises(DuplicateError):
            svc.assets.create_asset(_input(name="다른 장비", daily_rate_won=1))
        with pytest.raises(DuplicateError):
            svc.assets.create_asset(_input(asset_code="  cam-0001 "))
        kept = svc.assets.search_assets()
        assert [a.id for a in kept] == [first.id]
        assert kept[0].name == "미러리스 카메라 A"
        assert kept[0].daily_rate_won == 30_000

    def test_us01_same_model_two_physical_assets(self, svc):
        a = svc.assets.create_asset(_input())
        b = svc.assets.create_asset(_input(asset_code="CAM-0002"))
        assert a.id != b.id
        assert a.category_id == b.category_id

    def test_code_and_text_fields_are_trimmed(self, svc):
        asset = svc.assets.create_asset(_input(asset_code="  CAM-9 ", name=" 렌즈 ", brand="  "))
        assert asset.asset_code == "CAM-9"
        assert asset.name == "렌즈"
        assert asset.brand is None

    def test_auto_code_generated(self, svc):
        a = svc.assets.create_asset(_input(asset_code=None))
        b = svc.assets.create_asset(_input(asset_code=""))
        assert (a.asset_code, b.asset_code) == ("AST-000001", "AST-000002")

    @pytest.mark.parametrize(
        ("field", "overrides"),
        [
            ("name", {"name": "   "}),
            ("name", {"name": "가" * 101}),
            ("category", {"category_name": ""}),
            ("category", {"category_name": "가" * 51}),
            ("asset_code", {"asset_code": "A" * 31}),
            ("daily_rate_won", {"daily_rate_won": -1}),
            ("daily_rate_won", {"daily_rate_won": 1000.0}),
            ("deposit_won", {"deposit_won": -100}),
        ],
    )
    def test_validation(self, svc, field, overrides):
        with pytest.raises(ValidationError) as exc:
            svc.assets.create_asset(_input(**overrides))
        assert exc.value.field == field
        assert svc.assets.search_assets(include_retired=True) == []

    def test_update_keeps_code_and_rejects_code_change(self, svc):
        asset = svc.assets.create_asset(_input())
        updated = svc.assets.update_asset(
            asset.id, _input(asset_code=None, name="수정됨", daily_rate_won=35_000, category_name="렌즈")
        )
        assert updated.asset_code == "CAM-0001"
        assert updated.name == "수정됨"
        assert updated.category_name == "렌즈"
        with pytest.raises(ValidationError):
            svc.assets.update_asset(asset.id, _input(asset_code="CAM-0009"))

    def test_search(self, svc):
        svc.add_asset("CAM-0001", name="소니 카메라")
        svc.add_asset("LEN-0001", name="렌즈 50mm", category="렌즈")
        assert [a.asset_code for a in svc.assets.search_assets("렌즈")] == ["LEN-0001"]
        assert [a.asset_code for a in svc.assets.search_assets("cam")] == ["CAM-0001"]
        assert svc.assets.search_assets("%") == []  # LIKE 와일드카드 이스케이프

    def test_retire_blocked_by_confirmed_reservation(self, svc):
        asset = svc.add_asset("CAM-0001")
        customer = svc.add_customer()
        order = svc.reservations.create_confirmed(
            ReservationInput(customer.id, kst(10, 20, 10), kst(10, 21, 10), [asset.id])
        )
        with pytest.raises(InvalidStateError) as exc:
            svc.assets.retire_asset(asset.id)
        assert order.order_no in exc.value.message
        svc.reservations.cancel(order.id)
        assert svc.assets.retire_asset(asset.id).lifecycle_status == LifecycleStatus.RETIRED
        assert svc.assets.search_assets() == []
        assert len(svc.assets.search_assets(include_retired=True)) == 1

    def test_retire_blocked_while_out(self, svc):
        asset = svc.add_asset("CAM-0001")
        customer = svc.add_customer()
        order = svc.reservations.create_confirmed(
            ReservationInput(customer.id, kst(10, 20, 10), kst(10, 21, 10), [asset.id])
        )
        svc.force_checkout(order.id, kst(10, 20, 10))
        with pytest.raises(InvalidStateError):
            svc.assets.retire_asset(asset.id)

    def test_delete_only_without_history(self, svc):
        unused = svc.add_asset("CAM-0001")
        used = svc.add_asset("CAM-0002")
        customer = svc.add_customer()
        svc.reservations.create_draft(
            ReservationInput(customer.id, kst(10, 20, 10), kst(10, 21, 10), [used.id])
        )
        svc.assets.delete_asset(unused.id)
        with pytest.raises(InvalidStateError):
            svc.assets.delete_asset(used.id)
        assert [a.asset_code for a in svc.assets.search_assets()] == ["CAM-0002"]

    def test_archived_category_cannot_receive_new_assets(self, svc):
        asset = svc.add_asset("CAM-0001")
        svc.assets.set_category_archived(asset.category_id, True)
        with pytest.raises(ValidationError):
            svc.add_asset("CAM-0002")

    def test_audit_does_not_store_customer_contact(self, svc):
        svc.customers.create_customer(CustomerInput("홍길동", phone="010-1234-5678", email="a@b.c"))
        descriptions = [r[0] or "" for r in svc.conn.execute("SELECT description FROM audit_events")]
        assert not any("010-1234-5678" in d or "a@b.c" in d or "홍길동" in d for d in descriptions)


class TestCustomers:
    def test_create_and_duplicate_phone_allowed(self, svc):
        a = svc.customers.create_customer(CustomerInput(" 김고객 ", phone="010-0000-0000"))
        b = svc.customers.create_customer(
            CustomerInput("(주)렌탈", customer_type=CustomerType.BUSINESS, phone="010-0000-0000")
        )
        assert a.name == "김고객"
        assert b.customer_type == CustomerType.BUSINESS

    @pytest.mark.parametrize(
        ("field", "data"),
        [
            ("name", CustomerInput("   ")),
            ("name", CustomerInput("가" * 101)),
            ("phone", CustomerInput("김", phone="0" * 31)),
            ("customer_type", CustomerInput("김", customer_type="VIP")),
        ],
    )
    def test_validation(self, svc, field, data):
        with pytest.raises(ValidationError) as exc:
            svc.customers.create_customer(data)
        assert exc.value.field == field

    def test_search_by_name_or_phone(self, svc):
        svc.customers.create_customer(CustomerInput("김고객", phone="010-1111-2222"))
        svc.customers.create_customer(CustomerInput("이고객"))
        assert [c.name for c in svc.customers.search_customers("1111")] == ["김고객"]
        assert len(svc.customers.search_customers("고객")) == 2

    def test_archive_hides_and_blocks_new_reservation(self, svc):
        customer = svc.add_customer()
        asset = svc.add_asset("CAM-0001")
        svc.customers.set_archived(customer.id, True)
        assert svc.customers.search_customers() == []
        assert len(svc.customers.search_customers(include_archived=True)) == 1
        with pytest.raises(ValidationError) as exc:
            svc.reservations.create_draft(
                ReservationInput(customer.id, kst(10, 20, 10), kst(10, 21, 10), [asset.id])
            )
        assert exc.value.field == "customer_id"

    def test_delete_only_without_orders(self, svc):
        lonely = svc.add_customer("이력없음")
        regular = svc.add_customer("단골")
        asset = svc.add_asset("CAM-0001")
        svc.reservations.create_draft(
            ReservationInput(regular.id, kst(10, 20, 10), kst(10, 20, 10) + timedelta(hours=2),
                             [asset.id])
        )
        svc.customers.delete_customer(lonely.id)
        with pytest.raises(InvalidStateError):
            svc.customers.delete_customer(regular.id)
