# 라이선스 발급·이전 운영 절차 (판매자 전용)

> 이 문서와 `scripts/generate_license.py`는 판매자용입니다. **개인키는 저장소·설치파일·메일·메신저에 넣지 않습니다.**

## 구성

- 서명: Ed25519. 앱에는 공개키(`src/rentdesk/license_public_key.py`)만 들어 있습니다.
- 개인키: 판매자 PC `~/RentDeskSellerKeys/signing_key.json`(권한 600). **분실하면 현재 앱 빌드용 라이선스를 더 발급할 수 없습니다** → 암호화된 USB 등 오프라인 사본을 1개 이상 보관.
- 발급 내역: 같은 폴더 `issued_licenses.csv`(license_id, 발급 시각, 고객 표시명, 기기 코드, 메모).
- 키를 새로 만들면(`keygen`) 공개키를 앱에 넣고 다시 빌드해야 하며, 이전 키로 발급한 라이선스는 새 빌드에서 무효가 됩니다. 출시 후에는 키를 바꾸지 않습니다.

## 신규 발급

```bash
python scripts/generate_license.py issue --machine-code XXXX-XXXX-XXXX-XXXX-XXXX \
    --customer "OO렌탈" --out ~/Desktop/RentDeskLite_OO렌탈.json
python scripts/generate_license.py verify --file ~/Desktop/RentDeskLite_OO렌탈.json \
    --machine-code XXXX-XXXX-XXXX-XXXX-XXXX     # 앱 내장 공개키로 확인
```

- `--customer`에는 상호 등 표시명만 넣고 전화번호·이메일은 넣지 않습니다(라이선스 파일에 그대로 들어감).
- 기간제가 필요하면 `--expires YYYY-MM-DD`(서울 날짜 끝까지 유효). 기본은 기간 제한 없음.

## 사용권 이전·재발급 (PC 교체, Mac ↔ Windows 변경, OS 재설치)

1. 고객이 기존 PC에서 **백업 파일**을 만들고 새 PC에 설치·복원합니다(라이선스 없이도 예제 모드와 "실제 데이터 백업…"은 사용 가능).
2. 고객이 새 PC의 기기 코드를 보냅니다.
3. `issued_licenses.csv`에서 기존 발급 건을 확인하고, 새 기기 코드로 발급하며 `--note "이전: <기존 license_id>"`를 남깁니다.
4. 기본 정책(가설, 판매 전 약관 확정): 1구매 = 동시 1대. 이전 재발급은 연 N회 이내 무상, 동시 2대 사용은 추가 구매.

## 한계(고객 안내 문구에 포함)

- 오프라인 방식이라 **이전 PC의 라이선스를 원격으로 비활성화할 수 없습니다.** 이전 시 기존 PC 사용 중단을 약관으로 요구합니다.
- 오프라인 서명 파일은 완전한 불법복제 방지책이 아니며 기본적인 구매 확인 용도입니다.
- 라이선스 검사에 실패해도 고객의 실제 데이터는 삭제·잠금되지 않습니다(예제 모드와 백업 내보내기만 허용).
