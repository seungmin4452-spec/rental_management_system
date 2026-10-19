#!/usr/bin/env python3
"""RentalDesk 오프라인 라이선스 발급 도구 — **판매자 PC에서만** 실행한다 (PRD 11.1).

개인키는 이 저장소·설치파일·로그에 절대 넣지 않는다. 기본 보관 위치는 저장소 밖
(`~/RentDeskSellerKeys/`)이며, 저장소 안 경로를 지정하면 거부한다.

사용 예:
  # 1) 최초 1회: 서명 키 생성 → 출력된 공개키를 src/rentdesk/license_public_key.py에 넣고 앱 빌드
  python scripts/generate_license.py keygen

  # 2) 고객이 보낸 기기 코드로 라이선스 발급 (DELUXE: 평생, 1.0.x 사용)
  python scripts/generate_license.py issue --machine-code ABCD-EFGH-IJKL-MNOP-QRST \\
      --customer "OO렌탈" --package DELUXE --order 12345678 --out ~/Desktop/RentalDesk_license.json

  # STANDARD(1개월): 발급일 + 1개월 같은 날짜(서울)까지
  python scripts/generate_license.py issue ... --package STANDARD --months 1

  # 3) 발급 파일 확인
  python scripts/generate_license.py verify --file ~/Desktop/RentalDesk_license.json \\
      --machine-code ABCD-EFGH-IJKL-MNOP-QRST

발급 내역은 키 폴더의 `issued_licenses.csv`에 남는다(사용권 이전·재발급·유료 업데이트 확인용).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from nacl.signing import SigningKey, VerifyKey  # noqa: E402

from rentdesk import __version__  # noqa: E402
from rentdesk.domain.license_terms import (  # noqa: E402
    expiry_end_utc,
    expiry_for_months,
    parse_series,
    series_of,
)
from rentdesk.domain.timeutil import SEOUL, now_utc  # noqa: E402
from rentdesk.services.licensing import (  # noqa: E402
    format_machine_code,
    is_machine_code,
    issue_license,
    verify_license,
)

DEFAULT_KEY_PATH = Path.home() / "RentDeskSellerKeys" / "signing_key.json"
LEDGER_NAME = "issued_licenses.csv"
LEDGER_HEADER = ["license_id", "issued_at_utc", "customer_label", "machine_code",
                 "edition", "expires_at_utc", "note",
                 # 2026-10-19 추가(K3·K5): 크몽 패키지, 주문번호, 발급 당시 앱 버전, 업데이트 허용 버전
                 "package", "kmong_order", "app_version", "updates_through"]
PACKAGES = ("STANDARD", "DELUXE", "PREMIUM", "ADDON", "REISSUE", "UPDATE")


def _force_utf8_output() -> None:
    """한국어 Windows에서 출력이 파이프로 연결되면 cp949로 써져 깨지는 문제(K3)를 막는다."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def _inside_repo(path: Path) -> bool:
    try:
        path.resolve().relative_to(REPO_ROOT)
    except ValueError:
        return False
    return True


def _load_key(path: Path) -> SigningKey:
    data = json.loads(path.read_text(encoding="utf-8"))
    return SigningKey(bytes.fromhex(data["private_key_hex"]))


def cmd_keygen(args: argparse.Namespace) -> int:
    path = Path(args.out).expanduser()
    if _inside_repo(path):
        print("오류: 개인키를 저장소 안에 만들 수 없습니다. 저장소 밖 경로를 지정하세요.", file=sys.stderr)
        return 2
    if path.exists():
        print(f"오류: 이미 키 파일이 있습니다: {path} (덮어쓰지 않음)", file=sys.stderr)
        return 2
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(path.parent, 0o700)
    key = SigningKey.generate()
    content = json.dumps({
        "type": "ed25519",
        "product": "RentalDesk",
        "created_at_utc": now_utc().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "private_key_hex": key.encode().hex(),
        "public_key_hex": key.verify_key.encode().hex(),
    }, indent=2)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(content)
    print(f"서명 키를 만들었습니다: {path}")
    print("이 파일을 잃어버리면 같은 앱 빌드용 라이선스를 더 발급할 수 없습니다. 안전한 곳에 별도 백업하세요.")
    print(f"앱에 넣을 공개키(PUBLIC_KEY_HEX): {key.verify_key.encode().hex()}")
    return 0


