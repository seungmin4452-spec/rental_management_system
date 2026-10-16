#!/usr/bin/env bash
# Apple 공증 → 스테이플 → Gatekeeper 확인 (CROSS_PLATFORM H).
# 사전 준비(판매자 Mac에서 1회, 비밀번호는 키체인에만 저장):
#   xcrun notarytool store-credentials "rentdesk-notary" --apple-id <ID> --team-id <TEAMID>
# 실행:
#   NOTARY_PROFILE=rentdesk-notary packaging/macos/notarize.sh dist/RentalDesk_x.y.z_macos-arm64.dmg
set -euo pipefail
DMG="${1:?usage: notarize.sh path/to/app.dmg}"
: "${NOTARY_PROFILE:?NOTARY_PROFILE(키체인 프로필 이름)을 지정하세요}"

xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait
xcrun stapler staple "$DMG"
xcrun stapler validate "$DMG"
spctl --assess --type open --context context:primary-signature --verbose=2 "$DMG"

MNT="$(mktemp -d)"
hdiutil attach "$DMG" -nobrowse -readonly -mountpoint "$MNT" >/dev/null
trap 'hdiutil detach "$MNT" >/dev/null || true; rmdir "$MNT" 2>/dev/null || true' EXIT
spctl --assess --type execute --verbose=2 "$MNT/RentalDesk.app"
codesign --verify --deep --strict --verbose=2 "$MNT/RentalDesk.app"
echo "== 공증·Gatekeeper 확인 완료: $DMG"
