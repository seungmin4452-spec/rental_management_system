"""시작 도우미(M4.6 R7) 도메인: 데이터 파일 검증·적용 계획, 동봉 데이터 파일 검증."""

import json
from decimal import Decimal

import pytest

from rentdesk.domain.errors import ValidationError
from rentdesk.domain.rates import DayCountMethod
from rentdesk.domain.starter import parse_starter_kind, plan_starter, sort_kinds
from rentdesk.platform.resources import get_starter_dir
from rentdesk.services.starter import load_starter_kinds


def kind_data(key="camera", label="카메라·촬영", order=10, categories=None, **extra):
    data = {
        "format": 1, "key": key, "label": label, "order": order,
        "categories": categories if categories is not None else [{"name": "렌즈"}],
    }
    data.update(extra)
    return data


def kind(key, names, order=10):
    return parse_starter_kind(kind_data(key=key, label=key, order=order,
                                        categories=[{"name": n} for n in names]))


# --------------------------------------------------------------------------- parsing


def test_parse_full_category():
    k = parse_starter_kind(kind_data(description="설명", categories=[{
        "name": " 텐트·타프 ", "turnaround_buffer_minutes": 1440, "grace_minutes": 60,
        "late_multiplier": "1.5", "rate_rule": {"day_count": "CALENDAR", "weekend_enabled": True},
    }]))
    c = k.categories[0]
    assert c.name == "텐트·타프"
    assert c.policy.turnaround_buffer_minutes == 1440 and c.policy.grace_minutes == 60
    assert c.policy.late_multiplier == Decimal("1.5")
    assert c.rate_rule.day_count == DayCountMethod.CALENDAR and c.rate_rule.weekend_enabled
    assert c.rate_rule.weekend_multiplier == Decimal("2")  # 빠진 키는 기본값


def test_parse_category_defaults_to_shop_values():
    c = parse_starter_kind(kind_data()).categories[0]
    assert not c.policy.overridden and c.rate_rule is None


@pytest.mark.parametrize("data, message", [
    ([], "파일 형식"),
    (kind_data(format=2), "형식 버전"),
    (kind_data(extra_key=1), "알 수 없는 항목"),
    (kind_data(key="카메라"), "영문"),
    (kind_data(key=""), "비어"),
    (kind_data(label=""), "비어"),
    (kind_data(order="1"), "정렬 순서"),
    (kind_data(categories=[]), "하나 이상"),
    (kind_data(categories=[{"name": f"c{i}"} for i in range(31)]), "30개"),
    (kind_data(categories=[{"name": "렌즈"}, {"name": "렌즈"}]), "두 번"),
    (kind_data(categories=[{"name": "LENS"}, {"name": "lens"}]), "두 번"),
    (kind_data(categories=[{"name": "기타"}]), "기타"),
    (kind_data(categories=[{"name": "x" * 51}]), "50자"),
    (kind_data(categories=[{"name": "렌즈", "color": "red"}]), "알 수 없는 카테고리 항목"),
    (kind_data(categories=["렌즈"]), "카테고리 항목 형식"),
    (kind_data(categories=[{"name": "렌즈", "turnaround_buffer_minutes": -1}]), "준비 시간"),
    (kind_data(categories=[{"name": "렌즈", "turnaround_buffer_minutes": 10081}]), "준비 시간"),
    (kind_data(categories=[{"name": "렌즈", "grace_minutes": True}]), "기다려"),
    (kind_data(categories=[{"name": "렌즈", "grace_minutes": 1.5}]), "기다려"),
    (kind_data(categories=[{"name": "렌즈", "late_multiplier": 1.5}]), "따옴표"),
    (kind_data(categories=[{"name": "렌즈", "late_multiplier": "abc"}]), "숫자가 아닙니다"),
    (kind_data(categories=[{"name": "렌즈", "late_multiplier": "0"}]), "0보다 크고"),
    (kind_data(categories=[{"name": "렌즈", "rate_rule": {"weekly_multiplier": "8"}}]), "요금 규칙"),
    (kind_data(categories=[{"name": "렌즈", "rate_rule": {"day_count": "HOURLY"}}]), "요금 규칙"),
])
def test_parse_rejects_bad_data_with_file_name(data, message):
    with pytest.raises(ValidationError) as exc:
        parse_starter_kind(data, "bad.json")
    assert message in exc.value.message and "bad.json" in exc.value.message


def test_sort_kinds_by_order_and_rejects_duplicate_keys():
    first, second = kind("z_first", ["x"], order=1), kind("a_second", ["y"], order=2)
    assert [k.key for k in sort_kinds([second, first])] == ["z_first", "a_second"]
    with pytest.raises(ValidationError):
        sort_kinds([first, kind("z_first", ["z"])])


