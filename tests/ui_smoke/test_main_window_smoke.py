from PySide6.QtWidgets import QApplication

from rentdesk.ui.main_window import MIN_HEIGHT, MIN_WIDTH, MainWindow


def test_main_window_constructs_with_menu_and_min_size():
    app = QApplication.instance() or QApplication([])

    window = MainWindow()

    assert window.windowTitle() == "RentDesk Lite"
    assert window.menu_list.count() == 6
    assert window.minimumWidth() >= MIN_WIDTH
    assert window.minimumHeight() >= MIN_HEIGHT

    window.close()
