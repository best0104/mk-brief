#!/usr/bin/env python3
"""매일경제 AI 모닝 브리핑.

  python briefing.py build [--sample]   RSS 수집 → Claude 요약 → docs/ 에 HTML 생성
  python briefing.py send  [--dry-run]  카카오톡 '나에게 보내기'로 요약 + 링크 전송
"""
import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

KST = ZoneInfo("Asia/Seoul")
ROOT = Path(__file__).parent
OUT = ROOT / "out"
DOCS = ROOT / "docs"
UA = "Mozilla/5.0 (compatible; MorningBriefing/1.0)"
WEEKDAYS = "월화수목금토일"

SECTIONS = [
    ("economy", "① 경제", ["금리 및 환율 주요 변화", "국내외 경기 흐름", "정부 경제정책 및 주요 이슈"]),
    ("stock", "② 주식", ["코스피·코스닥 주요 이슈", "반도체·AI·2차전지 관련 종목", "오늘 주목할 업종 및 투자 포인트"]),
    ("realestate", "③ 부동산", ["서울·수도권 아파트 시장", "부동산 정책 및 대출 규제", "금리와 부동산 시장 영향"]),
    ("it", "④ IT", ["AI 및 생성형 AI 기술", "반도체·클라우드·데이터센터", "주요 IT 기업 동향"]),
]


def today_kst():
    return dt.datetime.now(KST)


def date_label(d):
    return f"{d.year}년 {d.month}월 {d.day}일 ({WEEKDAYS[d.weekday()]})"


# ───────────────────────── 수집 ─────────────────────────
def strip_html(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def collect(per_section=10, hours=30):
    import feedparser

    feeds = json.loads((ROOT / "feeds.json").read_text(encoding="utf-8"))
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)
    result, total = {}, 0
    for key, label, _ in SECTIONS:
        items, seen = [], set()
        for url in feeds[key]["urls"]:
            try:
                r = requests.get(url, headers={"User-Agent": UA}, timeout=20)
                r.raise_for_status()
                parsed = feedparser.parse(r.content)
            except Exception as e:  # noqa: BLE001
                print(f"[경고] {label} 피드 실패 {url}: {e}", file=sys.stderr)
                continue
            print(f"[수집] {label} {url} → {len(parsed.entries)}건")
            for e in parsed.entries:
                link = e.get("link", "").strip()
                if not link or link in seen:
                    continue
                seen.add(link)
                ts = None
                if e.get("published_parsed"):
                    ts = dt.datetime(*e.published_parsed[:6], tzinfo=dt.timezone.utc)
                items.append({
                    "title": strip_html(e.get("title")),
                    "link": link,
                    "summary": strip_html(e.get("summary") or e.get("description"))[:400],
                    "ts": ts,
                })
        items.sort(key=lambda x: x["ts"] or dt.datetime.min.replace(tzinfo=dt.timezone.utc), reverse=True)
        recent = [i for i in items if i["ts"] and i["ts"] >= cutoff]
        chosen = (recent if len(recent) >= 5 else items)[:per_section]
        for i in chosen:
            i["ts"] = i["ts"].astimezone(KST).strftime("%m/%d %H:%M") if i["ts"] else ""
        result[key] = chosen
        total += len(chosen)
    if total < 5:
        sys.exit("기사를 5건 미만으로 수집했습니다. feeds.json의 RSS 주소를 확인하세요.")
    return result


# ───────────────────────── 요약 ─────────────────────────
SYSTEM = """당신은 한국 경제지 편집데스크 출신의 시장 분석가입니다. 아침 브리핑을 작성합니다.
규칙:
- 입력으로 주어진 기사 제목·요약에 있는 사실만 사용합니다. 없는 수치·종목·전망을 만들어내지 않습니다.
- 해당 항목에 관련 기사가 없으면 '관련 기사 없음'이라고 씁니다.
- 문장은 간결한 한국어(~다/~했다 체 또는 명사형)로 씁니다.
- TOP 5는 시장 파급력이 큰 순으로 중복 주제를 피해 고르고, 링크는 입력 기사의 link 값을 그대로 복사합니다.
- 투자 관점은 '무엇을 지켜볼지' 중심으로 쓰고 매수·매도 권유는 하지 않습니다.
- 출력은 JSON 하나만, 코드블록 없이 출력합니다."""


