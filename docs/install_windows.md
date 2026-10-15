# RentDesk Lite 설치 안내 — Windows

> 상태: **초안(M4)**. Windows 11 x64용입니다. Windows 10은 실기기 검증 후 지원 여부를 표시합니다.
> 설치 파일은 Windows에서 빌드합니다. 코드 서명(Authenticode) 전에는 SmartScreen 경고가 나올 수 있습니다.

## 설치

1. `RentDeskLite_x.y.z_win-x64_setup.exe`를 실행합니다.
2. 기본값은 관리자 권한 없이 현재 사용자용으로 설치합니다(모든 사용자용 설치는 설치 첫 화면에서 선택).
3. 시작 메뉴 또는 바탕 화면의 **RentDesk Lite**를 실행합니다.

## 첫 실행 — 라이선스 등록

macOS와 같습니다: 기기 코드 복사 → 판매자에게 전달 → 받은 라이선스 파일 불러오기. 라이선스 전에도 예제 데이터로 체험할 수 있습니다.

## 데이터 위치

- `%LOCALAPPDATA%\RentDeskLite\` (예: `C:\Users\<사용자>\AppData\Local\RentDeskLite\`)
  - `data\rentdesk.db` · `backups\` · `logs\` · `license\`
- 설치 폴더(`Program Files` 또는 `AppData\Local\Programs`)에는 데이터를 저장하지 않습니다.
- **제거해도 데이터는 삭제되지 않습니다.** 완전히 지우려면 백업 여부를 확인한 뒤 위 폴더를 직접 삭제하세요.

## 백업 · 다른 PC로 옮기기

macOS 안내와 같습니다. Windows에서 만든 백업을 Mac에서, Mac에서 만든 백업을 Windows에서 복원할 수 있도록 같은 형식을 씁니다(양방향 실기기 검증은 출시 전 진행).

## 업데이트

새 설치 파일을 실행하면 같은 위치에 덮어 설치됩니다. 프로그램이 실행 중이면 먼저 종료하세요.
