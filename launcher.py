"""PyInstaller 전용 진입점. 상대 import 문제를 피하기 위한 얇은 래퍼."""

from rentdesk.app import main

if __name__ == "__main__":
    raise SystemExit(main())