def build_prompt(articles):
    lines = []
    for key, label, topics in SECTIONS:
        lines.append(f"## {label} (key={key})")
        for i, a in enumerate(articles[key], 1):
            lines.append(f"{i}. [{a['ts']}] {a['title']}\n   요약: {a['summary']}\n   link: {a['link']}")
    schema = {
        "headline": "오늘 시장을 한 문장으로",
        "sections": {k: [{"topic": t, "text": "1~2문장"} for t in topics] for k, _, topics in SECTIONS},
        "top5": [{
            "rank": 1, "section": "economy|stock|realestate|it", "title": "기사 제목",
            "summary": ["핵심 1", "핵심 2", "핵심 3"],
            "market_impact": "시장에 미치는 영향 1~2문장",
            "investment_point": "투자 관점에서 주목할 점 1~2문장",
            "link": "입력 기사의 link",
        }],
    }
    return ("아래 기사 목록으로 아침 브리핑을 만드세요. 각 섹션의 topic 3개를 모두 채우고 top5는 정확히 5개입니다.\n\n"
            + "\n".join(lines) + "\n\n출력 JSON 형식:\n" + json.dumps(schema, ensure_ascii=False, indent=1))


GEMINI_FALLBACK_MODELS = ["gemini-3.1-flash-lite", "gemini-2.5-flash", "gemini-3.5-flash"]


def call_gemini(prompt):
    """Gemini REST API 호출. 모델이 없거나(404) 한도(429)에 걸리면 다음 모델로 넘어간다."""
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        sys.exit("GEMINI_API_KEY가 설정되지 않았습니다. GitHub Secrets에 등록하세요.")
    models = [m for m in [os.environ.get("GEMINI_MODEL")] + GEMINI_FALLBACK_MODELS if m]
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "maxOutputTokens": 8192, "temperature": 0.3},
    }
    last = ""
    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        r = None
        for attempt in range(2):
            try:
                r = requests.post(url, headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                                  json=body, timeout=90)
            except requests.RequestException as e:  # 시간 초과·연결 오류 → 재시도 후 다음 모델
                last = f"{model}: {type(e).__name__}"
                print(f"[경고] {last} (시도 {attempt + 1}/2)", file=sys.stderr)
                r = None
                time.sleep(10)
                continue
            if r.status_code in (429, 500, 503) and attempt < 1:
                time.sleep(20)
                continue
            break
        if r is None:
            continue
        if r.status_code == 200:
            try:
                parts = r.json()["candidates"][0]["content"]["parts"]
                text = "".join(p.get("text", "") for p in parts)
            except (KeyError, IndexError):
                last = f"{model}: 응답 형식 오류 {r.text[:300]}"
                print(f"[경고] {last}", file=sys.stderr)
                continue
            print(f"[요약] 사용 모델: {model}")
            return text
        last = f"{model}: HTTP {r.status_code} {r.text[:300]}"
        print(f"[경고] {last}", file=sys.stderr)
    sys.exit(f"Gemini 호출 실패 — 마지막 오류: {last}")


def summarize(articles):
    text = call_gemini(build_prompt(articles))
    data = json.loads(text[text.index("{"): text.rindex("}") + 1])
    allowed = {a["link"] for v in articles.values() for a in v}
    for t in data.get("top5", []):
        if t.get("link") not in allowed:
            print(f"[경고] 목록에 없는 링크 제거: {t.get('title')}", file=sys.stderr)
            t["link"] = ""
    return data


