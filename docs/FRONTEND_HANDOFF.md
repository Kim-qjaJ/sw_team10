# 담당 4 프론트엔드 1차 구현 보고서

작성일: 2026-10-13. 작업 브랜치: `feature/frontend-ux-map`.
시작 당시 작업 트리는 깨끗했고 기존 화면 3개는 빈 파일이었다. 백엔드, 공용 Schema, 기존 Python 테스트는 변경하지 않았다. 최초 구현 완료 당시 commit / push / PR은 실행하지 않았다. 이후 제출 결정은 아래 2026-10-14 기록을 참고한다.

## A. 구현 완료한 기능

- FastAPI의 기존 `/`와 `/static` 경로로 서비스하는 HTML/CSS/Vanilla JavaScript 화면.
- 검색 / 추천 / 주변 시설 3개 모드, 추천 카테고리, 결과 수(5/10/20), 자연어 입력(1~500자).
- Enter 전송, Shift+Enter 줄바꿈, 한국어 IME 조합 중 Enter 보호.
- 실제 `POST /api/v1/recommend` 연동. `results[].places`를 요청별 카드 그룹으로 표시.
- 중복 제출 차단, 45초 요청 제한, 연결 실패·429·서버 오류·잘못된 JSON/응답 구조·0건 안내와 재시도.
- 서버 안내, notice, suggestions의 message/mode, 안전한 links 표시.
- 무작위 session_id만 sessionStorage에 저장. 저장 차단 환경에서는 메모리에 보관. 질문·좌표·사진은 브라우저 영구 저장소에 저장하지 않음.
- 위치 재요청·해제, 권한 거부·시간 초과 처리. 좌표는 실제 Geolocation 반환값만 요청에 포함. 부산 제한은 서버가 판단한다. 위치 해제는 새 session_id를 발급해 이전 서버 세션의 origin 참조를 막는다(서버 기록 삭제 API는 아님).
- Leaflet 1.9.4 로컬 SDK와 OpenStreetMap 지도. 서버의 유효 좌표만 마커로 표시. 카드/버튼 → 지도 이동, 마커 → 팝업 및 카드 강조. 마커 Enter/Space 지원.
- 좌표 누락 안내, 지도 프로그램 및 타일 실패 안내. 지도 장애 중에도 API 검색 가능.
- JPG/PNG/WebP 최대 5MB 파일 선택, 디코딩 확인, 미리보기, 제거, 재선택. 사진은 전송되지 않으며 분석 버튼은 준비 중으로 비활성화.
- PC/모바일 화면, 입력 레이블, 키보드 포커스, 상태 알림, 외부 URL의 http/https 제한, textContent 기반 XSS 방어.
- 기본 점수 0은 평가 점수로 표시하지 않음. 계산 가능한 score_breakdown 근거가 있을 때만 추천 점수 표시. 서버의 현재 “점수순” 안내에 대해서는 계산 근거 미제공 안내를 함께 표시.

### 현재 계약과 임시 결정 구분

**현행 계약:** `app/schemas/api.py`, `place.py`, `intent.py`와 실제 chat/service 흐름이 기준이다. 공용 계약은 바꾸지 않았다.

**프론트엔드 임시 결정:** “부산 한걸음” 서비스명, 블루 디자인, 기본 결과 5개, 45초 클라이언트 타임아웃, 사진 5MB 제한, 사진 형식 3종, Leaflet/OSM 선택은 확정 SRS가 아니다. 팀 협의 후 변경 가능하다.

**서버 미지원:** 이미지 업로드/분석 엔드포인트, 실제 추천 점수 계산, 실제 외부 장소 Provider 통합. UI가 이 기능을 수행한다고 표시하지 않는다.

## B. 수정하거나 새로 만든 파일

