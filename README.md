# 제휴 마케팅 자동화

트렌드 키워드 → 상품 매칭 → 제휴 링크 → 추천 문구 → 검수·복사·발행 → 성과 기록

- 프론트엔드: HTML / CSS / JavaScript (`frontend/`)
- 백엔드: Python (`backend/`)
- 서버: Flask (`app.py`)

## 설치 (최초 1회)

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env   # 그 다음 .env 에 키 입력
```

## 실행

```powershell
.\.venv\Scripts\python.exe app.py
```

브라우저에서 http://127.0.0.1:5000 접속.

## API

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/api/status` | 수집기별 준비 상태(키 유무, Mock 여부) |
| POST | `/api/collect` | 1단계 트렌드 수집 실행 후 결과 반환 |
| GET | `/api/trends/latest` | 마지막 수집 결과 |

## 폴더 구조

```
app.py                 Flask 서버 (정적 파일 + API)
config/settings.yaml   동작 설정
.env                   API 키 (공유 금지)
backend/
  settings.py          설정·환경변수 로더
  collectors/          1단계 수집기 (Collector.fetch() -> list[Trend])
  affiliates/          제휴 플랫폼 API 클라이언트 (쿠팡 HMAC 등)
  services/            단계별 비즈니스 로직
  storage/db.py        SQLite (data/marketing.db)
frontend/
  index.html
  css/style.css
  js/app.js
fixtures/              키가 없을 때 쓰는 Mock 데이터
```

## 진행 상황

| 단계 | 상태 |
|---|---|
| 1. 트렌드 수집 | 구글 트렌드 ✅ / 네이버 데이터랩 (키 필요) / 쿠팡 골드박스 (키 없으면 Mock) |
| 2. 필터·상품 매칭 | 예정 |
| 3. 제휴 링크 생성 | 예정 |
| 4. 추천 문구 생성 | 예정 (Claude API) |
| 5. 검수·복사·발행 | 예정 |
| 6. 성과 기록 | 예정 |
