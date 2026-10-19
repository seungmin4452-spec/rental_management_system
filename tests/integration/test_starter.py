"""시작 도우미(M4.6 R7) 서비스: 다중 선택 적용, 재적용 멱등, 기존 데이터 보호, 첫 실행 판단."""

import json
from decimal import Decimal

import pytest

from rentdesk.domain.errors import ValidationError
from rentdesk.domain.rates import DayCountMethod
from rentdesk.services.starter import OFFERED_KEY, StarterService
from tests.support import kst


@pytest.fixture
def starter(conn, clock):
    return StarterService(conn, clock)


def categories(conn):
    return {r["name"]: r for r in conn.execute("SELECT * FROM categories")}


def test_apply_multi_select_adds_categories_with_settings(svc, starter):
    result = starter.apply(["camping", "tools"])
    cats = {c.name: c for c in svc.assets.list_categories()}
    assert "텐트·타프" in cats and "전동공구" in cats and "카메라 바디" not in cats
    assert result.added[:2] == ("전동공구", "측정·측량")  # 종류 정렬 순서(공구 → 캠핑)
    assert len(result.added) == 5 + 6 and result.skipped == ()
    tent = cats["텐트·타프"]
    assert tent.policy.turnaround_buffer_minutes == 1440 and tent.policy.grace_minutes is None
    assert tent.rate_rule.day_count == DayCountMethod.CALENDAR and tent.rate_rule.weekend_enabled
    gen = cats["발전기·컴프레서"]
    assert gen.policy.late_multiplier == Decimal("1.5")
    assert gen.rate_rule.weekly_enabled and gen.rate_rule.monthly_enabled
    assert cats["기타"].is_system and list(cats)[-1] == "기타"  # 기타는 항상 맨 끝
    assert "추가했습니다" in result.message
    audits = svc.conn.execute(
        "SELECT COUNT(*) FROM audit_events WHERE action = 'STARTER_ADD'").fetchone()[0]
    assert audits == 11


def test_event_categories_keep_shop_rate_rule(svc, starter):
    starter.apply(["event"])
    speaker = next(c for c in svc.assets.list_categories() if c.name == "스피커")
    assert speaker.rate_rule is None and speaker.policy.turnaround_buffer_minutes == 240
    assert speaker.policy.grace_minutes == 120 and speaker.policy.late_multiplier is None


def test_reapply_is_idempotent(conn, starter):
    starter.apply(["camera", "baby"])
    before = [tuple(r) for r in conn.execute("SELECT * FROM categories ORDER BY id")]
    result = starter.apply(["camera", "baby"])
    assert result.added == () and len(result.skipped) == 7 + 4
    assert "새로 추가할 카테고리가 없습니다" in result.message
    assert [tuple(r) for r in conn.execute("SELECT * FROM categories ORDER BY id")] == before


def test_existing_categories_are_never_overwritten(svc, starter):
    lens = svc.assets.create_category("렌즈")
    svc.assets.update_category_policy(lens.id, turnaround_buffer_minutes=30, grace_minutes=None,
                                      late_multiplier=None)
    hidden = svc.assets.create_category("조명")
    svc.assets.set_category_archived(hidden.id, True)
    asset = svc.add_asset("LEN-1", category="렌즈")

    result = StarterService(svc.conn, svc.clock).apply(["camera"])
    assert set(result.skipped) == {"렌즈", "조명"}
    cats = {c.name: c for c in svc.assets.list_categories(include_archived=True)}
    assert cats["렌즈"].policy.turnaround_buffer_minutes == 30 and cats["렌즈"].rate_rule is None
    assert cats["조명"].is_archived  # 숨긴 카테고리는 다시 보이게 하지 않음
    assert svc.assets.get_asset(asset.id).category_id == lens.id
    assert "그대로 두었습니다" in result.message


def test_case_insensitive_existing_name_is_skipped(conn, clock, tmp_path, svc):
    (tmp_path / "x.json").write_text(json.dumps({
        "format": 1, "key": "x", "label": "엑스", "categories": [{"name": "Drone", "turnaround_buffer_minutes": 5}],
    }), encoding="utf-8")
    svc.assets.create_category("drone")
    result = StarterService(conn, clock, data_dir=tmp_path).apply(["x"])
    assert result.added == () and result.skipped == ("Drone",)
    assert categories(conn)["drone"]["turnaround_buffer_minutes"] is None


def test_unknown_kind_writes_nothing(conn, starter):
    before = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
    with pytest.raises(ValidationError):
        starter.apply(["camera", "nope"])
    with pytest.raises(ValidationError):
        starter.apply([1])
    assert conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == before
    assert not starter.was_offered()


def test_failure_mid_apply_rolls_back(conn, starter, monkeypatch):
    from rentdesk.persistence import repositories as repo

    calls = []

    def boom(conn_, category_id, rule):
        calls.append(category_id)
        if len(calls) == 3:
            raise RuntimeError("disk full")
        return original(conn_, category_id, rule)

    original = repo.update_category_rate_rule
    monkeypatch.setattr(repo, "update_category_rate_rule", boom)
    before = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
    with pytest.raises(RuntimeError):
        starter.apply(["camping"])
    assert conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == before
    assert not starter.was_offered()


def test_first_run_offer_lifecycle(svc, starter):
    assert starter.is_empty_database() and starter.should_offer()
    starter.mark_offered()
    assert starter.was_offered() and not starter.should_offer()


def test_apply_with_nothing_selected_counts_as_offered(starter):
    result = starter.apply([])
    assert result.added == () and starter.was_offered() and not starter.should_offer()


def test_not_offered_when_database_has_data(svc, starter):
    svc.assets.create_category("카메라")  # 카테고리만 있으면 아직 빈 DB
    assert starter.should_offer()
    svc.add_customer()
    assert not starter.is_empty_database() and not starter.should_offer()


def test_not_offered_when_only_assets(svc, starter):
    svc.add_asset("CAM-1")
    assert not starter.should_offer()


def test_offered_flag_survives_in_settings(conn, starter):
    starter.mark_offered()
    row = conn.execute("SELECT value_json FROM app_settings WHERE key = ?", (OFFERED_KEY,)).fetchone()
    assert json.loads(row[0]) is True


def test_reservation_uses_starter_category_rules(svc, starter):
    """시작 도우미로 만든 캠핑 카테고리: 날짜 기준 요금 + 준비 24시간이 예약에 스냅샷된다."""
    starter.apply(["camping"])
    tent = svc.add_asset("CMP-1", category="텐트·타프", rate=30_000)
    other = svc.add_asset("CAM-1", category="카메라", rate=30_000)
    k = svc.add_customer()
    # 금 10:00 ~ 토 09:00(23시간): 날짜 기준 1일. 24시간 방식도 1일이지만 토 11:00이면 2일 vs 1일
    order = svc.book(k.id, [tent.id, other.id], kst(10, 23, 10), kst(10, 24, 11))
    items = {i.asset_id: i for i in order.items}
    assert items[tent.id].turnaround_buffer_snapshot_minutes == 1440
    assert items[other.id].turnaround_buffer_snapshot_minutes == 60
    quote = svc.reservations.preview_quote([tent.id, other.id], kst(10, 23, 10), kst(10, 24, 11))
    tent_fee, other_fee = quote.item_fees  # 요청 순서
    assert tent_fee.days == 1 and tent_fee.amount_won == 30_000  # 날짜 기준(1박2일 = 1일)
    assert other_fee.days == 2 and other_fee.amount_won == 60_000  # 가게 기본(24시간 올림)
    assert items[tent.id].rate_rule_snapshot.day_count == DayCountMethod.CALENDAR
