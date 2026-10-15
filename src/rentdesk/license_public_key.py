"""라이선스 검증용 Ed25519 **공개키**(PRD 11.1). 개인키는 판매자 PC에만 있다.

키를 바꾸면(`scripts/generate_license.py keygen`) 이전 키로 발급한 라이선스는 새 빌드에서
더 이상 유효하지 않다. 출시 후에는 바꾸지 않는다.
"""

PUBLIC_KEY_HEX = "45eeeecbf1cc40fe0233592fb6b1f4f452af64923f29876659a419e68af07934"
