"""라이선스 사용 조건 순수 함수 (KMONG_LAUNCH_PLAN K3·K4·K5)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from rentdesk import __version__
from rentdesk.domain.license_terms import (
    add_months,
    days_left,
    expiry_end_utc,
    expiry_for_months,
    expiry_notice,
    is_version_covered,
    parse_app_version,
    parse_series,
    series_of,
)


def test_app_version_is_release_format():
    assert parse_app_version(__version__) >= (1, 0, 0)


@pytest.mark.parametrize("version", ["1.0.0", "1.0.3", "1.0.99", "0.9.0"])
def test_patch_versions_covered_by_series(version):
    assert is_version_covered(version, "1.0")


@pytest.mark.parametrize("version", ["1.1.0", "1.10.0", "2.0.0"])
def test_feature_versions_need_update(version):
    assert not is_version_covered(version, "1.0")


def test_later_series_covers_older_versions():
    assert is_version_covered("1.0.2", "1.1")
    assert is_version_covered("1.1.4", "1.1")
    assert not is_version_covered("1.2.0", "1.1")
    assert is_version_covered("1.9.0", "1.10")  # 숫자 비교(문자열 비교 아님)


@pytest.mark.parametrize("bad", ["1.0", "1", "v1.0.0", "1.0.0-beta", "01.0.0", "", "1.0.0.0", None])
def test_bad_app_version_rejected(bad):
    with pytest.raises(ValueError):
        parse_app_version(bad)


@pytest.mark.parametrize("bad", ["1", "1.0.0", "x.y", "1.", "", " ", "01.0", None, 1.0])
def test_bad_series_rejected(bad):
    with pytest.raises(ValueError):
        parse_series(bad)


def test_series_of():
    assert series_of("1.0.3") == "1.0"
    assert series_of("2.11.0") == "2.11"


@pytest.mark.parametrize(("start", "months", "expected"), [
    (date(2026, 10, 19), 1, date(2026, 11, 19)),
    (date(2026, 12, 15), 1, date(2027, 1, 15)),
    (date(2027, 1, 31), 1, date(2027, 2, 28)),
    (date(2028, 1, 31), 1, date(2028, 2, 29)),  # 윤년
    (date(2026, 3, 31), 1, date(2026, 4, 30)),
    (date(2026, 10, 19), 12, date(2027, 10, 19)),
    (date(2026, 11, 30), 3, date(2027, 2, 28)),
])
def test_add_months(start, months, expected):
    assert add_months(start, months) == expected


def test_add_months_rejects_zero():
    with pytest.raises(ValueError):
        add_months(date(2026, 1, 1), 0)


def test_expiry_end_is_seoul_end_of_day():
    assert expiry_end_utc(date(2026, 11, 19)) == datetime(2026, 11, 19, 14, 59, 59, tzinfo=UTC)


def test_one_month_uses_seoul_issue_date():
    # 서울 10월 20일 오전 1시(UTC 19일 16시)에 발급 → 서울 11월 20일까지
    issued = datetime(2026, 10, 19, 16, 0, tzinfo=UTC)
    assert expiry_for_months(issued, 1) == datetime(2026, 11, 20, 14, 59, 59, tzinfo=UTC)


EXPIRES = expiry_end_utc(date(2026, 11, 19))


@pytest.mark.parametrize(("now", "left"), [
    (datetime(2026, 11, 19, 14, 0, tzinfo=UTC), 0),   # 당일 서울 23시
    (datetime(2026, 11, 18, 15, 0, tzinfo=UTC), 0),   # 당일 서울 0시
    (datetime(2026, 11, 18, 14, 59, tzinfo=UTC), 1),  # 전날 서울 23:59
    (datetime(2026, 11, 12, 3, 0, tzinfo=UTC), 7),
    (datetime(2026, 11, 20, 3, 0, tzinfo=UTC), -1),
])
def test_days_left_uses_seoul_dates(now, left):
    assert days_left(EXPIRES, now) == left


def test_expiry_notice_window():
    assert expiry_notice(None, datetime(2026, 11, 18, tzinfo=UTC)) is None
    assert expiry_notice(EXPIRES, datetime(2026, 11, 11, 3, tzinfo=UTC)) is None  # 8일 전
    assert expiry_notice(EXPIRES, datetime(2026, 11, 12, 3, tzinfo=UTC)) == "사용 기간이 7일 남았습니다"
    assert expiry_notice(EXPIRES, datetime(2026, 11, 18, 3, tzinfo=UTC)) == "사용 기간이 1일 남았습니다"
    assert expiry_notice(EXPIRES, datetime(2026, 11, 19, 3, tzinfo=UTC)) == "사용 기간이 오늘 끝납니다"
    assert expiry_notice(EXPIRES, EXPIRES) is None  # 이미 만료 → 시작 화면에서 막힘
