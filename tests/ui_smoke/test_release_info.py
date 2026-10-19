"""판매 1.0.0 정보: 버전 표시, 오픈소스 고지 (KMONG_LAUNCH_PLAN K1·K6·K8)."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from PySide6.QtWidgets import QLabel, QPushButton

from rentdesk import __version__
from rentdesk.platform.resources import get_third_party_notices_path
from rentdesk.ui.dialogs.about_dialog import AboutDialog, NoticesDialog, load_notices

ROOT = Path(__file__).resolve().parents[2]


def test_release_version():
    assert __version__ == "1.0.0"


def test_about_shows_version_and_notices_button(qapp):
    dlg = AboutDialog()
    assert __version__ in [w.text() for w in dlg.findChildren(QLabel)]
    assert [b for b in dlg.findChildren(QPushButton) if b.text() == "오픈소스 라이선스"]


def test_notices_cover_every_pinned_runtime_dependency():
    """의존성 버전을 바꾸고 `scripts/make_notices.py`를 다시 돌리지 않으면 실패한다."""
    text = get_third_party_notices_path().read_text(encoding="utf-8")
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    labels = {"PySide6": "Qt for Python", "openpyxl": "openpyxl", "PyNaCl": "PyNaCl", "tzdata": "tzdata"}
    for dep in deps:
        name, version = re.match(r"^([A-Za-z0-9_.-]+)==([\w.]+)", dep).groups()
        assert re.search(rf"^- {re.escape(labels[name])}.* {re.escape(version)} — ", text, re.M), dep
    for required in ("GNU LESSER GENERAL PUBLIC LICENSE", "GNU GENERAL PUBLIC LICENSE",
                     "Lucide", "Frank Denis", "PYTHON SOFTWARE FOUNDATION LICENSE", "역공학"):
        assert required in text, required


def test_notices_bundled_in_installer_spec():
    spec = (ROOT / "packaging" / "pyinstaller" / "rentdesk.spec").read_text(encoding="utf-8")
    assert "THIRD_PARTY_NOTICES.txt" in spec


def test_notices_dialog_and_missing_file(qapp, tmp_path):
    dlg = NoticesDialog()
    assert "LGPL" in dlg.text.toPlainText() and dlg.text.isReadOnly()
    assert "찾을 수 없습니다" in load_notices(tmp_path / "없음.txt")
