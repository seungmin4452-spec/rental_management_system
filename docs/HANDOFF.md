# 작업 인수인계 문서 (HANDOFF)

> 이 문서는 단계가 끝날 때마다 **덮어써서 최신 상태만** 남긴다. 지난 내역은 git 커밋 로그, `docs/PROGRESS_REPORT.md`(M0~M4 요약), `USABILITY_PLAN.md` "진행 기록"(M4.5 단계별), `OPEN_QUESTIONS.md`(결정 근거)에서 확인한다.
> 새 세션은 **이 문서 → `CLAUDE.md` → `USABILITY_PLAN.md` → `TASKS.md`** 순서로 읽으면 바로 이어서 작업할 수 있다.

## 현재 상태 (2026-10-19): M4.6 범용화 진행 중 — R1·R2·R3 완료, **R4부터 이어서**

- **M4.6 R1("기타" 카테고리)·R2(카테고리별 운영 설정, 품목별 준비 시간 스냅샷) 완료**, Windows PC에서 커밋해 브랜치 `m4.5-usability`로 푸시. 맥북에서는 `git fetch && git switch m4.5-usability && git pull` 후 `pip install -e '.[dev,build]'`(새 의존성 없음)로 이어간다. 상세·다음 작업 위치는 `MULTI_INDUSTRY_PLAN.md` "진행 기록".
- **2026-10-19 R3(요금 방식 확장) 완료** — 스키마 버전 **6**(`006_rate_rules`). 상세·가정(시간 단위 연체 N=10 등)은 `MULTI_INDUSTRY_PLAN.md` 진행 기록.
- 스키마 버전 5까지: `004_system_category`, `005_category_policy`. 맥북에서 처음 실행하면 개발용 DB가 자동 마이그레이션된다(이전 DB는 `backups`에 보호 백업).

- 원격 저장소: `https://github.com/seungmin4452-spec/rental_management_system.git` (`main`은 `e477eda`까지, M4.5 작업은 브랜치 `m4.5-usability`).
- U0~U6·U9(+반납 창 연체료 문구 정리)는 Windows PC에서 작업해 브랜치 **`m4.5-usability`**로 커밋·푸시했다(`main`에는 아직 합치지 않음). 다른 PC에서는 `git fetch && git switch m4.5-usability`로 이어간다.
- M4.5는 크몽 판매 전 사용자 관점 검토에서 나온 개선 단계다. 상세 요구사항·완료 조건·진행 기록은 **`USABILITY_PLAN.md`**, 체크리스트는 `TASKS.md`의 "M4.5".
- **2026-10-16 제품명 변경: RentDesk Lite → RentalDesk(렌탈데스크).** 표시 이름과 내부 식별자(`PRODUCT`, Qt 앱 이름, 번들 ID `com.example.rentaldesk`, 설치 파일·아이콘·백업 파일 이름)를 모두 바꿨다. 영향:
  - 앱 데이터 폴더가 `RentalDesk`로 바뀌어 개발 중 쓰던 `RentDeskLite` 폴더의 데이터는 자동으로 옮기지 않는다(출시 전이라 고객 데이터 없음). 필요하면 옛 앱 버전에서 백업 → 새 버전에서 복원(옛 이름 백업 복원 허용, `LEGACY_PRODUCTS`).
  - 기기 코드 계산에 제품명이 들어가므로 **기기 코드가 모두 바뀌고 기존 라이선스는 무효**. 개발용 라이선스는 다시 발급한다.
  - 그대로 둔 것: Python 패키지 `rentdesk`, `rentdesk.spec`/`rentdesk.iss` 파일 이름, `RENTDESK_*` 환경변수, Inno Setup `AppId`, 판매자 키 폴더 `~/RentDeskSellerKeys`(실제 서명 키 위치라 바꾸지 않음), 공증 프로필 이름 `rentdesk-notary`.
