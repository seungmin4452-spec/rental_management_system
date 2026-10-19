# RentalDesk 문서 변경 이력

> 앱 버전별 고객용 변경 내용은 `docs/RELEASE_NOTES.md`에 적는다. 이 파일은 기획·개발 문서 이력이다.

## 앱 1.0.0 준비 — 2026-10-19 (KMONG_LAUNCH_PLAN K 단계)

- 앱 버전 `0.1.0` → **`1.0.0`**(판매 첫 버전). 유료 업데이트를 버전 단위로 구분한다.
- 라이선스에 `updates_through`(예: `1.0`) 추가 — 오류 수정 버전(1.0.x)은 허용, 1.1 이상은 업데이트 구매 후 재발급 라이선스 필요. 필드 없는 예전 라이선스는 1.0까지 허용(K5). 서명 형식(`format_version` 1)은 그대로.
- 판매자 발급 도구: 한국어 Windows 출력 인코딩(cp949) 오류 수정, `--months`(1개월 라이선스), `--updates-through`, `--package`, `--order`, 발급 내역 열 추가(예전 파일 자동 확장)(K3).
- 1개월 라이선스 만료 7일 전 안내(사이드바·설정 > 라이선스), 만료 후 새 라이선스로 기존 데이터 이어 쓰기 통합 테스트(K4).
- 오픈소스 라이선스 고지문(`resources/legal/THIRD_PARTY_NOTICES.txt`, `scripts/make_notices.py`)과 [도움말 > 프로그램 정보 > 오픈소스 라이선스](K6).
- 고객 문서 신설: `docs/USER_GUIDE.md`, `docs/RELEASE_NOTES.md`, `docs/PRIVACY_AND_LICENSE.md`(초안), `docs/customer/INSTALL_GUIDE.md`(그림 일부는 K2에서 캡처)(K6).
- Windows 빌드: Inno Setup 6 사용자 범위 설치 경로(`%LOCALAPPDATA%\Programs\Inno Setup 6`)도 찾도록 `build_windows.ps1` 수정(K1).

## v1.2 — 2026-10-16

- 제품명을 **RentDesk Lite → RentalDesk(렌탈데스크)**로 변경. 출시 전이라 앱 데이터 폴더·기기 코드·라이선스 `product`·백업 manifest·번들 ID·설치 파일 이름까지 바꿨다. 이름 변경 전 백업은 복원을 허용한다.
- `PRD.md` 11.2 판매 구성을 크몽 3단계 패키지(STANDARD/DELUXE/PREMIUM)와 추가 옵션으로 구체화.
- `PRD.md` 11.4 제품 로드맵 추가, 업종별 버전 계획 `INDUSTRY_PRESETS.md` 신설.
- 같은 날 방향 변경: 업종별 버전 대신 **모든 업종이 쓰는 하나의 앱(M4.6 범용화)**. `INDUSTRY_PRESETS.md` → `MULTI_INDUSTRY_PLAN.md`로 대체. 상시 "기타" 카테고리, 카테고리별 설정(장비별 없음), 요금 방식(일·1박2일·주말·주·월), 장비 세트(키트)를 1.0 범위로 확정. U7·U8은 R3·R6로 확장 이동, PRD 2.2/2.3 키트 불일치 해소.

## v1.1 — 2026-10-15

**요청:** 개발은 맥북에서 진행하고, 고객에게 Windows/macOS 양쪽 설치형 애플리케이션 제공.

### 변경 요약

- Windows 전용 제품 정의를 **macOS Apple Silicon arm64 + Windows 11 x64 필수 지원**으로 변경.
- Intel Mac, Windows 10, macOS 구체적인 최저 버전은 테스트 전 지원을 광고하지 않도록 규정.
- macOS용 `.app/.dmg` 생성·Developer ID 서명·공증과 Windows용 `.exe` 네이티브 빌드·설치를 추가.
- 사용자 데이터 위치를 Windows 경로 고정에서 `QStandardPaths.AppLocalDataLocation` 기반으로 변경.
- OS별 경로·라이선스 기기 ID 추상화, Mac↔Windows 백업 호환, CP-01~CP-10 테스트를 추가.
- `CROSS_PLATFORM.md`와 맥북용 `START_HERE.md`, Claude 작업 지침/태스크/와이어프레임 업데이트.
- 제품의 예약·반납·정산·보증금 비즈니스 규칙은 v1.0과 동일하게 유지.

## v1.0 — 2026-10-15

Windows 설치형 오프라인 장비 렌탈 관리 프로그램의 최초 상세 기획 기준선.
