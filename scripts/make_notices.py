#!/usr/bin/env python3
"""설치 파일에 들어가는 오픈소스 라이선스 고지문을 만든다 (KMONG_LAUNCH_PLAN K6, PRD 15장 7번).

결과: `src/rentdesk/resources/legal/THIRD_PARTY_NOTICES.txt` (앱 [도움말 > 프로그램 정보]에서 표시, 저장소에 커밋).
의존성 버전을 바꾸면 이 스크립트를 다시 실행한다(테스트가 pyproject 고정 버전과 고지문 버전을 비교한다).

  python scripts/make_notices.py
"""

from __future__ import annotations

import importlib.metadata as md
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "src" / "rentdesk" / "resources" / "legal" / "THIRD_PARTY_NOTICES.txt"
LICENSES = ROOT / "packaging" / "licenses"

# (배포 이름, 표시 이름, 라이선스, 원문 위치) — 원문 위치: dist-info 안 파일 이름 또는 packaging/licenses 파일
COMPONENTS = [
    ("PySide6", "Qt for Python (PySide6, shiboken6) 및 Qt 6 라이브러리", "LGPL-3.0-only",
     [("lic", "LGPL-3.0.txt"), ("lic", "GPL-3.0.txt")]),
    ("openpyxl", "openpyxl", "MIT", [("dist", "LICENCE.rst")]),
    ("et_xmlfile", "et_xmlfile", "MIT", [("dist", "LICENCE.rst")]),
    ("PyNaCl", "PyNaCl (libsodium 포함)", "Apache-2.0 / ISC(libsodium)", [("dist", "LICENSE"), ("lic", "libsodium-ISC.txt")]),
    ("cffi", "cffi", "MIT-0", [("dist", "licenses/LICENSE")]),
    ("pycparser", "pycparser", "BSD-3-Clause", [("dist", "licenses/LICENSE")]),
    ("tzdata", "tzdata (IANA 시간대 데이터)", "Apache-2.0", [("dist", "LICENSE"), ("dist", "LICENSE_APACHE")]),
]

HEADER = """RentalDesk 오픈소스 라이선스 고지 (Third-Party Notices)
=====================================================

RentalDesk는 아래 오픈소스 소프트웨어를 포함합니다. 각 소프트웨어의 저작권은 원저작자에게 있으며,
각 라이선스 조건에 따라 배포됩니다. RentalDesk 이용조건은 이 라이선스들이 주는 권리를 제한하지 않습니다.

[Qt / PySide6 — GNU LGPL v3]
- RentalDesk는 Qt 6와 Qt for Python(PySide6)을 LGPL v3 조건으로 사용합니다.
- Qt·PySide6 라이브러리는 하나의 실행 파일에 합치지 않고, 설치 폴더 안 별도 파일
  (예: PySide6\\Qt6Core.dll, PySide6\\QtCore.pyd)로 들어 있습니다. 사용자는 이 파일들을 호환되는
  다른 버전(직접 수정한 버전 포함)으로 바꿔 사용할 수 있습니다.
- 이 목적(라이브러리 수정 및 그 수정의 디버깅)을 위한 역공학은 RentalDesk 이용조건과 관계없이 허용됩니다.
- Qt·PySide6의 소스 코드: https://download.qt.io/official_releases/QtForPython/ ,
  https://code.qt.io/cgit/pyside/pyside-setup.git/ , https://download.qt.io/official_releases/qt/
  이 설치 파일에 사용된 버전의 소스 코드를 원하시면 판매자에게 요청해 주세요(배포일로부터 최소 3년간 제공).
- LGPL v3는 GPL v3에 추가 허가를 더한 라이선스이므로 두 원문을 함께 싣습니다.

[아이콘 — Lucide, ISC]
- 화면 아이콘 일부는 Lucide(https://lucide.dev) 경로 데이터를 사용합니다.

[Python]
- RentalDesk는 Python 실행 환경을 함께 배포합니다(PSF License 및 Python에 포함된 제3자 구성요소).

[PyInstaller]
- 실행 파일 묶음에 PyInstaller 부트로더를 사용합니다. PyInstaller는 GPL v2 + 부트로더 예외 조건이며,
  예외 조항에 따라 묶인 프로그램에는 GPL 의무가 적용되지 않습니다.
"""


def _dist_file(dist: md.Distribution, rel: str) -> str:
    path = Path(str(dist.locate_file(""))) / f"{dist.metadata['Name'].replace('-', '_')}-{dist.version}.dist-info"
    candidates = [p for p in (dist.files or []) if str(p).replace("\\", "/").endswith(f".dist-info/{rel}")]
    if candidates:
        return Path(str(dist.locate_file(candidates[0]))).read_text(encoding="utf-8")
    return (path / rel).read_text(encoding="utf-8")


def _section(title: str, body: str) -> str:
    line = "=" * 78
    return f"\n{line}\n{title}\n{line}\n\n{body.strip(chr(10))}\n"


def build() -> str:
    parts = [HEADER, "\n구성요소 목록\n-------------\n"]
    texts = []
    for name, label, spdx, sources in COMPONENTS:
        dist = md.distribution(name)
        parts.append(f"- {label} {dist.version} — {spdx}\n")
        for kind, rel in sources:
            text = (LICENSES / rel).read_text(encoding="utf-8") if kind == "lic" else _dist_file(dist, rel)
            texts.append(_section(f"{label} {dist.version} — {rel}", text))
    parts.append(f"- Python {sys.version.split()[0]} — PSF-2.0 외\n")
    parts.append("- Lucide 아이콘 — ISC\n")
    parts.append("- PyInstaller 부트로더 — GPL-2.0-or-later WITH Bootloader-exception\n")
    texts.append(_section("Lucide — ISC License", (LICENSES / "Lucide-ISC.txt").read_text(encoding="utf-8")))
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.exists():
        python_license = Path(sys.base_prefix) / "lib" / f"python{sys.version_info[0]}.{sys.version_info[1]}" / "LICENSE.txt"
    texts.append(_section(f"Python {sys.version.split()[0]} — LICENSE", python_license.read_text(encoding="utf-8")))
    return "".join(parts) + "".join(texts)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(build(), encoding="utf-8", newline="\n")
    print(f"고지문을 만들었습니다: {OUT} ({OUT.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
