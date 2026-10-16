"""엑셀로 가져오기 화면 (USABILITY_PLAN U10). 파일 대화상자 대신 경로를 직접 넘긴다."""

from __future__ import annotations

from openpyxl import Workbook

from rentdesk.context import AppContext
from rentdesk.services.bulk_import import ImportKind
from rentdesk.ui.dialogs.import_dialog import ImportDialog
from rentdesk.ui.main_window import MainWindow


def _xlsx(path, headers, rows, sheet):
    wb = Workbook()
    ws = wb.active
    ws.title = sheet
    ws.append(headers)
    for r in rows:
        ws.append(list(r))
    wb.save(path)
    return path


def test_asset_import_preview_and_partial_import(conn, clock, svc, ui, tmp_path):
    ctx = AppContext(conn, clock, import_backup_dir=tmp_path / "before_import")
    svc.add_asset("CAM-1", category="카메라")
    path = _xlsx(tmp_path / "장비 목록.xlsx", ["관리번호", "장비명", "카테고리", "일 대여료"], [
        ("CAM-2", "카메라 B", "카메라", 30000),
        ("CAM-1", "중복", "카메라", 30000),
        ("DR-1", "드론", "드론", "만원"),
        ("DR-2", "드론 B", "드론", 40000),
    ], "장비")
    dlg = ImportDialog(ctx, ImportKind.ASSETS)
    assert not dlg.import_btn.isEnabled()  # 파일 선택 전
    dlg.load_file(path)
    assert dlg.table.rowCount() == 4
    assert dlg.table.item(1, 1).text() == "오류" and "이미 등록된" in dlg.table.item(1, 6).text()
    assert "새 카테고리: 드론" in dlg.summary.text()
    assert dlg.import_btn.text() == "오류 없는 2행만 등록"

    dlg.create_categories.setChecked(False)  # 새 카테고리 안 만들기 → 드론 행도 오류
    assert dlg.import_btn.text() == "오류 없는 1행만 등록"
    assert "카테고리가 없습니다" in dlg.table.item(3, 6).text()
    dlg.create_categories.setChecked(True)

    dlg.run_import()
    assert "2행은 등록하지 않습니다" in ui.texts("confirm")[-1]
    done = ui.texts("info")[-1]
    assert "장비 2건을 등록했습니다" in done and "새로 만든 카테고리: 드론" in done
    assert "가져오기 전 백업" in done
    assert dlg.result.created == 2
    assert len(list((tmp_path / "before_import").glob("*.zip"))) == 1
    assert sorted(a.asset_code for a in svc.assets.search_assets()) == ["CAM-1", "CAM-2", "DR-2"]


def test_cancel_confirm_imports_nothing(ctx, svc, ui, tmp_path):
    path = _xlsx(tmp_path / "고객.xlsx", ["이름", "전화번호"], [("홍길동", "010-1234-5678")], "고객")
    dlg = ImportDialog(ctx, ImportKind.CUSTOMERS)
    dlg.load_file(path)
    ui.answer = False
    dlg.run_import()
    assert dlg.result is None and svc.customers.search_customers() == []


def test_bad_file_shows_message(ctx, ui, tmp_path):
    bad = tmp_path / "x.xlsx"
    bad.write_bytes(b"nope")
    dlg = ImportDialog(ctx, ImportKind.CUSTOMERS)
    assert dlg.load_file(bad) is None
    assert "읽지 못했습니다" in ui.texts("warn")[-1]
    assert not dlg.import_btn.isEnabled()


def test_save_template_and_demo_notice(ctx, ui, tmp_path):
    dlg = ImportDialog(ctx, ImportKind.CUSTOMERS)  # import_backup_dir 없음 = 예제 데이터처럼 백업 안 함
    saved = dlg.save_template_to(tmp_path / "고객_가져오기_양식.xlsx")
    assert saved.exists() and "양식 파일을 저장했습니다" in ui.texts("info")[-1]
    assert not dlg.create_categories.isVisibleTo(dlg)


def test_pages_have_import_button(ctx):
    window = MainWindow(ctx)
    pages = [window.pages.widget(i) for i in range(window.pages.count())]
    with_import = {type(p).__name__ for p in pages if hasattr(p, "import_btn")}
    assert with_import == {"AssetsPage", "CustomersPage"}
    assert all(p.import_btn.text() == "엑셀로 가져오기" for p in pages if hasattr(p, "import_btn"))
    window.close()
