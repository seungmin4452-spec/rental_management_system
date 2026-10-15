# Open Questions / 결정 기록

PRD.md 17.6 지침에 따라 문서에 없는 사항을 임의 구현하지 않고, 불명확한 부분의 결정과 이유를 여기에 남긴다.

## M0

| 항목 | 결정 | 이유 |
|---|---|---|
| Windows 네이티브 빌드 환경 | GitHub Actions `windows-latest` 러너 우선 사용 | 별도 Windows PC/VM 라이선스 없이 즉시 준비 가능. CROSS_PLATFORM.md D의 "선택지 1"에 해당. 단, CI 통과만으로 Windows 실기기 설치 검증(CP-04, CP-10)을 대체하지 않음 — M4/M5에서 반드시 실기기 재확인 |
| macOS CI 러너 | `macos-14` (Apple Silicon) 사용 | P0 필수 대상(arm64)과 아키텍처 일치. 로컬 맥북(arm64)과도 동일 |
| 의존성 버전 고정 | PySide6==6.7.3, openpyxl==3.1.5, PyNaCl==1.5.0, pytest==8.3.3, pytest-cov==5.0.0, freezegun==1.5.1, pyinstaller==6.10.0(build extra) | PRD 6번 항목 "호환되는 의존성 버전 잠금" 충족. PyInstaller는 아직 사용하지 않지만(M4) 버전 선충돌 방지를 위해 extras로 분리 |
| macOS 최소 지원 버전 | 미확정, 추후 실기기/배포 전 확정 | PRD 12A.1: 검증 전 표기 금지 |
| Intel Mac / Windows 10 | P0 범위 제외, 조건부 P1 | PRD 12A.1과 동일 |
| CI 범위(M0) | pytest 실행까지만. PyInstaller/서명/공증/Inno Setup은 M4에서 추가 | TASKS.md M4 항목과 중복 방지 |

## 샘플 데이터 / 데모 모드 계획 (M0 설계, 구현은 M1 이후)

- 샘플 데이터는 `src/rentdesk/resources/sample_data/`에 JSON/SQL seed 형태로 보관하고, 실제 사용자 DB와 분리된 별도 SQLite 파일(`:memory:` 또는 임시 경로)로 로드한다(PRD 5장 "예제 데이터 모드" 요건).
- 데모 모드는 장비 10~20종, 고객 10~20명, 과거/진행/연체 예약을 각각 포함해 S-01~S-09 화면 시연이 가능한 최소 구성으로 M3에서 실제 적재 로직을 구현한다.
- 데모 DB와 실 운영 DB의 경로를 혼동하지 않도록 `platform/paths.py`에 데모 전용 경로 함수를 M1~M3 사이에 추가할 계획이며, 지금은 과설계를 피하기 위해 미구현.

## 다음 확인 필요

- Windows 네이티브 빌드/실기기 검증을 GitHub Actions만으로 끝낼지, 추가로 실제 Windows PC/VM에서 설치형 테스트를 할지는 M4 착수 시 재확인.
- macOS 서명/공증용 Apple Developer 계정 보유 여부는 M4 착수 전 사용자에게 확인 필요.