- 제품 로드맵은 `PRD.md` 11.4. **2026-10-16 범용화 결정**: 업종별 버전 대신 모든 업종이 쓰는 하나의 앱으로 가며, 1.0 전에 **M4.6(`MULTI_INDUSTRY_PLAN.md`)**을 진행한다 — 상시 "기타" 카테고리, 카테고리별 설정(장비별 설정 없음), 요금 방식(일·1박2일·주말·주·월), 장비 세트(키트). U7·U8은 M4.6 R3·R6로 확장 이동.
- M4 미완료 항목(Developer ID 서명·공증, Windows 네이티브 빌드, Python 미설치 PC 설치 검증, Mac↔Windows 실제 왕복 복원, CP-02~CP-10)과 M5 출시 항목은 그대로 남아 있다.

## 다음 할 일

| 단계 | 내용 | 시작 조건 |
|---|---|---|
| R1 | "기타" 카테고리 상시 제공 | **완료** |
| R2 | 카테고리별 운영 설정 | **완료**(PRD 7.3 문구 갱신, 반납 창 출처 표시만 남음) |
| R3 | 요금 방식 확장(구 U7) | **완료**(2026-10-19, 685 passed) |
| R4 | 장비 세트(키트) | **다음 작업**. 결정 완료(7장) |
| R5~R7 | 추가 비용 일반화, 확인서·견적서(구 U8, **약관 문구 초안 확인 필요**), 시작 도우미(1.0 포함) | R4 뒤 |

M4.5는 U0~U6·U9·U10 완료로 마감(U7·U8은 M4.6로 이동). 각 단계는 `MULTI_INDUSTRY_PLAN.md`의 체크리스트대로 구현하고, 끝나면 그 파일의 "진행 기록"과 `TASKS.md` 체크박스, 이 문서를 갱신한다.

## 환경 재현

### Windows 11 (현재 작업 PC)

```powershell
cd C:\Users\SMSHIN\git\rental_management_system
py install 3.12                      # 이미 3.12.10 설치됨. 3.14/3.11은 PySide6 6.7.3과 맞지 않음
py -V:3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1          # 막히면: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
python -m pip install --upgrade pip
pip install -e ".[dev]"

python -m pytest -q --deselect tests/unit/test_licensing.py::test_seller_script_keygen_issue_verify
python -m rentdesk                    # 라이선스 창 → "예제 데이터로 체험"
```

- 이 PC 주의: 이 저장소 폴더는 상위 폴더 `C:\Users\SMSHIN\git`(다른 프로젝트 `swkim128/pignai` 저장소) 안에 있지만, 자체 `.git`이 있어 독립된 저장소로 동작한다.
- Bash 도구에서 실행할 때는 `.venv/Scripts/python.exe -m pytest ...`처럼 가상환경 파이썬을 직접 지정한다.

### macOS (원래 개발 맥북)

```bash
git clone https://github.com/seungmin4452-spec/rental_management_system.git
cd rental_management_system
python3.12 -m venv .venv && source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev,build]'
QT_QPA_PLATFORM=offscreen python -m pytest -q
python -m rentdesk
scripts/build_macos.sh                # .app + .dmg (ad-hoc 서명, 공증 생략)
```

### 테스트 현황

| 명령 | 환경 | 결과 |
|---|---|---|
| `pytest -q --deselect tests/unit/test_licensing.py::test_seller_script_keygen_issue_verify` | Windows 11 x64 (10.0.26200), Python 3.12.10, PySide6 6.7.3 | **543 passed, 3 skipped** (U10 완료 시점) |
| 위에서 뺀 1개 | 같은 PC | **실패(기존 문제, U0~U6과 무관)**: 판매자 스크립트 `generate_license.py`가 한국어 Windows 콘솔 인코딩(cp949)으로 출력하는데 테스트가 UTF-8로 읽음. Windows에서 라이선스를 발급하면 같은 문제가 날 수 있음. 아직 고치지 않음 |
| `python -m rentdesk --self-check <json>` | 같은 PC | ok |
| 같은 명령 (M4.6 R1·R2 완료 시점) | Windows 11 x64, 같은 환경 | **580 passed, 3 skipped** |
| 같은 명령 (M4.6 R3 완료 시점) | Windows 11 x64, 같은 환경 | **685 passed, 3 skipped** |
| macOS 전체 테스트 | — | M4 시점 387 passed. **U0~U6·U9·U10·M4.6 R1·R2 이후 macOS에서는 아직 안 돌림 — 맥북에서 먼저 실행할 것** |