# ───────────────────────── HTML ─────────────────────────
CSS = """
:root{--bg:#f5f6f8;--card:#fff;--ink:#1a1d21;--sub:#5b6470;--line:#e4e7ec;--accent:#0b5fff}
@media(prefers-color-scheme:dark){:root{--bg:#111316;--card:#1b1e23;--ink:#eceef1;--sub:#9aa3af;--line:#2b3038;--accent:#6ea0ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.65 -apple-system,"Apple SD Gothic Neo","Noto Sans KR",sans-serif}
main{max-width:720px;margin:0 auto;padding:20px 16px 48px}
h1{font-size:22px;margin:8px 0 2px}.date{color:var(--sub);font-size:14px;margin-bottom:16px}
.headline{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--accent);border-radius:10px;padding:12px 14px;margin-bottom:16px}
section{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin-bottom:14px}
h2{font-size:17px;margin:0 0 8px}ul{margin:0;padding-left:0;list-style:none}li{padding:6px 0;border-top:1px solid var(--line)}li:first-child{border-top:0}
.topic{display:block;font-size:13px;font-weight:600;color:var(--sub)}
.n{display:inline-block;min-width:22px;color:var(--accent);font-weight:700}
.tag{font-size:12px;color:var(--sub);margin-left:6px}.sum{margin:6px 0 6px 0;padding-left:18px;list-style:disc}.sum li{border:0;padding:1px 0}
.k{font-weight:600}a{color:var(--accent)}.foot{color:var(--sub);font-size:12px;margin-top:20px}
"""


def e(s):
    return html.escape(str(s or ""))


def render_html(data, d):
    label = {k: l for k, l, _ in SECTIONS}
    parts = [f"<h1>매일경제 AI 모닝 브리핑</h1><div class='date'>{e(date_label(d))} · 오전 7:00</div>"]
    if data.get("headline"):
        parts.append(f"<div class='headline'>{e(data['headline'])}</div>")
    for key, title, _ in SECTIONS:
        lis = "".join(f"<li><span class='topic'>{e(b['topic'])}</span>{e(b['text'])}</li>" for b in data["sections"].get(key, []))
        parts.append(f"<section><h2>{e(title)}</h2><ul>{lis}</ul></section>")
    tops = []
    for t in data.get("top5", []):
        link = t.get("link", "")
        link_html = (f"<a href='{e(link)}' target='_blank' rel='noopener'>원문 기사 보기 ↗</a>"
                     if link.startswith("https://") else "")
        summ = "".join(f"<li>{e(x)}</li>" for x in t.get("summary", []))
        tops.append(
            f"<li><span class='n'>{e(t.get('rank'))}</span><b>{e(t.get('title'))}</b>"
            f"<span class='tag'>{e(label.get(t.get('section'), ''))}</span>"
            f"<ul class='sum'>{summ}</ul>"
            f"<div><span class='k'>시장 영향</span> {e(t.get('market_impact'))}</div>"
            f"<div><span class='k'>투자 포인트</span> {e(t.get('investment_point'))}</div>"
            f"<div>{link_html}</div></li>")
    parts.append(f"<section><h2>⑤ 오늘의 핵심 뉴스 TOP 5</h2><ul>{''.join(tops)}</ul></section>")
    parts.append("<div class='foot'>매일경제 RSS의 제목·요약을 AI가 정리한 자료이며 오류가 있을 수 있습니다. "
                 "투자 판단의 책임은 본인에게 있으며, 자세한 내용은 원문 기사를 확인하세요.</div>")
    return ("<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<meta name='robots' content='noindex,nofollow'>"
            f"<title>모닝 브리핑 {d:%Y-%m-%d}</title><style>{CSS}</style></head>"
            f"<body><main>{''.join(parts)}</main></body></html>")


# ───────────────────────── 카카오 메시지 ─────────────────────────
def clip(s, n):
    s = s.strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def digest_text(data, d):
    lines = [f"📰 매일경제 모닝브리핑 {d.month}/{d.day}({WEEKDAYS[d.weekday()]})", "🔥 TOP5"]
    for t in data.get("top5", [])[:5]:
        lines.append(f"{t.get('rank')}. {clip(t.get('title', ''), 24)}")
    text = "\n".join(lines)
    return clip(text, 200)  # 카카오 기본 텍스트 템플릿 한도: 200자


