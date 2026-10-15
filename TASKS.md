# RentDesk Lite — 개발 작업 체크리스트

> P0만 먼저 수행. 체크박스는 실제 테스트가 통과한 후 변경한다.

## M0. 개발 환경 / 규칙
- [x] PRD/CLAUDE 파일 확인 및 아키텍처 결정 기록 (`OPEN_QUESTIONS.md`)
- [x] **맥북에서** Python 3.12 가상환경, 의존성 잠금, pytest 설정
- [x] macOS arm64 / Windows 11 x64 지원 범위·최소 OS 버전 검증 계획 확정 (`OPEN_QUESTIONS.md`)
- [x] OS 경로 어댑터(`QStandardPaths`), OS 정보/라이선스 기기 ID 어댑터 인터페이스 정의
- [x] Windows 네이티브 빌드 환경(Windows PC/VM 또는 CI Windows runner) 준비 — GitHub Actions `windows-latest` + `macos-14`, CI 실제 통과 확인
- [x] 초기 패키지 구조·메인 창 생성
- [x] 샘플 데이터 및 데모 모드 계획 (`OPEN_QUESTIONS.md`, 구현은 M1 이후)

## M1. 데이터 / 예약 코어
- [x] SQLite 연결·FK 활성화·마이그레이션·트랜잭션 헬퍼
- [x] assets/categories/customers CRUD
- [x] rental_orders/rental_items 데이터형·리포지토리
- [x] availability 시간 겹침·버퍼·정비 블록·연체 미반납
- [x] 예약 임시저장/확정/수정/취소
- [x] T-01~T-06, T-13~T-14 통과 (macOS arm64 로컬 확인. Windows 검증은 전체 완료 후 일괄)

## M2. 출고 / 반납 / 정산
- [x] 일 대여료·할인·연체료 `Decimal` 계산
- [x] 출고 원자적 상태 전이 및 청구 원장
- [x] 장비 단위 부분 반납과 중복 반납 방지
- [x] 반납 시 정비 블록과 미래 예약 영향 경고
- [x] 금전 원장·보증금 충당/반환·정산 잔액 계산
- [x] T-07~T-12, T-15~T-18, T-23~T-24 통과 (macOS arm64 로컬 확인. Windows 검증은 전체 완료 후 일괄)

## M3. UI 및 엑셀
- [ ] 대시보드 S-01
- [ ] 장비/고객 화면 S-02~S-03
- [ ] 예약 등록/상세/목록 S-04
- [ ] 출고/부분 반납/정산 S-05~S-07
- [ ] 정비/설정/보고서 S-08~S-09
- [ ] 5개 표준 엑셀 보고서
- [ ] 에러/빈 상태/필터/검색 사용성 점검
- [ ] macOS Retina와 Windows DPI(100/150%), 한글 경로·UI 단축키·파일 선택창 점검

## M4. 백업 / 라이선스 / macOS + Windows 패키징
- [ ] 온라인 백업 API 기반 백업·복원·무결성 검사
- [ ] 백업 파일 손상 시 기존 DB 보존 검증
- [ ] Ed25519 라이선스 생성·검증·판매자 개인키 분리
- [ ] Mac/Windows 사용자 데이터 경로·로그 보호 및 QStandardPaths 테스트
- [ ] macOS Apple Silicon `.app` + `.dmg` 빌드 스크립트
- [ ] macOS Developer ID 코드서명 / Hardened Runtime / 공증·검증 단계 구현(배포용)
- [ ] Windows용 PyInstaller one-dir 및 Inno Setup 설치 도구 스크립트, **Windows에서 빌드**
- [ ] Python 미설치 macOS/Windows에서 각각 설치/실행/제거 검증
- [ ] Mac ↔ Windows 백업/복원 호환 및 OS별 라이선스 이전 절차 검증
- [ ] T-19~T-22, T-25, CP-01~CP-10 통과

## M5. 출시
- [ ] 500장비/3000고객/10000예약 규모 테스트
- [ ] Windows 1366×768, macOS Retina/논리 해상도 주요 화면 확인
- [ ] 실제 업체 담당자 2~3명 피드백 반영
- [ ] 사용 설명서·빠른 시작 안내·샘플 데이터
- [ ] 오픈소스 라이선스 고지 및 이용조건 검토
- [ ] 15장 Definition of Done 전체 확인
- [ ] macOS arm64 / Windows x64 **각각** 지원 매트릭스·스모크 테스트 결과 기록
- [ ] Windows `setup.exe`, Mac 서명·공증된 `.dmg` 동시 릴리스 1.0.0 기록

## P1 아이디어 백로그(지금 구현 금지)
- [ ] 주간/월간 캘린더
- [ ] 장비 사진·QR/바코드
- [ ] 엑셀 일괄 가져오기
- [ ] PDF 견적/대여확인서
- [ ] 연장/선결제/예약금
- [ ] 장비 세트/액세서리
- [ ] 암호화 백업/자동백업
- [ ] 요금제 세분화
