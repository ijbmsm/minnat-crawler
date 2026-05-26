"""디시인사이드 실시간 베스트 트렌드 감지 → 네이버 뉴스 연계

디시 실베 자체는 소스로 사용하지 않음 (Tier 4, 유저 생성 콘텐츠).
트렌드 키워드만 추출하여 네이버 뉴스에서 관련 기사를 검색.
정치 관련 + 사회 논란이 정치권으로 확산된 이슈를 포착.
"""
import re
import time
from html import unescape

import httpx

from crawlers.naver_news import search_news

DC_REALTIME_BEST_URL = "https://m.dcinside.com/board/dcbest"

# 정치 관련 키워드 — 이 키워드가 포함된 글만 트렌드로 취급
POLITICAL_SIGNALS = [
    "대통령", "국회", "의원", "장관", "여당", "야당",
    "국민의힘", "민주당", "정부", "청와대", "대통령실",
    "불매", "고발", "고소", "탄핵", "특검",
    "선거", "투표", "공약", "정책",
    "논란", "규탄", "시위", "집회",
    "검찰", "경찰", "법원", "재판",
]

# 디시 실베에서 무시할 패턴 (정치 무관)
IGNORE_PATTERNS = [
    r"야구", r"축구", r"롤$", r"게임", r"아이돌",
    r"코스프레", r"짤", r"ㅋㅋ", r"레전드$",
]

# 사설/칼럼 필터 (naver_news.py와 동일)
OPINION_KEYWORDS = [
    "사설", "칼럼", "오피니언", "시론", "논설", "기고",
]


def _clean(text: str) -> str:
    text = unescape(text)
    return re.sub(r"<[^>]+>", "", text).strip()


def _is_political(title: str) -> bool:
    """정치 관련 키워드가 포함된 제목인지 확인"""
    return any(kw in title for kw in POLITICAL_SIGNALS)


def _should_ignore(title: str) -> bool:
    """무시할 패턴인지 확인"""
    return any(re.search(pat, title) for pat in IGNORE_PATTERNS)


def fetch_dc_trending_keywords() -> list[str]:
    """디시 실베에서 정치 관련 트렌드 키워드를 추출한다.

    HTML을 파싱해서 제목 텍스트만 추출.
    정치 관련 키워드가 포함된 제목에서 핵심 키워드를 뽑아 반환.
    """
    try:
        resp = httpx.get(
            DC_REALTIME_BEST_URL,
            headers={
                "User-Agent": "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
                "Accept-Language": "ko-KR,ko;q=0.9",
            },
            timeout=15,
            follow_redirects=True,
        )
        resp.raise_for_status()
        html = resp.text

        # 제목 추출 (모바일 페이지 기준)
        # <span class="gall-tit">...</span> 또는 <a> 내 텍스트
        titles: list[str] = []
        for match in re.finditer(r'class="gall-tit"[^>]*>.*?<a[^>]*>([^<]+)</a>', html, re.DOTALL):
            title = _clean(match.group(1))
            if title and len(title) > 5:
                titles.append(title)

        # 대체 패턴
        if not titles:
            for match in re.finditer(r'<a[^>]*href="/board/dcbest/[^"]*"[^>]*>([^<]+)</a>', html):
                title = _clean(match.group(1))
                if title and len(title) > 5:
                    titles.append(title)

        # 정치 관련만 필터링
        political_titles = [
            t for t in titles
            if _is_political(t) and not _should_ignore(t)
        ]

        # 키워드 추출: 제목에서 정치 신호 키워드를 제거한 핵심 주제어
        keywords: list[str] = []
        seen: set[str] = set()
        for title in political_titles:
            # 제목 자체를 검색 키워드로 사용 (네이버가 알아서 매칭)
            # 너무 길면 앞 30자만
            keyword = title[:30].strip()
            if keyword not in seen:
                seen.add(keyword)
                keywords.append(keyword)

        return keywords[:10]  # 최대 10개

    except Exception as e:
        print(f"  [dcinside] 트렌드 수집 실패: {e}")
        return []


def fetch_trending_news() -> list[dict]:
    """디시 실베 트렌드 → 네이버 뉴스 검색 결과 반환.

    디시 글 자체는 절대 반환하지 않음.
    네이버 뉴스 기사만 반환 (Tier 3).
    """
    keywords = fetch_dc_trending_keywords()
    if not keywords:
        print("  [dcinside] 정치 관련 트렌드 없음")
        return []

    print(f"  [dcinside] 트렌드 키워드 {len(keywords)}개: {', '.join(k[:15] for k in keywords)}")

    all_news: list[dict] = []
    seen_urls: set[str] = set()

    for keyword in keywords:
        results = search_news(keyword, display=5)
        for item in results:
            url = item.get("source_url", "")
            title = item.get("title", "")

            # 사설 제외
            if any(kw in title for kw in OPINION_KEYWORDS):
                continue

            if url and url not in seen_urls:
                seen_urls.add(url)
                # 트렌드 소스 표시 (분석기가 social_controversy로 분류하도록)
                item["trend_source"] = "dcinside_realtime_best"
                all_news.append(item)

        time.sleep(0.3)  # 네이버 API 부하 방지

    print(f"  [dcinside→네이버] {len(all_news)}건 수집")
    return all_news


if __name__ == "__main__":
    print("디시 실베 트렌드 감지 테스트\n")

    keywords = fetch_dc_trending_keywords()
    print(f"정치 트렌드 {len(keywords)}개:")
    for kw in keywords:
        print(f"  - {kw}")

    print(f"\n뉴스 검색...")
    news = fetch_trending_news()
    for n in news[:10]:
        print(f"  [{n['source']}] {n['title']}")