def page_url(d):
    base = os.environ.get("PAGES_BASE_URL", "").rstrip("/")
    return f"{base}/{d:%Y-%m-%d}.html" if base else ""


def env_clean(name):
    """Secret 앞뒤의 공백·줄바꿈·따옴표를 제거한다."""
    return os.environ.get(name, "").strip().strip("\"'").strip()


def kakao_access_token():
    key = env_clean("KAKAO_REST_API_KEY")
    secret = env_clean("KAKAO_CLIENT_SECRET")
    refresh = env_clean("KAKAO_REFRESH_TOKEN")
    data = {"grant_type": "refresh_token", "client_id": key, "refresh_token": refresh}
    if secret:
        data["client_secret"] = secret
    r = requests.post("https://kauth.kakao.com/oauth/token", data=data, timeout=20)
    if r.status_code != 200:
        # 값 자체는 출력하지 않고 길이만 보여 준다 (REST API 키·시크릿은 보통 32자)
        print(f"[진단] REST_API_KEY 길이={len(key)}, CLIENT_SECRET 길이={len(secret)}"
              f"{'(미설정)' if not secret else ''}, REFRESH_TOKEN 길이={len(refresh)}", file=sys.stderr)
        sys.exit(f"카카오 토큰 갱신 실패: {r.status_code} {r.text}\n→ 키/시크릿을 확인하거나 kakao_auth.py로 refresh token을 다시 발급하세요.")
    j = r.json()
    if j.get("refresh_token"):  # 만료가 가까우면 새 토큰이 내려온다
        (ROOT / "new_refresh_token.txt").write_text(j["refresh_token"])
        print("[알림] 새 refresh token 발급됨")
    return j["access_token"]


def wait_for_page(url, timeout=360):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if requests.get(url, timeout=10).status_code == 200:
                return True
        except requests.RequestException:
            pass
        time.sleep(15)
    return False


# ───────────────────────── 명령 ─────────────────────────
def cmd_build(args):
    d = today_kst()
    if args.sample:
        data = json.loads((ROOT / "sample_data.json").read_text(encoding="utf-8"))
    else:
        data = summarize(collect())
    OUT.mkdir(exist_ok=True)
    DOCS.mkdir(exist_ok=True)
    page = render_html(data, d)
    (DOCS / f"{d:%Y-%m-%d}.html").write_text(page, encoding="utf-8")
    (DOCS / "index.html").write_text(page, encoding="utf-8")
    (OUT / "digest.json").write_text(
        json.dumps({"text": digest_text(data, d), "date": f"{d:%Y-%m-%d}"}, ensure_ascii=False), encoding="utf-8")
    print(f"[완료] docs/{d:%Y-%m-%d}.html 생성")


def cmd_send(args):
    d = today_kst()
    digest = json.loads((OUT / "digest.json").read_text(encoding="utf-8"))
    url = page_url(d) or "https://www.mk.co.kr/"
    template = {
        "object_type": "text",
        "text": digest["text"],
        "link": {"web_url": url, "mobile_web_url": url},
        "button_title": "전체 브리핑 보기",
    }
    if args.dry_run:
        print(json.dumps(template, ensure_ascii=False, indent=2), f"\n(글자 수 {len(digest['text'])})")
        return
    if page_url(d) and not wait_for_page(url):
        print("[경고] 페이지가 아직 배포되지 않았지만 전송을 계속합니다.", file=sys.stderr)
    token = kakao_access_token()
    r = requests.post(
        "https://kapi.kakao.com/v2/api/talk/memo/default/send",
        headers={"Authorization": f"Bearer {token}"},
        data={"template_object": json.dumps(template, ensure_ascii=False)},
        timeout=20,
    )
    if r.status_code != 200 or r.json().get("result_code") != 0:
        sys.exit(f"카카오 전송 실패: {r.status_code} {r.text}")
    print("[완료] 카카오톡 전송 성공")


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--sample", action="store_true", help="네트워크·API 없이 샘플로 화면만 생성")
    b.set_defaults(fn=cmd_build)
    s = sub.add_parser("send")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_send)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
