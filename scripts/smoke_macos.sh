#!/usr/bin/env bash
# 빌드된 .app 구동 검증: 자기 점검 실행 + 서명 확인 + 번들 안 비밀정보/DB 미포함 확인.
# 사용자 데이터 폴더를 건드리지 않는다(자기 점검은 임시 폴더에서만 동작).
set -euo pipefail
APP="${1:-dist/RentalDesk.app}"
BIN="$APP/Contents/MacOS/RentalDesk"
OUT="$(mktemp -d)/selfcheck.json"

[[ -x "$BIN" ]] || { echo "실행 파일 없음: $BIN"; exit 1; }
"$BIN" --self-check "$OUT" || { echo "자기 점검 실패"; cat "$OUT" 2>/dev/null; exit 1; }
grep -q '"ok": true' "$OUT" || { cat "$OUT"; exit 1; }
cat "$OUT"

codesign --verify --deep --strict "$APP"

# 개인키·서명 비밀·사용자 DB가 번들에 없어야 한다(PRD 15-4)
if grep -rIl --exclude='*.pyc' -e 'private_key_hex' "$APP" >/dev/null 2>&1; then
  echo "번들에 개인키 흔적이 있습니다"; exit 1
fi
if find "$APP" \( -name '*.db' -o -name '*.sqlite' -o -name '*.pem' -o -name '*.p12' -o -name 'signing_key*' \) | grep -q .; then
  echo "번들에 DB/인증서/키 파일이 있습니다"; exit 1
fi
echo "== macOS 스모크 통과: $APP"
