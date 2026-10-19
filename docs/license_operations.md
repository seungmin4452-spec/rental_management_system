# 라이선스 발급·이전 운영 절차 (판매자 전용)

> 이 문서와 `scripts/generate_license.py`는 판매자용입니다. **개인키는 저장소·설치파일·메일·메신저·클라우드에 넣지 않습니다.**
> 판매 조건·가격은 `docs/kmong_listing.md`와 `KMONG_LAUNCH_PLAN.md` 0장을 따릅니다(가격은 이 문서에 다시 적지 않음).

## 구성

- 서명: Ed25519. 앱에는 공개키(`src/rentdesk/license_public_key.py`)만 들어 있습니다.
- **발급은 맥북에서만 합니다(2026-10-19 결정).** 개인키는 맥북 `~/RentDeskSellerKeys/signing_key.json`(권한 600)에만 두고 Windows PC로 옮기지 않습니다. **분실하면 현재 앱 빌드용 라이선스를 더 발급할 수 없습니다** → 암호화된 USB 등 오프라인 사본을 1개 이상 보관.
- 발급 내역: 같은 폴더 `issued_licenses.csv`. 열: `license_id, issued_at_utc, customer_label, machine_code, edition, expires_at_utc, note, package, kmong_order, app_version, updates_through`. 예전(7열) 파일은 다음 발급 때 기존 줄을 그대로 두고 머리글만 넓힙니다.
- 키를 새로 만들면(`keygen`) 공개키를 앱에 넣고 다시 빌드해야 하며, 이전 키로 발급한 라이선스는 새 빌드에서 무효가 됩니다. **`keygen`으로 키를 새로 만들지 않습니다.**

## 패키지별 발급

| 크몽 주문 | 명령 옵션 | 결과 |
|---|---|---|
| STANDARD | `--package STANDARD --months 1` | PC 1대, 발급일 + 1개월 같은 날짜(서울) 23:59:59까지 |
| DELUXE | `--package DELUXE` | PC 1대, 기간 제한 없음 |
| PREMIUM | PC마다 `--package PREMIUM` 1번씩(총 2번) | PC 2대, 각각 평생. 두 PC 데이터는 연동되지 않음 |
| 라이선스 추가 | `--package ADDON` | 추가 PC 1대, 평생 |
| 재발급 | `--package REISSUE --note "이전: <license_id>"` | 새 기기 코드, 기존 조건(만료일·버전) 그대로 |
| 기능 업데이트(라이선스마다) | `--package UPDATE --updates-through 1.1 --note "이전: <license_id>"` | 같은 기기 코드, 새 버전까지 사용 |

모든 발급에 `--order <크몽 주문번호>`를 넣습니다. `--updates-through`를 생략하면 발급 도구의 앱 버전 단위(예: 1.0.0 → `1.0`)가 들어갑니다.

```bash
# DELUXE 예
python scripts/generate_license.py issue --machine-code XXXX-XXXX-XXXX-XXXX-XXXX \
    --customer "OO렌탈" --package DELUXE --order 12345678 --out ~/Desktop/RentalDesk_OO렌탈.json
# STANDARD(1개월) 예 — 2026-10-19 발급 → 2026-11-19 23:59:59(서울)까지
python scripts/generate_license.py issue --machine-code XXXX-XXXX-XXXX-XXXX-XXXX \
    --customer "OO렌탈" --package STANDARD --months 1 --order 12345678 --out ~/Desktop/RentalDesk_OO렌탈.json
# 발급 파일 확인(앱 내장 공개키) — 만료일·사용 가능 버전도 출력
python scripts/generate_license.py verify --file ~/Desktop/RentalDesk_OO렌탈.json \
    --machine-code XXXX-XXXX-XXXX-XXXX-XXXX
```

- `--customer`에는 상호 등 표시명만 넣고 전화번호·이메일은 넣지 않습니다(라이선스 파일에 그대로 들어감).
- 1개월 만료일 규칙: **발급일(서울 날짜) + 1개월의 같은 날짜, 그 날 끝까지.** 그 달에 같은 날이 없으면 말일(1월 31일 → 2월 28/29일). 특정 날짜가 필요하면 `--expires YYYY-MM-DD`(`--months`와 함께 쓸 수 없음).
- STANDARD → 평생 전환은 DELUXE/PREMIUM **새 구매**(차액 없음). 새 라이선스를 등록하면 기존 실제 데이터를 그대로 이어서 씁니다.

## 버전과 유료 업데이트

- 라이선스의 `updates_through`(예: `1.0`)까지의 앱만 실행됩니다. **오류 수정 버전(1.0.1, 1.0.2 …)은 항상 무료로 실행**되고, 기능 업데이트 버전(1.1 이상)은 업데이트를 구매해 재발급받은 라이선스가 필요합니다.
- 업데이트를 사지 않은 고객이 새 버전을 설치하면 시작 화면에 안내가 나오고 실제 데이터 화면이 열리지 않습니다. 데이터는 지워지지 않으며, 이전 버전을 다시 설치하면 그대로 이어서 쓸 수 있고 백업 내보내기도 됩니다.
- 이 필드가 없는 예전 라이선스(2026-10-19 이전 개발용 발급분)는 `1.0`까지 허용합니다.
- 버전 번호 규칙: 오류 수정만 → 끝자리(1.0.**1**). 기능 추가(작은 것도 모아서) → 가운데 자리(1.**1**.0), 끝자리 0부터.
- 업데이트 결제 여부는 `issued_licenses.csv`의 `package=UPDATE`, `updates_through` 열로 확인합니다.

## 사용권 이전·재발급 (PC 교체, Mac ↔ Windows 변경, OS 재설치)

1. 고객이 기존 PC에서 **백업 파일**을 만들고 새 PC에 설치·복원합니다(라이선스 없이도 예제 모드와 "실제 데이터 백업…"은 사용 가능).
2. 고객이 새 PC의 기기 코드를 크몽 메시지로 보냅니다.
3. `issued_licenses.csv`에서 기존 발급 건(패키지·만료일·`updates_through`)을 확인하고, 같은 조건으로 새 기기 코드에 발급하며 `--package REISSUE --note "이전: <기존 license_id>"`를 남깁니다. 1개월 라이선스는 기존 만료일을 `--expires`로 그대로 넣습니다.
4. 정책: **1구매 = 동시 1대.** 사용권 이전·재발급은 **건당 10,000원**(PC 교체, OS 재설치·포맷, Mac ↔ Windows 변경 모두 동일). 동시에 PC를 더 쓰려면 라이선스 추가(99,000원).
5. 발급은 판매자가 직접 처리하므로 즉시 발급되지 않습니다. 고객에게는 발급 전까지 예제 데이터 모드로 체험할 수 있다고 안내합니다.

## 한계(고객 안내 문구에 포함)

- 오프라인 방식이라 **이전 PC의 라이선스를 원격으로 비활성화할 수 없습니다.** 이전 시 기존 PC 사용 중단을 약관으로 요구합니다.
- 오프라인 서명 파일은 완전한 불법복제 방지책이 아니며 기본적인 구매 확인 용도입니다.
- 라이선스 검사에 실패해도(만료·다른 PC·업데이트 미구매) 고객의 실제 데이터는 삭제·잠금되지 않습니다(예제 모드와 백업 내보내기만 허용).
- 만료 검사는 앱을 켤 때 합니다. 앱을 켜 둔 채 만료 시각이 지나면 다음 실행 전까지는 계속 쓸 수 있습니다.
- 만료 7일 전부터 화면 왼쪽 아래와 설정 > 라이선스에 남은 기간이 표시됩니다.
