# 매일경제 AI 모닝 브리핑 (매일 아침 7시 카톡 발송)

매일 06:30(KST)에 GitHub Actions가 돌면서
1. 매일경제 RSS(경제·증권·부동산·IT)를 수집하고
2. Claude가 ①경제 ②주식 ③부동산 ④IT ⑤TOP5 형식으로 정리해
3. 전체 브리핑을 웹페이지(GitHub Pages)로 게시한 뒤
4. 카카오톡 "나에게 보내기"로 TOP5 요약 + [전체 브리핑 보기] 버튼을 보냅니다. (7시 전후 도착)

> 카톡 기본 텍스트 메시지는 200자 제한이라, 전체 내용은 링크 페이지로 보여줍니다.

## 설정 순서 (약 20분, 최초 1회)

### 1. GitHub 저장소
1. 새 저장소 생성(Public) 후 이 폴더 내용을 전부 push
2. Settings → Pages → Source: `Deploy from a branch`, Branch: `main`, Folder: `/docs`

### 2. Kakao Developers
1. https://developers.kakao.com → 애플리케이션 추가
2. 앱 설정 → 플랫폼 → **Web 사이트 도메인**에 `https://<GitHub아이디>.github.io` 등록 (메시지 버튼 링크 허용에 필요)
3. 제품 설정 → 카카오 로그인 → 활성화, **Redirect URI** 등록 (예: `http://localhost:3000`)
4. 동의항목 → **카카오톡 메시지 전송(talk_message)** 선택 동의로 설정
5. REST API 키 확인 (보안 탭에서 Client Secret을 켰다면 그 값도)
6. 내 PC에서 한 번 실행:
   ```
   pip install requests
   python kakao_auth.py <REST_API_KEY> <REDIRECT_URI> [CLIENT_SECRET]
   ```
   출력된 주소 접속 → 동의 → 주소창의 `code=` 값 붙여넣기 → `refresh_token` 출력

### 3. GitHub Secrets (Settings → Secrets and variables → Actions)
| 이름 | 값 |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic API 키 (console.anthropic.com) |
| `KAKAO_REST_API_KEY` | 카카오 REST API 키 |
| `KAKAO_REFRESH_TOKEN` | 위에서 받은 refresh token |
| `KAKAO_CLIENT_SECRET` | (사용하는 경우만) |
| `GH_PAT` | (선택) repo 권한 토큰 — refresh token 자동 갱신용 |

### 4. RSS 주소 확인 ⚠️
`feeds.json`의 RSS 주소는 매일경제 RSS 페이지(https://www.mk.co.kr/rss)에서 각 섹션 주소를 확인해 맞게 수정하세요.
(제작 환경에서 해당 사이트에 접속할 수 없어 주소를 직접 검증하지 못했습니다.)

### 5. 테스트
Actions 탭 → morning-briefing → Run workflow. 로그의 `[수집]` 줄에서 섹션별 기사 수를 확인하고 카톡 도착을 확인하세요.

## 로컬 미리보기
```
python briefing.py build --sample     # docs/ 에 샘플 화면 생성 (API 불필요)
python briefing.py send --dry-run     # 보낼 카톡 내용 확인
```

## 알아둘 점
- GitHub 스케줄은 수 분~수십 분 늦을 수 있어 06:30에 시작합니다. 더 정확히 맞추려면 cron을 앞당기세요.
- refresh token 유효기간은 약 2개월입니다. `GH_PAT`을 넣으면 자동 갱신되고, 없으면 2개월마다 `kakao_auth.py`를 다시 실행하세요. 실패 시 GitHub가 실패 메일을 보냅니다.
- Public 저장소이므로 브리핑 페이지는 URL을 아는 사람이 볼 수 있습니다(검색 노출 차단 meta 포함). 기사 전문이 아닌 AI 요약과 원문 링크만 담깁니다.
- 요약은 RSS의 제목·요약문 기반이라 기사 본문 수준의 디테일은 아닙니다. 투자 판단은 원문 확인 후 하세요.
