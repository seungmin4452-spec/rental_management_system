from rentdesk.context import AppContext
from rentdesk.services.sample_data import load_sample_data
from rentdesk.ui.main_window import MENU_ITEMS, MIN_HEIGHT, MIN_WIDTH, MainWindow
from tests.support import kst


def test_main_window_constructs_with_menu_and_min_size(ctx):
    window = MainWindow(ctx)

    assert window.windowTitle() == "RentDesk Lite"
    assert window.menu_list.count() == len(MENU_ITEMS) == 9
    assert window.minimumWidth() >= MIN_WIDTH
    assert window.minimumHeight() >= MIN_HEIGHT
    window.close()


def test_every_page_renders_with_demo_data_at_minimum_size(conn, clock, ui, qapp):
    load_sample_data(conn, kst(10, 19, 9))
    window = MainWindow(AppContext(conn, clock, is_demo=True))
    window.resize(MIN_WIDTH, MIN_HEIGHT)
    window.show()
    for row in range(window.menu_list.count()):
        window.menu_list.setCurrentRow(row)
        qapp.processEvents()
        assert window.pages.currentIndex() == row
    assert "예제 데이터 모드" in window.windowTitle()
    assert not ui.texts("warn"), ui.messages  # 어떤 화면도 오류 없이 그려진다
    window.close()


def test_dashboard_cards_reflect_services(conn, clock, ui):
    load_sample_data(conn, kst(10, 19, 9))
    ctx = AppContext(conn, clock)
    window = MainWindow(ctx)
    page = window.page_by_key["dashboard"]
    data = ctx.dashboard.snapshot()
    assert page.cards["overdue"].value.text() == str(len(data.overdue))
    assert page.cards["rented"].value.text() == str(data.rented_asset_count)
    assert page.overdue.rowCount() == len(data.overdue)
    window.close()


def test_global_search_without_results_informs(ctx, ui):
    window = MainWindow(ctx)
    window.search_box.setText("없는장비")
    assert window.run_search() == []
    assert any("없습니다" in t for t in ui.texts("info"))
    window.close()


def test_goto_reservations_with_customer_filter(ctx, svc):
    k = svc.add_customer("필터고객")
    a = svc.add_asset("CAM-1")
    svc.book(k.id, [a.id], kst(10, 20, 10), kst(10, 21, 10))
    svc.book(svc.add_customer("다른고객").id, [a.id], kst(10, 25, 10), kst(10, 26, 10))
    window = MainWindow(ctx)
    window.goto("reservations", query="필터고객")
    page = window.page_by_key["reservations"]
    assert window.pages.currentWidget() is page
    assert page.table.rowCount() == 1
    window.close()
