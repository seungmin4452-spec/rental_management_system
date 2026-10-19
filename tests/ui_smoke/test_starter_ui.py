"""시작 도우미 창(M4.6 R7): 다중 선택·미리보기·적용·건너뛰기, 첫 실행에만 자동으로 뜸."""

from rentdesk.context import AppContext
from rentdesk.ui.dialogs.starter_dialog import StarterDialog, category_hint
from rentdesk.ui.main_window import MainWindow


def make_dialog(ctx, *, first_run=False):
    return StarterDialog(ctx, ctx.starter.kinds(), first_run=first_run)


def test_dialog_lists_kinds_with_recommendations(ctx):
    dlg = make_dialog(ctx)
    assert list(dlg.checks) == ["camera", "tools", "camping", "event", "baby"]
    assert dlg.checks["camping"].text() == "캠핑"
    assert not dlg.apply_btn.isEnabled() and "고른 종류가 없습니다" in dlg.preview.text()
    assert dlg.skip_btn.text() == "닫기"
    tent = next(c for k in ctx.starter.kinds() for c in k.categories if c.name == "텐트·타프")
    assert category_hint(tent) == "텐트·타프(준비 24시간, 날짜 기준 · 주말)"


def test_multi_select_preview_and_apply(ctx, svc, ui):
    svc.assets.create_category("렌즈")
    dlg = make_dialog(ctx)
    dlg.checks["camera"].setChecked(True)
    dlg.checks["baby"].setChecked(True)
    assert dlg.selected_keys() == ["camera", "baby"]
    assert "카테고리 10개를 추가합니다" in dlg.preview.text() and "렌즈" in dlg.preview.text()
    assert dlg.apply_btn.isEnabled()
    result = dlg.apply()
    assert result is not None and len(result.added) == 10 and result.skipped == ("렌즈",)
    names = {c.name for c in ctx.assets.list_categories()}
    assert {"카메라 바디", "유모차", "카시트", "기타"} <= names and "전동공구" not in names
    assert any("추가했습니다" in t for t in ui.texts("info"))
    assert ctx.starter.was_offered()


def test_all_existing_disables_apply(ctx):
    ctx.starter.apply(["baby"])
    dlg = make_dialog(ctx)
    dlg.checks["baby"].setChecked(True)
    assert not dlg.apply_btn.isEnabled() and "모두 이미 있습니다" in dlg.preview.text()


def test_first_run_skip_marks_offered(ctx):
    dlg = make_dialog(ctx, first_run=True)
    assert dlg.skip_btn.text() == "건너뛰기"
    dlg.skip_btn.click()
    assert ctx.starter.was_offered()
    assert {c.name for c in ctx.assets.list_categories()} == {"기타"}


def test_menu_close_does_not_mark_offered(ctx):
    make_dialog(ctx).reject()
    assert not ctx.starter.was_offered()


def test_main_window_offers_on_empty_real_db_only_once(ctx, opened_starters):
    window = MainWindow(ctx)
    assert window.offer_starter() is True
    assert len(opened_starters) == 1 and opened_starters[0].first_run
    opened_starters[0].reject()  # 사용자가 건너뜀
    assert window.offer_starter() is False and len(opened_starters) == 1
    window.close()


def test_main_window_does_not_offer_when_data_exists(ctx, svc, opened_starters):
    svc.add_asset("CAM-1")
    window = MainWindow(ctx)
    assert window.offer_starter() is False and not opened_starters
    window.close()


def test_main_window_does_not_offer_in_demo(conn, clock, ui, opened_starters):
    window = MainWindow(AppContext(conn, clock, is_demo=True))
    assert window.offer_starter() is False and not opened_starters
    window.close()


def test_help_menu_opens_starter_any_time(ctx, svc, opened_starters):
    svc.add_asset("CAM-1")
    window = MainWindow(ctx)
    assert window.starter_action.text().startswith("시작 도우미")
    window.starter_action.trigger()
    assert len(opened_starters) == 1 and not opened_starters[0].first_run
    window.close()


def test_open_starter_refreshes_after_apply(ctx, monkeypatch):
    window = MainWindow(ctx)

    def fake_exec(self):
        self.checks["event"].setChecked(True)
        self.apply()
        return 1

    monkeypatch.setattr(StarterDialog, "exec", fake_exec)
    assert window.open_starter() is True
    window.goto("assets")
    combo = window.page_by_key["assets"].category
    assert combo.findText("스피커") >= 0
    window.close()
