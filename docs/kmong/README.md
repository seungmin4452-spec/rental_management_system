# 크몽 이미지

| 파일 | 크기 | 내용 |
|---|---|---|
| `메인_652x488.png` | 652×488 | 메인(썸네일). 파란 배경 + 제목 + 대시보드 화면 |
| `상세_01.png` | 860 가로 | 렌탈샵 고민 4가지 → 해결(1회 구매·오프라인·내 PC 저장) |
| `상세_02.png` | 〃 | 대시보드 |
| `상세_03.png` | 〃 | 예약 달력 · 중복 예약 차단 |
| `상세_04.png` | 〃 | 반납 · 연체료 계산 근거 · 추가 비용 |
| `상세_05.png` | 〃 | 대여확인서 PDF · 인쇄 |
| `상세_06.png` | 〃 | 주요 기능 6묶음 |
| `상세_07.png` | 〃 | 패키지 가격 · 구매 후 순서 · 지원 환경 |

앱 화면은 모두 실제 프로그램을 **예제 데이터**로 띄워 찍었다(이미지에 "화면은 예제 데이터입니다" 표기). 가격·패키지가 바뀌면 `상세_07`을, 기능이 바뀌면 `상세_06`을 다시 만든다. 문구 원문은 `docs/kmong_listing.md`.

## 다시 만드는 방법 (Windows, Git Bash)

```bash
# 1) 예제 데이터로 앱 화면 캡처 (QT_SCALE_FACTOR=1.5 → 2880×1800)
.venv/Scripts/python.exe docs/kmong/source/shots.py <캡처폴더>
# 2) 이미지에 쓸 부분만 잘라 source/img/ 에 저장
.venv/Scripts/python.exe docs/kmong/source/crop.py crop <캡처폴더> docs/kmong/source/img
# 3) HTML → PNG (Chrome 헤드리스, 2배 해상도). 상세는 섹션마다 #s1~#s7
K=$(cygpath -m "$PWD/docs/kmong/source")
"/c/Program Files/Google/Chrome/Application/chrome.exe" --headless=new --disable-gpu --hide-scrollbars \
  --force-device-scale-factor=2 --window-size=652,488 --screenshot="$(cygpath -w "$PWD")\\main@2x.png" "file:///$K/main.html"
"/c/Program Files/Google/Chrome/Application/chrome.exe" --headless=new --disable-gpu --hide-scrollbars \
  --force-device-scale-factor=2 --window-size=860,3000 --screenshot="$(cygpath -w "$PWD")\\raw_s7.png" "file:///$K/detail.html#s7"
# 4) 메인은 652×488로 줄이고, 상세는 아래 자홍색 여백을 잘라 가로 860으로 줄인다
.venv/Scripts/python.exe docs/kmong/source/crop.py resize main@2x.png docs/kmong/메인_652x488.png 652 488
.venv/Scripts/python.exe docs/kmong/source/crop.py trim raw_s7.png docs/kmong/상세_07.png
```

- `detail.html`의 `body` 배경 자홍색(#FF00FF)은 잘라내기 표시용이다. 바꾸지 않는다.
- 이미지 문구에 크몽 금지 표현(무료, n% 할인, 최저가, 최고·1위, 외부 연락처)을 넣지 않는다.