# --------------------------------------------------------------------------- planning


def test_plan_multi_select_adds_only_selected_kinds_in_order():
    kinds = [kind("camera", ["렌즈", "조명"], 10), kind("tools", ["전동공구"], 20), kind("baby", ["유모차"], 30)]
    plan = plan_starter(kinds, ["baby", "camera"], existing_names=["기타"])
    assert [p.category.name for p in plan.add] == ["렌즈", "조명", "유모차"]
    assert [p.kind_label for p in plan.add] == ["camera", "camera", "baby"]
    assert plan.skipped == () and not plan.is_empty


def test_plan_skips_existing_names_case_insensitively():
    kinds = [kind("k", ["Lens", "조명"])]
    plan = plan_starter(kinds, ["k"], existing_names=["lens", "기타"])
    assert [p.category.name for p in plan.add] == ["조명"]
    assert plan.skipped == ("Lens",)


def test_plan_same_name_in_two_kinds_uses_first_kind_once():
    kinds = [kind("first", ["조명"], 1), kind("second", ["조명", "믹서"], 2)]
    plan = plan_starter(kinds, ["first", "second"], existing_names=[])
    assert [(p.kind_label, p.category.name) for p in plan.add] == [("first", "조명"), ("second", "믹서")]
    assert plan.skipped == ()
    plan = plan_starter(kinds, ["first", "second"], existing_names=["조명"])
    assert plan.skipped == ("조명",)  # 한 번만 적음


def test_plan_nothing_selected_or_all_existing_is_empty():
    kinds = [kind("k", ["a", "b"])]
    assert plan_starter(kinds, [], existing_names=[]).is_empty
    assert plan_starter(kinds, ["k"], existing_names=["A", "B"]).is_empty


def test_plan_rejects_unknown_kind():
    with pytest.raises(ValidationError, match="알 수 없는"):
        plan_starter([kind("k", ["a"])], ["nope"], existing_names=[])


# --------------------------------------------------------------------------- bundled data


def test_bundled_starter_files_are_valid():
    kinds = load_starter_kinds()
    assert [k.key for k in kinds] == ["camera", "tools", "camping", "event", "baby"]
    assert [k.label for k in kinds] == ["카메라·촬영", "공구·건설", "캠핑", "행사·음향·조명", "유아용품"]
    names = [n.casefold() for k in kinds for n in k.category_names]
    assert len(names) == len(set(names))  # 종류끼리 이름이 겹치지 않는다
    for k in kinds:
        assert k.description and k.categories


def test_bundled_files_are_utf8_json_with_format_1():
    files = sorted(get_starter_dir().glob("*.json"))
    assert len(files) == 5
    for path in files:
        assert json.loads(path.read_text(encoding="utf-8"))["format"] == 1


def test_bundled_recommendations_follow_plan_table():
    kinds = {k.key: {c.name: c for c in k.categories} for k in load_starter_kinds()}
    tent = kinds["camping"]["텐트·타프"]
    assert tent.policy.turnaround_buffer_minutes == 24 * 60
    assert tent.rate_rule.day_count == DayCountMethod.CALENDAR and tent.rate_rule.weekend_enabled
    body = kinds["camera"]["카메라 바디"]
    assert body.rate_rule.day_count == DayCountMethod.CALENDAR and body.rate_rule.weekend_enabled
    drill = kinds["tools"]["전동공구"]
    assert drill.rate_rule.weekly_enabled and drill.rate_rule.monthly_enabled
    assert kinds["tools"]["발전기·컴프레서"].policy.late_multiplier == Decimal("1.5")
    speaker = kinds["event"]["스피커"]
    assert speaker.policy.grace_minutes == 120 and speaker.rate_rule is None  # 24시간 = 가게 기본
    seat = kinds["baby"]["카시트"]
    assert seat.policy.turnaround_buffer_minutes == 24 * 60 and seat.policy.grace_minutes == 24 * 60


def test_load_rejects_broken_file(tmp_path):
    (tmp_path / "a.json").write_text(json.dumps(kind_data()), encoding="utf-8")
    assert [k.key for k in load_starter_kinds(tmp_path)] == ["camera"]
    (tmp_path / "b.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ValidationError, match="b.json"):
        load_starter_kinds(tmp_path)


def test_load_rejects_huge_file(tmp_path):
    (tmp_path / "a.json").write_text(" " * (64 * 1024 + 1), encoding="utf-8")
    with pytest.raises(ValidationError, match="너무 큽니다"):
        load_starter_kinds(tmp_path)