| 파일 | 내용 |
|---|---|
| `app/static/index.html` | 검색·모드·GPS·사진·지도·결과 화면 구조 |
| `app/static/style.css` | 블루 디자인, PC/모바일 레이아웃, 포커스/상태 스타일 |
| `app/static/app.js` | API 요청, 세션, GPS, 검증, 결과 카드, 안내, 후속 질문 |
| `app/static/ui-utils.js` | 안전한 DOM/링크 생성, 좌표 및 응답 검증 |
| `app/static/map.js` | 지도 초기화, 타일 실패 처리, 좌표 마커, 카드 연동 |
| `app/static/image-upload.js` | 로컬 사진 미리보기, 검증, URL 해제, 향후 분석 연결 지점 |
| `app/static/vendor/leaflet/leaflet.js`, `leaflet.css`, `LICENSE` | Leaflet 1.9.4 배포본과 BSD-2-Clause 라이선스 (CSS/라이선스 줄바꿈·줄 끝 공백 정리) |
| `frontend-tests/smoke.py` | 별도 프론트엔드 브라우저 검증; 기존 tests는 그대로 보존 |
| `docs/FRONTEND_HANDOFF.md` | 이 보고서, 실행/제출 안내와 미확정 항목 |

Leaflet 파일은 unpkg의 `leaflet@1.9.4` 배포본에서 받았다. SDK를 로컬 제공하여 CDN 장애 영향을 줄였다. 기본 핀 이미지 대신 CircleMarker를 사용하므로 별도 marker PNG는 필요 없다.

## C. 테스트 결과

