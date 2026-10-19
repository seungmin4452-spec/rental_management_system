"""RentalDesk — 오프라인 설치형 장비 렌탈 관리 프로그램."""

# 앱/설치파일/백업 manifest가 같은 값을 쓴다. pyproject.toml은 이 값을 읽는다.
__version__ = "1.0.0"
PRODUCT = "RentalDesk"
# 2026-10-16 이름 변경 전 제품명. 그때 만든 백업 파일을 복원할 수 있게 백업 manifest에서만 인정한다.
LEGACY_PRODUCTS = ("RentDeskLite",)
