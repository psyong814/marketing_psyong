"""카카오 선물하기 큐레이터 링크 수집 (earning_kakao 프로젝트에서 이식).

- automation.py   : Playwright 로 선물하기 로그인 → 큐레이터 → 모바일 전환
- collector.py    : 상품별 [링크 생성] 클릭 → 공유 링크 + 상품 정보 수집
- devtools_keys.py: 크롬 창에 F12 / Ctrl+Shift+M / F5 키 입력, 클립보드 읽기 (Windows)
- settings_store.py: 아이디·옵션(data/kakao_settings.json) + 비밀번호(Windows 자격 증명 관리자)
- service.py      : Flask 에서 쓰는 실행·상태·로그 관리
"""
