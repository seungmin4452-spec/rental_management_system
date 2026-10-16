"""화면 배율 자동 계산(창이 작업 영역에 들어가도록 줄이기)."""

from rentdesk.ui import display_scale as ds


def test_auto_keeps_100_when_screen_is_large():
    assert ds.auto_factor(1920, 1040) == 1.0
    assert ds.auto_factor(1536, 824) == 1.0  # FHD 125%


def test_auto_shrinks_for_fhd_150_laptop():
    # 1920×1080 150% → 논리 1280×720, 작업 표시줄 제외 약 672
    f = ds.auto_factor(1280, 672)
    assert f < 1.0
    assert 700 + 45 <= 672 / f  # 줄인 뒤에는 최소 창 높이가 들어간다
    assert round(f * 100) % 5 == 0


def test_auto_shrinks_for_1366x768():
    f = ds.auto_factor(1366, 728)
    assert f == 0.95


def test_auto_has_lower_bound_and_handles_unknown_screen():
    assert ds.auto_factor(400, 300) == ds.MIN_FACTOR
    assert ds.auto_factor(0, 0) == 1.0


def test_manual_choice_overrides_auto():
    assert ds.resolve_factor(125, 1280, 672) == 1.25
    assert ds.resolve_factor(100, 1280, 672) == 1.0
    assert ds.resolve_factor("auto", 1280, 672) == ds.auto_factor(1280, 672)


def test_unknown_saved_values_fall_back_to_auto():
    for bad in (None, "", 37, True, "125", 1.25):
        assert ds.normalize_choice(bad) == ds.AUTO
    assert ds.normalize_choice(90) == 90
