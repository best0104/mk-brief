#!/usr/bin/env python3
"""카카오 refresh token 최초 발급 도우미 (내 PC에서 한 번만 실행).

사용법:
  python kakao_auth.py <REST_API_KEY> <REDIRECT_URI> [CLIENT_SECRET]
1) 출력된 주소를 브라우저에서 열고 로그인·동의
2) 이동된 주소창의 ...?code=XXXX 에서 XXXX를 복사해 붙여넣기
3) 출력된 refresh_token을 GitHub Secret(KAKAO_REFRESH_TOKEN)에 저장
"""
import sys
import urllib.parse

import requests

if len(sys.argv) < 3:
    sys.exit(__doc__)
key, redirect = sys.argv[1], sys.argv[2]
secret = sys.argv[3] if len(sys.argv) > 3 else None

q = urllib.parse.urlencode({"client_id": key, "redirect_uri": redirect, "response_type": "code", "scope": "talk_message"})
print("\n아래 주소를 브라우저에서 여세요:\nhttps://kauth.kakao.com/oauth/authorize?" + q + "\n")
code = input("주소창의 code= 값을 붙여넣기: ").strip()

data = {"grant_type": "authorization_code", "client_id": key, "redirect_uri": redirect, "code": code}
if secret:
    data["client_secret"] = secret
r = requests.post("https://kauth.kakao.com/oauth/token", data=data, timeout=20)
print(r.status_code)
j = r.json()
if "refresh_token" in j:
    print("\nKAKAO_REFRESH_TOKEN =", j["refresh_token"])
    print("(유효기간 약", j.get("refresh_token_expires_in", 0) // 86400, "일)")
else:
    print(j)