## 라이선스 (중요)

- 판매자 서명 키는 **맥북의 `~/RentDeskSellerKeys/signing_key.json`에만** 있다(저장소 밖). 공개키는 `src/rentdesk/license_public_key.py`. 키를 Git·메신저로 옮기지 않는다. 발급은 맥북에서만.
- **`keygen`으로 키를 새로 만들지 않는다**(공개키가 바뀌면 기존 라이선스가 모두 무효).
- Windows PC에는 라이선스가 없다. 사용자는 **예제 데이터 모드로만 테스트하기로 했다**(테스트용 키를 만들어 우회하는 방식은 사용자가 원하지 않아 되돌림).
- 재발급 정책: 사용권 이전·재발급 **건당 10,000원**(`docs/license_operations.md`).

## 이번 세션(2026-10-16)의 사용자 결정

- Windows 코드 서명 인증서는 사지 않는다(미서명 출시 유지).
- 라이선스 발급 대기는 감수하고, 재발급은 1만원.
- 화면 배율은 앱이 PC마다 자동으로 맞추고 설정에서 고를 수도 있게(U0).
- 검토의 "실제 업무 불편(2번)·화면(3번)·용어(4번)"을 M4.5 단계로 만들어 개발. 기존 P1 백로그의 캘린더·엑셀 가져오기·대여확인서·연장·예약금·자동 백업·요금제 세분화를 출시 전 범위로 올림(PRD 2.2 표기 완료).
- 응답은 한국어로.

## U0~U6·U9·U10에서 바뀐 것 (요약 — 상세는 `USABILITY_PLAN.md`)

- **U0 화면 배율**: `ui/display_scale.py`(작업 영역을 재서 메인 창이 안 들어가면 `QT_SCALE_FACTOR`로 축소, 설정에서 자동/80~150%, 기기별 `preferences.json`의 `ui_scale`), `MainWindow._fit_to_screen`.
- **U1 용어**: 은퇴 → 사용 중지, 보관 → 거래 중지(숨김), 전환 버퍼 → 반납 후 준비 시간, 연체 배수 → 연체 1일당 일 대여료의 ○배, 순수금 → 받은 대여료, 대금충당 → 보증금에서 차감, 역거래 → 취소 기록. 표시 글자만 변경(DB 값·코드 이름 그대로). `tests/unit/test_terminology.py`가 옛 용어 재등장을 막음.
- **U2 화면**: 예약 2단계 장비 선택 표(사용 불가 이유 열, 고를 수 있는 장비 위로), 연체 행 연한 빨강 바탕(`theme.RowHighlightDelegate` — 표 QSS가 칸 배경을 무시하므로 필요), 설정 입력칸 폭, 도움말 > 프로그램 정보·문의(`ui/dialogs/about_dialog.py`, 문의 문구는 `resources/support/support.json`).
- **U3 자동 백업**: `services/auto_backup.py`. 라이선스 유효할 때 종료 시 `backups/auto/`에 하루 1개, 최근 14개. 7일 백업 알림(마지막 백업)은 갱신하지 않음.
- **U4 정산 마무리**: `domain/settlement.plan_final_settlement`, `SettlementService.final_plan/finalize`(한 트랜잭션, 저장 직전 계획이 바뀌면 거부).
- **U5 선입금**: 상태별 허용 거래 `MONEY_KINDS_BY_STATUS`(확정 예약은 선입금·보증금 수령과 그 환급·반환, 취소는 환급·반환·차감·수금). 취소 수수료는 **조정(ADJUSTMENT) 청구 + 설명 "예약 취소 수수료"** (DB 변경 없음). 돈 남은 취소 예약도 미정산 대상.
- **U6 연장**: `services/extensions.py`, 마이그레이션 `003_order_extensions.sql`(스키마 버전 **3**). 연장 대여료는 주문 단위 조정 청구.

