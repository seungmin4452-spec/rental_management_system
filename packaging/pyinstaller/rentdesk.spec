# -*- mode: python ; coding: utf-8 -*-
"""RentDesk Lite PyInstaller 설정 (onedir). 각 OS에서 네이티브로 실행한다 — 크로스컴파일 불가.

  macOS:   python -m PyInstaller --noconfirm --clean packaging/pyinstaller/rentdesk.spec
  Windows: 동일 명령 (Windows PowerShell)

환경변수
  RENTDESK_BUNDLE_ID  macOS 번들 ID (기본 com.example.rentdesklite — 출시 전 확정 필요)
  RENTDESK_ICON       .icns(macOS) / .ico(Windows) 경로. 기본은 packaging/{macos,windows}/RentDeskLite.*
비밀정보(서명 인증서·공증 자격증명·라이선스 개인키)는 이 파일·번들에 넣지 않는다.
"""

import os
import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent.parent  # noqa: F821 - PyInstaller 제공
sys.path.insert(0, str(ROOT / "src"))
from rentdesk import __version__  # noqa: E402

APP_NAME = "RentDeskLite"
BUNDLE_ID = os.environ.get("RENTDESK_BUNDLE_ID", "com.example.rentdesklite")
_DEFAULT_ICON = (ROOT / "packaging" / "macos" / "RentDeskLite.icns" if sys.platform == "darwin"
                 else ROOT / "packaging" / "windows" / "RentDeskLite.ico")
ICON = os.environ.get("RENTDESK_ICON") or str(_DEFAULT_ICON)

# 마이그레이션 SQL은 importlib.resources로 읽으므로 패키지 경로 그대로 넣는다.
datas = [
    (str(ROOT / "src" / "rentdesk" / "persistence" / "migrations" / "*.sql"),
     "rentdesk/persistence/migrations"),
    (str(ROOT / "src" / "rentdesk" / "resources" / "icons" / "*.png"), "rentdesk/resources/icons"),
]

a = Analysis(  # noqa: F821
    [str(ROOT / "launcher.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=datas,
    # PyNaCl은 cffi C 확장(_cffi_backend)을 동적으로 불러와 자동 분석에서 빠진다(스모크 테스트로 확인).
    hiddenimports=["rentdesk.persistence.migrations", "_cffi_backend"],
    hookspath=[],
    runtime_hooks=[],
    # 쓰지 않는 무거운 Qt 모듈 제외(번들 크기·서명 대상 축소)
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
              "PySide6.Qt3DCore", "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtMultimedia",
              "pytest", "freezegun"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    strip=False,
    upx=False,           # UPX는 서명·백신 오탐 문제를 일으키므로 쓰지 않는다
    console=False,       # windowed
    argv_emulation=False,
    target_arch=None,    # 실행 중인 Python의 아키텍처(macOS arm64 / Windows x64)
    codesign_identity=None,   # 서명은 build_macos.sh에서 별도 단계로 수행
    entitlements_file=None,
    icon=ICON,
)
coll = COLLECT(  # noqa: F821
    exe, a.binaries, a.datas,
    strip=False, upx=False,
    name=APP_NAME,
)

if sys.platform == "darwin":
    app = BUNDLE(  # noqa: F821
        coll,
        name=f"{APP_NAME}.app",
        icon=ICON,
        bundle_identifier=BUNDLE_ID,
        version=__version__,
        info_plist={
            "CFBundleDisplayName": "RentDesk Lite",
            "CFBundleName": "RentDesk Lite",
            "CFBundleShortVersionString": __version__,
            "CFBundleVersion": __version__,
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,  # 다크 모드 허용
            "LSApplicationCategoryType": "public.app-category.business",
            "NSHumanReadableCopyright": "RentDesk Lite",
        },
    )