def cmd_pubkey(args: argparse.Namespace) -> int:
    print(_load_key(Path(args.key).expanduser()).verify_key.encode().hex())
    return 0


def _parse_expiry(value: str | None, months: int | None, issued_at: datetime) -> datetime | None:
    """`--expires`(서울 날짜 끝까지) 또는 `--months`(발급일 + N개월 같은 날짜 끝까지)."""
    if months is not None:
        return expiry_for_months(issued_at, months)
    if not value:
        return None
    return expiry_end_utc(datetime.strptime(value, "%Y-%m-%d").date())


def _append_ledger(ledger: Path, row: dict) -> None:
    """발급 내역 한 줄 추가. 예전 형식(열이 적은) 파일이면 기존 내용을 지키며 머리글을 넓힌다."""
    rows: list[list[str]] = []
    if ledger.exists():
        with open(ledger, newline="", encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
    if rows and rows[0] != LEDGER_HEADER:
        old = rows[0]
        if old != LEDGER_HEADER[:len(old)]:
            raise SystemExit(f"오류: 발급 내역 파일의 머리글을 알 수 없습니다: {ledger}")
        rows = [LEDGER_HEADER] + [r + [""] * (len(LEDGER_HEADER) - len(r)) for r in rows[1:]]
    if not rows:
        rows = [LEDGER_HEADER]
    rows.append([row.get(col, "") for col in LEDGER_HEADER])
    fd, tmp = tempfile.mkstemp(prefix=".ledger-", suffix=".csv", dir=str(ledger.parent))
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerows(rows)
        os.replace(tmp, ledger)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def cmd_issue(args: argparse.Namespace) -> int:
    key_path = Path(args.key).expanduser()
    if not is_machine_code(args.machine_code):
        print("오류: 기기 코드는 XXXX-XXXX-XXXX-XXXX-XXXX 형식(영문 대문자·숫자 2~7)입니다.", file=sys.stderr)
        return 2
    out = Path(args.out).expanduser()
    if _inside_repo(out):
        print("오류: 고객 라이선스 파일을 저장소 안에 만들지 마세요.", file=sys.stderr)
        return 2
    if out.exists() and not args.force:
        print(f"오류: 이미 파일이 있습니다: {out} (--force로 덮어쓰기)", file=sys.stderr)
        return 2
    if args.months is not None and args.expires:
        print("오류: --months와 --expires는 함께 쓸 수 없습니다.", file=sys.stderr)
        return 2
    if args.months is not None and not 1 <= args.months <= 120:
        print("오류: --months는 1~120 사이여야 합니다.", file=sys.stderr)
        return 2
    try:
        parse_series(args.updates_through)
    except ValueError:
        print("오류: --updates-through는 1.0처럼 '주.부' 형식입니다.", file=sys.stderr)
        return 2
    try:
        issued_at = now_utc()
        expires_at = _parse_expiry(args.expires, args.months, issued_at)
    except ValueError:
        print("오류: --expires는 YYYY-MM-DD 형식입니다.", file=sys.stderr)
        return 2
    key = _load_key(key_path)
    text = issue_license(
        key, machine_code=args.machine_code, customer_label=args.customer,
        issued_at=issued_at, edition=args.edition, expires_at=expires_at,
        updates_through=args.updates_through,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    payload = json.loads(text)["license"]
    ledger = key_path.parent / LEDGER_NAME
    _append_ledger(ledger, {
        "license_id": payload["license_id"], "issued_at_utc": payload["issued_at_utc"],
        "customer_label": payload["customer_label"],
        "machine_code": format_machine_code(payload["machine_code"]), "edition": payload["edition"],
        "expires_at_utc": payload["expires_at_utc"] or "", "note": args.note or "",
        "package": args.package or "", "kmong_order": args.order or "",
        "app_version": __version__, "updates_through": payload["updates_through"],
    })
    print(f"라이선스를 발급했습니다: {out}")
    print(f"license_id={payload['license_id']} 기기 코드={format_machine_code(payload['machine_code'])}")
    if expires_at is not None:
        print(f"만료: {expires_at.astimezone(SEOUL):%Y-%m-%d} (서울) 23:59:59까지")
    else:
        print("만료: 없음(평생)")
    print(f"사용 가능 버전: {payload['updates_through']}.x까지")
    print(f"발급 내역 기록: {ledger}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    if args.key:
        public = _load_key(Path(args.key).expanduser()).verify_key
    else:
        from rentdesk.license_public_key import PUBLIC_KEY_HEX
        public = VerifyKey(bytes.fromhex(PUBLIC_KEY_HEX))
    text = Path(args.file).expanduser().read_text(encoding="utf-8")
    result = verify_license(text, public_key=public, machine_code=args.machine_code, now=now_utc())
    print(f"{result.status.value}: {result.message}")
    if result.info is not None:
        expiry = result.info.expires_at
        until = f"{expiry.astimezone(SEOUL):%Y-%m-%d} (서울)" if expiry else "없음"
        print(f"고객: {result.info.customer_label} · 만료: {until}")
        print(f"사용 가능 버전: {result.info.updates_through}.x까지 (이 도구의 앱 버전 {__version__})")
    return 0 if result.valid else 1


def main(argv: list[str] | None = None) -> int:
    _force_utf8_output()
    parser = argparse.ArgumentParser(description="RentalDesk 라이선스 발급 도구(판매자 전용)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("keygen", help="Ed25519 서명 키 생성(최초 1회)")
    p.add_argument("--out", default=str(DEFAULT_KEY_PATH))
    p.set_defaults(func=cmd_keygen)

    p = sub.add_parser("pubkey", help="키 파일의 공개키 출력")
    p.add_argument("--key", default=str(DEFAULT_KEY_PATH))
    p.set_defaults(func=cmd_pubkey)

    p = sub.add_parser("issue", help="기기 코드로 라이선스 파일 발급")
    p.add_argument("--key", default=str(DEFAULT_KEY_PATH))
    p.add_argument("--machine-code", required=True)
    p.add_argument("--customer", required=True, help="고객 표시명(상호 등, 연락처 넣지 않기)")
    p.add_argument("--edition", default="standard")
    p.add_argument("--expires", help="만료일 YYYY-MM-DD(서울). 생략하면 기간 제한 없음")
    p.add_argument("--months", type=int,
                   help="발급일 + N개월 같은 날짜(서울)까지. STANDARD 1개월은 --months 1")
    p.add_argument("--updates-through", default=series_of(__version__),
                   help=f"사용 가능한 최고 버전 '주.부'(기본 {series_of(__version__)}). "
                        "기능 업데이트를 구매한 고객은 새 버전 값으로 재발급")
    p.add_argument("--package", type=str.upper, choices=PACKAGES,
                   help="크몽 패키지/옵션(발급 내역에만 기록)")
    p.add_argument("--order", help="크몽 주문번호(발급 내역에만 기록)")
    p.add_argument("--note", help="발급 내역 메모(예: 'PC 교체 재발급, 이전 license_id ...')")
    p.add_argument("--out", required=True)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_issue)

    p = sub.add_parser("verify", help="라이선스 파일 검사")
    p.add_argument("--file", required=True)
    p.add_argument("--machine-code", required=True)
    p.add_argument("--key", help="생략하면 앱에 들어 있는 공개키로 검사")
    p.set_defaults(func=cmd_verify)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
