#!/usr/bin/env bash
# RentDeskLite.app 코드서명. 내부 Mach-O → 프레임워크 → 앱 순서로 서명한다(--deep 의존 금지).
#   MACOS_SIGN_IDENTITY="Developer ID Application: 이름 (TEAMID)"  → 배포용(Hardened Runtime + timestamp)
#   MACOS_SIGN_IDENTITY 미설정                                     → ad-hoc 서명(개발·사내 시험용, 배포 금지)
# 인증서·개인키는 로그인 키체인에만 두고 저장소/로그에 넣지 않는다.
set -euo pipefail
APP="${1:?usage: sign_app.sh path/to/RentDeskLite.app}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ENTITLEMENTS="$HERE/entitlements.plist"

if [[ -n "${MACOS_SIGN_IDENTITY:-}" ]]; then
  IDENTITY="$MACOS_SIGN_IDENTITY"
  OPTS=(--force --options runtime --timestamp --entitlements "$ENTITLEMENTS")
  echo "== 배포용 서명: Developer ID 인증서 사용(이름은 로그에 남기지 않음)"
else
  IDENTITY="-"
  OPTS=(--force)
  echo "== MACOS_SIGN_IDENTITY 없음 → ad-hoc 서명(개발용, Gatekeeper 통과 불가)"
fi

# 1) 내부 Mach-O 파일(깊은 경로부터)
while IFS= read -r f; do
  if file -b "$f" | grep -q "Mach-O"; then
    codesign "${OPTS[@]}" --sign "$IDENTITY" "$f" >/dev/null
  fi
done < <(find "$APP/Contents" -type f \( -name "*.dylib" -o -name "*.so" -o -perm -u+x \) \
           | awk '{ print length($0) "\t" $0 }' | sort -rn | cut -f2-)

# 2) 프레임워크 번들
while IFS= read -r fw; do
  codesign "${OPTS[@]}" --sign "$IDENTITY" "$fw" >/dev/null
done < <(find "$APP/Contents" -type d -name "*.framework" | awk '{ print length($0) "\t" $0 }' | sort -rn | cut -f2-)

# 3) 앱 번들
codesign "${OPTS[@]}" --sign "$IDENTITY" "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"
echo "== 서명 확인 완료: $APP"
