#!/usr/bin/env bash
# RentDeskLite.app → 배포용 .dmg (드래그 설치용 /Applications 바로가기 포함)
set -euo pipefail
APP="${1:?usage: create_dmg.sh path/to/RentDeskLite.app output.dmg}"
OUT="${2:?usage: create_dmg.sh path/to/RentDeskLite.app output.dmg}"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
ditto "$APP" "$STAGE/$(basename "$APP")"
ln -s /Applications "$STAGE/Applications"
rm -f "$OUT"
hdiutil create -volname "RentDesk Lite" -srcfolder "$STAGE" -ov -format UDZO -fs HFS+ "$OUT" >/dev/null
if [[ -n "${MACOS_SIGN_IDENTITY:-}" ]]; then
  codesign --force --timestamp --sign "$MACOS_SIGN_IDENTITY" "$OUT"
fi
hdiutil verify "$OUT" >/dev/null
echo "== DMG 생성: $OUT"