### 실제 실행한 명령

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt pytest
USE_MOCK_PLACES=true .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
.venv/bin/python -m pytest -q
.venv/bin/python -m pip install playwright
.venv/bin/python -m playwright install chromium
.venv/bin/python frontend-tests/smoke.py
git diff --check
```

- 기존 Python 테스트: **14 passed**.
- 실제 서버 HTTP 검사: 아래 4개 질의 모두 **200**, `mock_data=true`.

| mode | 질문 | 장소 수 | route |
|---|---|---:|---|
| recommend | 서면 카페 추천해줘 | 3 | local |
| recommend | 해운대 맛집 알려줘 | 4 | local |
| search | 서면 카페 | 3 | api |
| nearby | 서면역 약국 | 5 | api |

위 결과는 이 저장소 MockProvider의 현행 출력이며 실재 장소가 아니다. 결과 수는 상한값으로, 항상 5개를 보장하지 않는다. 이 질의들은 LLM 키 없이 테스트했다.

브라우저 자동 테스트: 실제 서버 질의, 입력 검증, Enter/줄바꿈, 세션 유지, 후속 질문, 카드/지도 팝업, 마커 키보드, 사진 미리보기/제거 및 형식·크기·깨진 이미지, 다중 결과 그룹, XSS 문자열/위험 URL 차단, 좌표 없음, HTTP 429/500, 잘못된 JSON/Schema, 연결 실패, 0건, 응답 시간 초과, 중복 제출, GPS 성공/거부/시간 초과/해제, 모바일 가로 넘침, SDK 로드 실패 중 검색, category 전달을 확인했다.

지도 자동 테스트는 OSM 타일 요청을 차단한다. 타일 실패와 마커 동작을 검증하면서 공용 타일 서버에 반복 부하를 주지 않기 위해서다. 실제 OSM 배경과 샘플 좌표 마커는 별도로 Codex 브라우저에서 육안 확인했다.

### 미검증 / 한계

- 실제 기기의 OS/브라우저 GPS 권한창, 실측 위치 정확도: 자동 테스트에서는 Geolocation 성공/오류 콜백을 대체했다. 실제 개인 위치는 수집하지 않았다.
- 실기기 Safari/Android 터치 조작과 스크린리더 전체 검사: 미실시. 모바일 테스트는 Chromium viewport 320/390px이다.
- 실제 외부 Provider 정보·이동시간·점수의 정확도, LLM 키가 필요한 모호한 질문: 담당 기능 미완성/키 미사용으로 미검증.
- 이미지 업로드 및 AI 분석: 서버 엔드포인트가 없어 미구현, 미검증.
- 연결 실패는 브라우저 요청 차단으로 재현했다. 테스트 중 실행 서버를 강제 종료하지 않았다.

## D. 다른 팀원의 작업이 필요한 부분

### 담당 1 — AI / 백엔드 / 통합

- 이미지 API 경로, HTTP 방식, multipart 필드명, 파일 종류·크기, 응답/오류 Schema, 동기/비동기 및 진행 조회 방법 합의.
- 업로드 파일 보관/삭제 정책, 이미지 분석 후 추천 요청으로 이어지는 방식 합의.
- 추천 계산 미구현 상태에서 “점수순”이라고 나오는 `ResponseWriter.template_summary` 문구 개선 요청. 현재 프론트엔드는 원문을 보존하고 보충 안내한다.
- 서버 응답 지연 최대치와 프론트엔드 45초 제한 조율. 이전 서버 세션 즉시 삭제가 필요하면 별도 API 협의(현재는 새 ID로 참조만 중단).

### 담당 2 — 외부 API

- 기존 Place 계약에 맞춘 실제 장소 이름/주소/좌표/URL/전화/출처 제공 및 Provider 연결.
- 누락값은 null 유지. 실제 설정 완료 후 `USE_MOCK_PLACES=false`에서 통합 확인.
- 이동시간 정보를 제공한다면 출처, 단위(분), 최신성 확인.

### 담당 3 — 추천 알고리즘

- score, rank_score, data_confidence, score_breakdown 계산 및 정렬 구현.
- 점수의 범위·의미·계산 여부 판별 기준 협의. 현재는 유효한 세부 점수(`available=true`, 숫자 score)가 있어야 총 추천 점수를 표시한다. 0점 자체를 실패로 처리하지 않는다.

## E. GitHub 제출 방법

아래는 수동 제출 시 사용할 명령 예시다. 2026-10-14 사용자가 1차 작업분 commit/push를 승인했다. PR 생성 및 main 병합은 이번 제출 범위에 포함하지 않는다.

```bash
cd /Users/imhoon/Desktop/sw_team10
git branch --show-current
git status --short
git diff -- app/static
git diff --check
# 추가된 파일도 에디터로 검토한 뒤 지정 경로만 올립니다.
git add app/static/index.html app/static/style.css app/static/app.js app/static/ui-utils.js app/static/map.js app/static/image-upload.js app/static/vendor/leaflet frontend-tests/smoke.py docs/FRONTEND_HANDOFF.md
git diff --cached --stat
git diff --cached --check
git commit -m "feat: implement Busan frontend search, map and image preview"
git push -u origin feature/frontend-ux-map
```

GitHub 저장소에서 Compare & pull request를 누른다. base는 팀 통합 브랜치(현재 main 사용 여부 담당 1 확인), compare는 feature/frontend-ux-map이다.

추천 PR 제목: `담당 4: 장소 검색·GPS·지도·사진 미리보기 UI 구현`

본문에는 다음을 적는다.

> 빈 프론트엔드에 FastAPI 추천 API와 연결되는 검색/추천/주변 시설 화면을 구현했습니다. 결과를 요청별로 표시하고, 서버 좌표를 지도와 연결합니다. 사진은 로컬 미리보기만 지원하며 분석 준비 중임을 표시합니다. 백엔드·공용 Schema는 변경하지 않았습니다.
>
> 검증: Python 14개 테스트, 실제 Mock 서버 질의 4종, 별도 브라우저 오류/보안/모바일 테스트 통과. 실제 GPS 권한창 및 실기기 터치, 이미지 분석, 실제 Provider 정확도는 미검증입니다.

`.env`, API 키, `.venv`, 개인 사진은 제출하지 않는다. 기존 .gitignore는 변경하지 않았다.

## F. 실행 방법

Mac 터미널:

```bash
cd /Users/imhoon/Desktop/sw_team10
# 처음 설치하는 환경에서만:
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
# 키 없는 샘플 모드. 기존 .env를 덮어쓰지 않습니다.
USE_MOCK_PLACES=true python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

