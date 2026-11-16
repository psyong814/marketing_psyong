# 제휴 마케팅 자동화 (카카오 선물하기 큐레이터 기반)

트렌드 키워드 → 필터·상품 매칭 → 제휴 링크 확정 → 추천 문구 → 검수·복사·발행 → 성과 기록

- 프론트엔드: HTML / CSS / JavaScript (`frontend/`)
- 백엔드: Python (`backend/`), 서버: Flask (`app.py`)
- 제휴 상품·링크: **카카오 선물하기 큐레이터** (브라우저 자동화로 수집 → SQLite)
- LLM: Google AI Studio (Gemini API 무료 등급) — 키가 없으면 규칙 기반으로 동작
- 네이버 API, Claude API 는 사용하지 않음
- 쿠팡 파트너스는 API 승인 후 추가 예정 (코드: `backend/affiliates/coupang_client.py`, `backend/collectors/coupang_goldbox.py` — 현재 연결 안 됨)

## 설치 (최초 1회)

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

키·계정은 화면 왼쪽 메뉴 **⚙ 시스템 설정**에서 입력합니다.

## 실행

```powershell
.\.venv\Scripts\python.exe app.py
```

브라우저에서 http://127.0.0.1:5000 접속.
브라우저 자동화 때문에 서버 자동 재시작(`server.reloader`)은 기본으로 꺼져 있습니다. 코드를 바꾸면 서버를 직접 다시 실행하세요.

## 계정 · 키

| 항목 | 용도 | 저장 위치 | 없으면 |
|---|---|---|---|
| Gemini API 키 | 2단계 트렌드 키워드 판별, 4단계 문구 | `.env` | 규칙 기반 |
| 카카오 아이디 / 비밀번호 | 선물하기 로그인 → 큐레이터 링크 수집 | `data/kakao_settings.json` / Windows 자격 증명 관리자(`marketing_kakao`) | 수집 불가 (가져오기는 가능) |

## 카카오 선물하기 큐레이터 (🎁 카카오 상품)

1. ⚙ 시스템 설정 → **카카오 선물하기** 카드에 아이디·비밀번호 저장
2. 🎁 카카오 상품 → **▶ 실행**: 크롬이 열려 선물하기 로그인 → 큐레이터 → 모바일 전환 → 상품별 [링크 생성] 수집
   - 카카오톡 승인·보안문자가 나오면 열린 크롬 창에서 직접 처리
   - 개발자 도구 키 입력(F12 → Ctrl+Shift+M → F5) 중에는 키보드·마우스를 잠시 쓰지 마세요
   - 로그인 여부는 선물하기 상단의 계정 이름으로 판단, 모바일에서 401 이 나오면 자동으로 한 번 다시 로그인
3. 수집한 상품은 SQLite `kakao_products` 에 저장 → 1단계 수집 소스이자 2단계 매칭 대상
   - 태그(예: `수능, 수험생`)를 넣어두면 시즌·트렌드 키워드와 더 잘 연결됩니다
4. **예전 수집 결과 가져오기**: `config/settings.yaml` 의 `kakao.import_path` (results.json) 를 DB 로 가져옴

로그인 상태는 `data/kakao_profile/` 에 저장됩니다 (다른 사람에게 공유 금지).

## 단계별 처리

| 단계 | 내용 |
|---|---|
| 1. 트렌드 수집 | 구글 트렌드 RSS · 시즌 키워드 사전 · 카카오 상품(최근 구매자 수 순) |
| 2. 필터·상품 매칭 | 중복 제거 → 금칙어 제외 → 상품성 판별(시즌=가중치, 카카오=70~85점, 트렌드=Gemini) → 카카오 상품 매칭(이름·브랜드·태그) |
| 3. 제휴 링크 확정 | 키워드마다 카카오 상품 1개 선택(겹치는 상품은 점수 높은 키워드 우선) → 확정 체크. 점수 상위 `links.auto_select` 개는 미리 체크 |
| 4~6 | 예정 |

3단계는 링크를 자동으로 열어 확인하지 않습니다 (내 링크 클릭이 큐레이터 실적 통계를 왜곡할 수 있음). `https://gift-link.kakao.com/...` 형식만 확인합니다.

## 설정 파일

| 파일 | 내용 |
|---|---|
| `config/settings.yaml` | 수집기 on/off, LLM 엔진·모델, 필터 기준 점수, 링크 자동 확정 개수 |
| `config/season_keywords.yaml` | 시즌별 키워드 사전 (기간·가중치·키워드) |
| `config/keyword_rules.yaml` | 금칙어, 규칙 기반 상품 단어 |

## API

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/api/status` | 수집기 준비 상태 + LLM 엔진 정보 |
| POST | `/api/collect` | 1단계 수집 |
| GET | `/api/trends/latest` | 마지막 수집 결과 |
| POST | `/api/filter` | 2단계 필터·매칭 |
| GET | `/api/filter/latest` | 마지막 분석 결과 |
| POST | `/api/links/build` | 3단계 링크 후보 만들기 (최근 분석 기준) |
| GET | `/api/links/latest` | 링크 후보·확정 상태 |
| POST | `/api/links/<id>` | 상품 변경 `{"product_id"}` / 확정 `{"selected"}` |
| GET/POST | `/api/kakao/...` | 카카오 설정·실행·수집·상품 관리 |
| GET/POST | `/api/settings`, `/api/settings/test/gemini` | Gemini 키 저장·연결 테스트 |

`/api/*` 는 로컬(127.0.0.1)에서 온 요청만 받고, 다른 사이트에서 보낸 POST 는 차단합니다.

## 폴더 구조

```
app.py                     Flask 서버 (정적 파일 + API)
config/                    설정·사전 (YAML)
.env                       Gemini API 키 (공유 금지)
backend/
  settings.py, env_store.py, rules.py
  collectors/              1단계 수집기 (구글 트렌드, 시즌 키워드, 카카오 상품)
  kakao/                   카카오 선물하기 자동화 (로그인·큐레이터·링크 수집)
  llm/                     판별 엔진 (rule / gemini)
  affiliates/              카카오 상품 검색 (+ 미사용 쿠팡 클라이언트)
  services/                collect / filter / link / settings
  storage/db.py            SQLite (data/marketing.db)
frontend/                  index.html, css/, js/
```

## DB 테이블

| 테이블 | 내용 |
|---|---|
| `runs`, `trends` | 1단계 수집 회차 / 키워드 |
| `analyses`, `keywords`, `products` | 2단계 분석 회차 / 키워드 판별 / 매칭 상품 |
| `links` | 3단계 키워드별 홍보 상품·링크·확정 여부 |
| `kakao_products` | 카카오 선물하기 큐레이터 상품·공유 링크·태그 |
| `llm_cache` | Gemini 판별 결과 캐시 |
