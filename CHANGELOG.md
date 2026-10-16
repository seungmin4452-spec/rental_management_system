# RentalDesk 문서 변경 이력

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
