"""네이버 검색 API 뉴스 크롤러

- Client ID/Secret으로 무료 발급 (일 25,000건)
- 정치인 키워드 기반 polling
- Tier 3 소스
"""
import os
import httpx
from datetime import datetime
from html import unescape
import re

from config import MEDIA_LEAN, media_from_url

NAVER_CLIENT_ID = os.environ.get("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "")

SEARCH_URL = "https://openapi.naver.com/v1/search/news.json"

# 정치 검색 키워드
POLITICAL_QUERIES = [
    "국회 법안 통과",
    "국회 본회의",
    "정치인 기소",
    "정치인 뇌물",
    "정치인 막말",
    "정치인 공약",
    "대통령 정책",
    "여당 야당",
    "국정감사",
    "탄핵",
]

# 사설/칼럼 필터
OPINION_KEYWORDS = [
    "사설", "칼럼", "오피니언", "시론", "논설", "기고", "해설",
    "[인터뷰]", "기자의 눈", "취재후기",
]

# ⚠️ MEDIA_LEAN 사본을 두지 않는다. config 한 곳에서 읽는다.
#    2026-09-30 까지 여기 12개짜리 옛 사본이 있어 config import 를 가렸고,
#    YTN·뉴시스·JTBC·세계일보가 빠져 있었다. 같은 매체가 RSS 로는 진영이 붙고
#    네이버로는 unknown 이 되는 상태였다.


def _clean_html(text: str) -> str:
    """HTML 태그 및 엔티티 제거"""
    text = unescape(text)
    text = re.sub(r"<[^>]+>", "", text)
    return text.strip()


def _detect_media(origin_url: str) -> tuple[str, str]:
    """원문 URL 에서 매체명과 진영을 읽는다.

    ⚠️ 예전에는 도메인 문자열(`www.ytn.co.kr`)을 받아 한글 매체명(`YTN`)과
       `in` 으로 대조했다. **절대 안 맞는다.** 2026-09-30 실측으로
       진영 미상 168건 중 120건이 이 때문이었고, 경향·오마이·조선처럼 RSS 로도
       받는 매체가 도메인으로 또 들어와 **같은 매체를 둘로 세고 있었다**
       (coverage_count 부풀림 → 교차검증 왜곡).

       도메인 표는 config.MEDIA_DOMAINS 한 곳에 있다.
    """
    name = media_from_url(origin_url)
    return (name or "네이버뉴스"), MEDIA_LEAN.get(name, "unknown")


def search_news(query: str, display: int = 20) -> list[dict]:
    """네이버 검색 API로 뉴스를 검색한다."""
    if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
        return []

    try:
        resp = httpx.get(
            SEARCH_URL,
            params={"query": query, "display": display, "sort": "date"},
            headers={
                "X-Naver-Client-Id": NAVER_CLIENT_ID,
                "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        results: list[dict] = []
        for item in data.get("items", []):
            title = _clean_html(item.get("title", ""))
            description = _clean_html(item.get("description", ""))

            # 사설/칼럼 제외
            if any(kw in title for kw in OPINION_KEYWORDS):
                continue

            # 발행일
            pub_date = item.get("pubDate", "")
            try:
                parsed = datetime.strptime(pub_date, "%a, %d %b %Y %H:%M:%S %z")
                published_at = parsed.isoformat()
            except (ValueError, TypeError):
                published_at = datetime.now().isoformat()

            media_name, media_lean = _detect_media(item.get("originallink", ""))

            results.append({
                "title": title,
                "summary": description[:500],
                "source_url": item.get("originallink", item.get("link", "")),
                "published_at": published_at,
                "source": media_name,
                "source_tier": 3,
                "media_lean": media_lean,
            })

        return results

    except Exception as e:
        print(f"[naver] 검색 실패 ({query}): {e}")
        return []


def fetch_all_political_news() -> list[dict]:
    """모든 정치 키워드로 뉴스를 수집한다."""
    all_news: list[dict] = []
    seen_urls: set[str] = set()

    for query in POLITICAL_QUERIES:
        results = search_news(query, display=10)
        for item in results:
            url = item.get("source_url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                all_news.append(item)

    print(f"  [네이버] {len(all_news)}건 수집 (중복 제거)")
    return all_news


if __name__ == "__main__":
    news = fetch_all_political_news()
    for n in news[:10]:
        print(f"  [{n['source']}|{n['media_lean']}] {n['title']}")