- **U9 예약 달력**: 메뉴 "예약 달력"(`ui/pages/calendar.py`), `services/calendar.py`(보기 기간만 조회), `domain/calendar.py`(칸 범위·줄 배정·선택 칸 → 예약 기간). 예약 마법사에 장비·기간 미리 채우기 인자 추가. DB 변경 없음.
- **U10 엑셀 가져오기**: 장비·고객 화면의 [엑셀로 가져오기](`ui/dialogs/import_dialog.py`) → `services/bulk_import.py`(양식 저장, 읽기·행별 검사·미리보기, 등록 직전 `backups/before_import/` 백업, 한 트랜잭션 등록, 미리보기 후 DB가 바뀌면 거부), 칸 값 해석은 `domain/import_cells.py`. `reports.save_workbook`을 분리해 함께 씀. DB 변경 없음.
- 전체 테스트 중 `test_seller_script_refuses_key_inside_repo`가 스레드 경고(cp949 디코드)를 냄 — 위 cp949 문제와 같은 원인.

### DB 마이그레이션

- `001_initial`(M1), `002_checkout_note`(M2), `003_order_extensions`(M4.5 U6), **`004_system_category`(R1), `005_category_policy`(R2)**. 현재 스키마 버전 5.
- `006_rate_rules`(R3, 스키마 버전 6). 계획상 R4에서 `007`(세트).

## 작업 시 알아둘 것 (이번 세션에서 겪은 문제)

- Bash heredoc 안의 파이썬 스크립트로 소스 파일을 고칠 때 `"\\n"`이 실제 줄바꿈으로 들어가 문법 오류가 난 적이 여러 번 있었다. 줄바꿈 문자가 들어가는 수정은 편집 도구(Edit)로 하는 것이 안전하다.
- 화면 변경은 테스트만으로 부족했다(연체 행 바탕색은 테스트 통과했지만 실제로는 안 보였음). 예제 데이터로 화면을 그려 PNG로 확인하는 방식을 썼다: `create_demo_database` → `AppContext(is_demo=True)` → `MainWindow`/대화상자 `.grab().save(...)`.
- 오프스크린 테스트 화면은 작아서 메인 창 최소 크기가 작업 영역에 맞춰 줄어든다(테스트도 그에 맞게 작성됨).

## 미완료/주의사항

- 브랜치 `m4.5-usability`를 `main`에 합칠지 사용자 확인 필요.
- 실기기 수동 확인 필요: 1366×768·FHD 125%/150%·Retina 화면 배율, 옛 버전(스키마 2) 백업을 새 버전에서 복원하는 왕복, 큰 DB에서 종료 시 자동 백업 시간.
- `support.json`의 판매자 문의 문구는 판매 전 사용자 확인 필요. 사용 안내 페이지가 생기면 `manual_url`(https만) 입력.
- 판매 페이지·설치 안내서·PRD 본문 용어를 U1의 새 용어로 맞추는 문서 작업이 남음.
- M4의 미완료 사항(Developer ID 서명·공증, Windows 빌드 `build_windows.ps1`/`.iss` 미실행, Inno Setup `Korean.isl` 확인, 번들 ID `com.example.rentaldesk` 확정)은 그대로.
- 장비 사진·QR, 장비 세트, 암호화 백업, 고객 목록 엑셀, 부분 출고는 여전히 P1(구현하지 않음).
- U10 손 확인 필요: Windows Excel·Mac Numbers·한셀에서 저장한 xlsx를 실제로 가져와 보기(테스트는 openpyxl로 만든 파일만 사용).
