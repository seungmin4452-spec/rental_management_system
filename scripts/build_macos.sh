#!/usr/bin/env bash
# macOS 네이티브 빌드: 테스트 → PyInstaller(.app) → 서명 → .dmg → (선택) 공증.
#   scripts/build_macos.sh
# 환경변수
#   PYTHON               기본 .venv/bin/python
#   SKIP_TESTS=1         테스트 생략(권장하지 않음)
#   RENTDESK_BUNDLE_ID   번들 ID(출시 전 확정)
#   MACOS_SIGN_IDENTITY  Developer ID Application 인증서 이름. 없으면 ad-hoc(개발용)
#   NOTARY_PROFILE       notarytool 키체인 프로필. 서명 인증서와 함께 있으면 공증까지 수행
set -euo pipefail
cd "$(dirname "$0")/.."

[[ "$(uname -s)" == "Darwin" ]] || { echo "macOS에서만 실행합니다."; exit 1; }
PYTHON="${PYTHON:-.venv/bin/python}"
ARCH="$("$PYTHON" -c 'import platform; print(platform.machine())')"
case "$ARCH" in
  arm64)  SUFFIX="macos-arm64" ;;
  x86_64) SUFFIX="macos-x64"; echo "주의: Intel(x86_64) 빌드입니다. Intel 실기기 검증 전에는 판매 표시 금지." ;;
  *) echo "지원하지 않는 아키텍처: $ARCH"; exit 1 ;;
esac
VERSION="$("$PYTHON" -c 'import sys; sys.path.insert(0, "src"); import rentdesk; print(rentdesk.__version__)')"

"$PYTHON" -m pip install -q -e '.[dev,build]'
if [[ "${SKIP_TESTS:-0}" != "1" ]]; then
  QT_QPA_PLATFORM=offscreen "$PYTHON" -m pytest -q
fi

rm -rf build/pyinstaller dist/RentDeskLite dist/RentDeskLite.app
"$PYTHON" -m PyInstaller --noconfirm --clean \
  --workpath build/pyinstaller --distpath dist packaging/pyinstaller/rentdesk.spec

APP="dist/RentDeskLite.app"
packaging/macos/sign_app.sh "$APP"
scripts/smoke_macos.sh "$APP"

DMG="dist/RentDeskLite_${VERSION}_${SUFFIX}.dmg"
packaging/macos/create_dmg.sh "$APP" "$DMG"

if [[ -n "${MACOS_SIGN_IDENTITY:-}" && -n "${NOTARY_PROFILE:-}" ]]; then
  packaging/macos/notarize.sh "$DMG"
else
  echo "== 공증 생략: 개발/사내 시험용 빌드입니다. 외부 판매 금지(MACOS_SIGN_IDENTITY + NOTARY_PROFILE 필요)."
fi
shasum -a 256 "$DMG"
