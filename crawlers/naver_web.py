"""네이버 뉴스 웹 크롤링 — 과거 기사 날짜 필터 지원

API가 아닌 웹 검색 페이지 파싱.
search.naver.com/search.naver?where=news&query=...&ds=2020.01.01&de=2020.12.31
웹 검색은 과거 날짜 필터가 정확하게 동작함.
"""
import re
import time
from datetime import datetime
from html import unescape

import httpx

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Accept": "text/html,application/xhtml+xml",
}

# 사설/칼럼 필터
OPINION_KEYWORDS = [
    "사설", "칼럼", "오피니언", "시론", "논설", "기고",
    "기자수첩", "취재후기", "[인터뷰]", "기자의 눈",
]

# 매체 진영 매핑
MEDIA_LEAN = {
    "한겨레": "progressive",
    "경향신문": "progressive",
    "오마이뉴스": "progressive",
    "프레시안": "progressive",
    "KBS": "center",
    "MBC": "center",
    "SBS": "center",
    "연합뉴스": "center",
    "JTBC": "center",
    "YTN": "center",
    "뉴시스": "center",
    "한국일보": "center",
    "조선일보": "conservative",
    "중앙일보": "conservative",
    "동아일보": "conservative",
    "채널A": "conservative",
    "TV조선": "conservative",
    "문화일보": "conservative",
    "세계일보": "conservative",
}


def _clean(text: str) -> str:
    text = unescape(text)
    text = re.sub(r"<[^>]+>", "", text)
    return text.strip()


def _detect_media(text: str) -> tuple[str, str]:
    for media, lean in MEDIA_LEAN.items():
        if media in text:
            return media, lean
    return "", "unknown"


def _parse_date(date_str: str) -> str:
    """네이버 뉴스 날짜 문자열 파싱 (예: '2020.03.15.' 또는 '3일 전')"""
    # YYYY.MM.DD. 형태
    match = re.match(r"(\d{4})\.(\d{2})\.(\d{2})", date_str)
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}T00:00:00+09:00"
    return datetime.now().isoformat()


def search_naver_web(
    query: str,
    ds: str,
    de: str,
    start: int = 1,
    display: int = 10,
) -> list[dict]:
    """네이버 웹 뉴스 검색.

    Args:
        query: 검색어
        ds: 시작 날짜 (YYYY.MM.DD)
        de: 끝 날짜 (YYYY.MM.DD)
        start: 시작 위치 (1, 11, 21, ...)
        display: 결과 수 (최대 10)
    """
    url = "https://search.naver.com/search.naver"
    params = {
        "where": "news",
        "query": query,
        "sort": "1",  # 관련도순 (0=최신순, 1=관련도)
        "ds": ds,
        "de": de,
        "start": start,
        "nso": f"so:r,p:from{ds.replace('.', '')}to{de.replace('.', '')}",
    }

    try:
        resp = httpx.get(url, params=params, headers=HEADERS, timeout=15, follow_redirects=True)
        resp.raise_for_status()
        html = resp.text

        results: list[dict] = []

        # 뉴스 결과 파싱 — news_tit 클래스에서 제목+링크 추출
        # 패턴 1: <a class="news_tit" href="..." title="...">
        for match in re.finditer(
            r'<a[^>]*class="news_tit"[^>]*href="([^"]*)"[^>]*title="([^"]*)"',
            html,
        ):
            link = match.group(1)
            title = _clean(match.group(2))

            if not title or len(title) < 10:
                continue

            # 사설 필터
            if any(kw in title for kw in OPINION_KEYWORDS):
                continue

            # 매체 추출 — 제목 근처에서 info_group 파싱
            media_name, media_lean = "", "unknown"
            # 링크에서 매체 추정
            for media, lean in MEDIA_LEAN.items():
                if media.lower().replace(" ", "") in link.lower():
                    media_name, media_lean = media, lean
                    break

            results.append({
                "title": title,
                "summary": "",  # 웹 검색에서는 요약이 제한적
                "source_url": link,
                "published_at": _parse_date(ds),  # 검색 기간의 시작일 기준
                "source": media_name or "네이버뉴스",
                "source_tier": 3,
                "media_lean": media_lean,
            })

        # 패턴 2: 모바일/다른 HTML 구조 fallback
        if not results:
            for match in re.finditer(
                r'<a[^>]*href="(https?://[^"]*news[^"]*)"[^>]*>([^<]{10,80})</a>',
                html,
            ):
                link = match.group(1)
                title = _clean(match.group(2))

                if any(kw in title for kw in OPINION_KEYWORDS):
                    continue

                media_name, media_lean = _detect_media(link)

                results.append({
                    "title": title,
                    "summary": "",
                    "source_url": link,
                    "published_at": _parse_date(ds),
                    "source": media_name or "네이버뉴스",
                    "source_tier": 3,
                    "media_lean": media_lean,
                })

        return results[:display]

    except Exception as e:
        print(f"  [naver_web] 검색 실패 ({query}): {e}")
        return []


def fetch_news_by_year(query: str, year: int, max_results: int = 15) -> list[dict]:
    """특정 연도의 뉴스를 반기별로 나눠서 검색."""
    all_results: list[dict] = []

    # 반기별 검색 (상반기 + 하반기)
    periods = [
        (f"{year}.01.01", f"{year}.06.30"),
        (f"{year}.07.01", f"{year}.12.31"),
    ]

    per_period = max(5, max_results // 2)

    for ds, de in periods:
        results = search_naver_web(query, ds, de, display=per_period)
        all_results.extend(results)
        time.sleep(0.5)

    return all_results[:max_results]


def fetch_historical_news(queries: list[str], year: int, max_per_query: int = 10) -> list[dict]:
    """여러 키워드로 특정 연도 뉴스를 일괄 수집."""
    all_news: list[dict] = []
    seen_titles: set[str] = set()

    for query in queries:
        results = fetch_news_by_year(query, year, max_results=max_per_query)
        for item in results:
            key = item["title"][:40]
            if key not in seen_titles:
                seen_titles.add(key)
                all_news.append(item)
        time.sleep(0.3)

    print(f"  [naver_web] {year}년 {len(all_news)}건 수집 (키워드 {len(queries)}개)")
    return all_news


if __name__ == "__main__":
    print("네이버 웹 크롤링 테스트\n")

    test_queries = ["정치인 기소", "국회의원 유죄 판결"]
    for year in [2020, 2022, 2024]:
        results = fetch_historical_news(test_queries, year, max_per_query=5)
        print(f"\n{year}년: {len(results)}건")
        for r in results[:5]:
            print(f"  [{r['source']}] {r['title']}")
