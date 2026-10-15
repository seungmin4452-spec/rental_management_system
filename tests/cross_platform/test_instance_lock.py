"""PRD 8.1 단일 인스턴스 실행 잠금 (QLockFile, macOS/Windows 공통)."""

from PySide6.QtCore import QSysInfo

from rentdesk.platform.instance_lock import InstanceLock


def test_second_instance_is_rejected_until_release(tmp_path):
    path = tmp_path / "한글 폴더" / "rentdesk.lock"
    path.parent.mkdir()
    first, second = InstanceLock(path), InstanceLock(path)

    assert first.acquire()
    assert not second.acquire()
    first.release()
    assert second.acquire()
    second.release()


def test_stale_lock_file_from_crash_is_recovered(tmp_path):
    path = tmp_path / "rentdesk.lock"
    # 같은 PC에서 이미 종료된 PID가 남긴 잠금 파일(비정상 종료 흉내).
    # QLockFile 형식: pid\nappname\nhostname\n — 다른 PC 이름이면 Qt는 회수하지 않는다(안전 측).
    host = QSysInfo.machineHostName()
    path.write_text(f"999999\nRentDeskLite\n{host}\n", encoding="utf-8")
    lock = InstanceLock(path)
    assert lock.acquire()
    lock.release()