화면: http://127.0.0.1:8000
API 문서: http://127.0.0.1:8000/docs
서버 확인: http://127.0.0.1:8000/health

서버가 이미 8000 포트에서 실행 중이라면 중복 실행하지 말고 화면을 열면 된다. 새 터미널에서 서버를 시작한 경우 종료는 Ctrl+C. HTML 파일을 Finder에서 직접 열지 말고 위 서버 URL로 접속한다.

추천 카테고리 “질문에서 자동 선택” 상태에서 “서면 카페 추천해줘”, “해운대 맛집 알려줘”를 입력하면 키 없이 결과를 확인할 수 있다. 위치를 사용하지 않을 때 주변 모드에는 “서면역 약국”처럼 기준 지역을 함께 쓴다.

브라우저 테스트를 다시 실행할 때 서버를 켜 둔 채 새 터미널에서:

```bash
cd /Users/imhoon/Desktop/sw_team10
.venv/bin/python -m pip install pytest playwright
.venv/bin/python -m playwright install chromium
.venv/bin/python -m pytest -q
.venv/bin/python frontend-tests/smoke.py
```

## G. 추가로 필요한 정보

- 현재 지도 API 키는 필요 없다. 타일 서버를 교체한다면 `map.js`의 TILE_URL과 attribution을 함께 바꾼다.
- OSM은 화면에 보이는 타일만 요청하고 브라우저 기본 캐시와 Referer를 유지한다. 저작자 표시를 숨기지 않는다. 오프라인 다운로드/타일 선행 수집 기능은 없다. 서비스 규모가 커지면 타일 공급자와 연락처 공개 방식을 검토한다.
- 공식 참고: [OSM 타일 정책](https://operations.osmfoundation.org/policies/tiles/), [Leaflet](https://leafletjs.com/).
- 확정 서비스명, 사진 API 계약, 사진 제한, 점수 표현, 타임아웃, 실제 배포 호스트와 HTTPS 설정은 팀과 협의해야 한다.

## 2026-10-13 디자인 리뉴얼

사용자가 제공한 `resortlux-DESIGN.md`를 참고해 기존 블루 UI를 아이보리(#FFFFF0)·네이비(#0F172A)·골드(#B8860B)로 변경했다. 전체 폭 광안대교 사진, 명조체 제목, 여백과 얇은 구분선 중심으로 정리하고 둥근 카드·배지를 줄였다. 골드 버튼의 작은 글자는 대비 확보를 위해 네이비로 표시한다. 작은 골드 텍스트는 어두운 파생 색상을 사용한다.

수정: index.html, style.css, app.js(문서 제목만), map.js(마커 색상만). 추가: images/gwangan.jpg, images/CREDITS.md. 사진은 소개용이며 검색 결과의 사진으로 사용하지 않는다. `AI 안내`는 `검색 안내`로 변경했다. 백엔드/API 계약은 그대로다.

검증: `frontend-tests/smoke.py` 전체 통과, `git diff --check` 통과. 실제 브라우저에서 PC와 모바일 배치 확인. 이미지 출처와 라이선스는 images/CREDITS.md 참고. 리뉴얼 완료 당시에는 commit/push하지 않았다.


## 2026-10-14 팀 피드백 및 1차 제출 결정

- 팀 피드백: “사이트를 만들기보다는 챗봇에 중점을 두고 GUI는 단순하게 해도 될 것 같다.”
- 합의: 현재 구현을 1차 작업분으로 feature/frontend-ux-map 브랜치에 커밋·푸시하고, 실제 기능이 연결된 후 확인하면서 수정한다.
- 후속 작업: AI/장소 API/추천 알고리즘 연동 상태 확인, 챗봇 대화 흐름을 중심으로 화면 구성 단순화. 현재 화면을 최종 디자인으로 확정하지 않는다.
- 이번 제출에서는 백엔드 변경, UI 재설계, PR 생성, main 병합을 하지 않는다.
