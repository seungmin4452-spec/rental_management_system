"""PDF 글자 확인용 하위 프로세스(R6 테스트에서 실행).

UI 테스트는 offscreen 플랫폼으로 도는데, Windows의 offscreen에는 시스템 글꼴이 없어 PDF에 글자가
그려지지 않는다. 실제 앱과 같은 기본 플랫폼(Windows·macOS)으로 따로 실행해 확인한다.
사용법: python pdf_probe.py <출력 폴더>  → JSON 한 줄(문서별 쪽수·글자·글꼴 포함 여부)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication


def main(out_dir: Path) -> None:
    app = QApplication([])  # noqa: F841 — 글꼴 DB를 쓰려면 필요
    from PySide6.QtPdf import QPdfDocument

    from rentdesk.domain.documents import DocumentKind
    from rentdesk.persistence.db import open_database
    from rentdesk.services.customers import CustomerInput
    from rentdesk.services.kits import KitInput, KitPick
    from rentdesk.services.reservations import ReservationInput
    from rentdesk.ui import document_output
    from tests.support import FakeClock, Services, kst

    conn = open_database(out_dir / "probe.db")
    svc = Services(conn, FakeClock(kst(10, 19, 9)))
    svc.settings.update_business_info(name="테스트 렌탈", phone="02-123-4567")
    cam = svc.add_asset("CAM-1", name="미러리스 카메라")
    lens = svc.add_asset("LENS-1", rate=10_000, category="렌즈", name="렌즈")
    kit = svc.kits.create_kit(KitInput(name="카메라 세트", asset_ids=[cam.id, lens.id], checklist=["배터리", "충전기"]))
    customer = svc.customers.create_customer(CustomerInput(name="홍길동", phone="010-1111-2222"))
    data = ReservationInput(customer_id=customer.id, start_at=kst(10, 20, 10), due_at=kst(10, 23, 10),
                            asset_ids=[cam.id, lens.id], kits=[KitPick(kit.id, (cam.id, lens.id))])
    quote_order = svc.reservations.create_draft(data)
    order = svc.reservations.create_confirmed(ReservationInput(
        customer_id=customer.id, start_at=kst(11, 20, 10), due_at=kst(11, 23, 10), asset_ids=[cam.id, lens.id],
        kits=[KitPick(kit.id, (cam.id, lens.id))]))
    svc.checkout(order.id, kst(11, 20, 10), note="스크래치 있음")
    svc.return_asset(order.id, cam.id, kst(11, 23, 15), damage_fee_won=5_000)

    result = {}
    for name, order_id, kind in (("quote", quote_order.id, DocumentKind.QUOTE),
                                 ("rental", order.id, DocumentKind.RENTAL),
                                 ("return", order.id, DocumentKind.RETURN)):
        content = document_output.pdf_bytes(svc.documents.build(order_id, kind))
        path = out_dir / f"{name}.pdf"
        path.write_bytes(content)
        pdf = QPdfDocument()
        pdf.load(str(path))
        result[name] = {
            "pages": pdf.pageCount(),
            "text": "".join(pdf.getAllText(p).text() for p in range(pdf.pageCount())),
            "font_embedded": b"/FontFile2" in content,
            "to_unicode": b"/ToUnicode" in content,
        }
        pdf.close()
    conn.close()
    sys.stdout.write(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
