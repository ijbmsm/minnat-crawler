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

# 매체 이름 → 성향 매핑
MEDIA_LEAN = {
    "한겨레": "progressive",
    "경향신문": "progressive",
    "오마이뉴스": "progressive",
    "KBS": "center",
    "MBC": "center",
    "SBS": "center",
    "연합뉴스": "center",
    "조선일보": "conservative",
    "중앙일보": "conservative",
    "동아일보": "conservative",
    "채널A": "conservative",
    "TV조선": "conservative",
}


def _clean_html(text: str) -> str:
    """HTML 태그 및 엔티티 제거"""
    text = unescape(text)
    text = re.sub(r"<[^>]+>", "", text)
    return text.strip()


def _detect_media(source_name: str) -> tuple[str, str]:
    """매체 이름에서 정규화된 이름과 성향을 반환"""
    for media, lean in MEDIA_LEAN.items():
        if media in source_name:
            return media, lean
    return source_name, "unknown"


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

            source_name = item.get("originallink", "").split("/")[2] if "originallink" in item else "네이버뉴스"
            media_name, media_lean = _detect_media(source_name)

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
