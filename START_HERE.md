# RentalDesk — 맥북에서 시작하는 Claude Code 개발 안내

> **제품 기획 v1.1** — 맥북을 주 개발환경으로 사용하고 **macOS + Windows**에 같은 기능의 설치형 앱을 배포합니다. 이 ZIP에는 기획서만 있으며 소스코드·서명된 설치파일은 없습니다.

## 문서 구성

- `PRD.md` — 제품 범위, 예약/반납/정산 규칙, DB 설계, 테스트 및 출시 승인 기준.
- `CROSS_PLATFORM.md` — 맥 개발환경, Mac/Windows 네이티브 빌드, 경로·라이선스·데이터 이동, 서명 및 공증의 상세 규칙.
- `CLAUDE.md` — Claude Code가 반드시 지켜야 하는 개발/테스트/보고 지침.
- `TASKS.md` — 마일스톤과 OS별 체크리스트.
- `WIREFRAMES.md` — 공통 화면 설계.
- `CHANGELOG.md` — v1.0 → v1.1 변경사항.

## macOS에서 시작하기

1. ZIP 압축 해제 후 모든 Markdown 파일을 **하나의 Git 프로젝트 루트**에 둡니다.
2. Mac Terminal에서 프로젝트 폴더로 이동하여 Claude Code를 실행합니다.
3. 먼저 `PRD.md`, `CROSS_PLATFORM.md`, `CLAUDE.md`, `TASKS.md`, `WIREFRAMES.md`를 읽도록 지시합니다.
4. Mac에서 `pytest` 기반 비즈니스 로직을 먼저 구현한 후 PySide6 GUI를 개발합니다.
5. Mac용 `.app/.dmg`는 Mac에서, Windows용 `.exe`는 **Windows의 CI/VM/실기기에서** 별도 빌드합니다. 두 플랫폼의 실제 설치·백업·정산 동작을 각각 검증합니다.

## Claude에게 첫 메시지로 붙여넣을 프롬프트

```text
현재 작업 디렉터리는 RentalDesk 프로젝트 루트입니다.
나는 맥북에서 개발하고, 최종 고객에게는 macOS와 Windows용 설치형 앱을 모두 판매할 예정입니다.

PRD.md, CROSS_PLATFORM.md, CLAUDE.md, TASKS.md, WIREFRAMES.md, CHANGELOG.md를 모두 읽으세요.
P0 기준 공식 지원 대상은 macOS Apple Silicon arm64와 Windows 11 x64이며,
Intel Mac과 Windows 10은 별도 빌드/검증 전에는 지원됐다고 주장하지 마세요.

하나의 Python/PySide6/SQLite 코드베이스에서 두 운영체제의 동일 기능을 구현하세요.
비즈니스 로직은 OS 독립적으로 유지하고, 파일 경로, 기기식별, 패키징만 분리하세요.
Mac에서는 Mac 앱을 만들고 Windows 실행파일은 Windows CI 또는 네이티브 환경에서 빌드하세요.
실제 테스트하지 않은 설치파일의 완성이나 플랫폼 지원을 주장하지 마세요.

먼저 요구사항 충돌/빠진 점과 아키텍처, Mac 개발환경 구성,
Windows 네이티브 빌드 준비, DB 구조, 마일스톤·테스트 계획을 보고한 뒤
M0부터 구현하세요. 각 마일스톤에 실제 실행한 명령과 테스트 결과를 남기세요.
GUI보다 도메인/예약 충돌/금액/보증금 테스트를 우선하세요.
P1/P2는 임의로 구현하지 마세요.
```

## 릴리스 파일 형태

- Windows: `RentalDesk_1.0.0_win-x64_setup.exe` (네이티브 Windows 빌드)
- macOS Apple Silicon: `RentalDesk_1.0.0_macos-arm64.dmg` (Developer ID 서명 및 Apple 공증)

사용자 DB는 앱 설치 경로가 아니라 OS별 사용자 앱 데이터 폴더에 저장합니다. Mac↔Windows 백업 이동은 지원하되 기기 라이선스 재발급/이전이 필요할 수 있습니다.

## 주의

- macOS 공증/코드서명에는 적절한 Apple 개발자 계정/Developer ID 인증서가 필요합니다.
- Windows 파일은 Mac의 PyInstaller에서 직접 생성할 수 없습니다.
- macOS Intel 지원 및 macOS 최소 지원 버전은 실기기 검증 전까지 광고하지 않습니다.
- 크몽 배포·고객지원·플랫폼별 설치 테스트를 마쳐야 상용 출시로 간주합니다.
