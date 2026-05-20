"""주요 언론사 RSS 뉴스 크롤러

- 연합뉴스, KBS, MBC, SBS 정치 섹션 RSS
- Tier 3 소스 (교차검증 필요)
- 스트레이트 뉴스만 수집 (사설/칼럼 제외)
"""
import feedparser
from datetime import datetime
from time import mktime

# 주요 언론사 정치 섹션 RSS
NEWS_FEEDS: list[dict[str, str]] = [
    {"name": "연합뉴스", "url": "https://www.yna.co.kr/politics/all/rss"},
    {"name": "KBS", "url": "https://news.kbs.co.kr/api/getRssData.do?ctg=politics"},
    {"name": "MBC", "url": "https://imnews.imbc.com/rss/politics.xml"},
    {"name": "SBS", "url": "https://news.sbs.co.kr/news/SectionRssFeed.do?sectionId=01&plink=RSSREADER"},
]

# 사설/칼럼 키워드 필터
OPINION_KEYWORDS = [
    "사설", "칼럼", "오피니언", "시론", "논설", "기고", "해설",
    "[인터뷰]", "인터뷰", "기자의 눈", "취재후기",
]

# 정치 관련 키워드
POLITICS_KEYWORDS = [
    "국회", "의원", "대통령", "총리", "장관", "여당", "야당",
    "민주당", "국민의힘", "선거", "공약", "법안", "표결",
    "검찰", "기소", "수사", "재판", "판결", "뇌물", "비리",
    "탄핵", "해임", "인사청문", "국정감사",
]


def _is_opinion(title: str) -> bool:
    """사설/칼럼인지 확인"""
    return any(kw in title for kw in OPINION_KEYWORDS)


def _is_political(title: str, summary: str) -> bool:
    """정치 관련 기사인지 확인"""
    text = f"{title} {summary}"
    return any(kw in text for kw in POLITICS_KEYWORDS)


def fetch_news_from_feed(feed_config: dict[str, str]) -> list[dict]:
    """단일 RSS 피드에서 정치 뉴스를 수집한다."""
    try:
        feed = feedparser.parse(feed_config["url"])
        results: list[dict] = []

        for entry in feed.entries:
            title = entry.get("title", "").strip()
            summary = entry.get("summary", entry.get("description", "")).strip()
            link = entry.get("link", "")

            # 사설/칼럼 제외
            if _is_opinion(title):
                continue

            # 정치 관련 기사만
            if not _is_political(title, summary):
                continue

            # 발행일 파싱
            published = ""
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                published = datetime.fromtimestamp(
                    mktime(entry.published_parsed)
                ).isoformat()
            elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                published = datetime.fromtimestamp(
                    mktime(entry.updated_parsed)
                ).isoformat()
            else:
                published = datetime.now().isoformat()

            results.append({
                "title": title,
                "summary": summary[:500],  # 요약 길이 제한
                "source_url": link,
                "published_at": published,
                "source": feed_config["name"],
                "source_tier": 3,
            })

        return results

    except Exception as e:
        print(f"[news] {feed_config['name']} 수집 실패: {e}")
        return []


def fetch_all_news() -> list[dict]:
    """모든 RSS 피드에서 뉴스를 수집한다."""
    all_news: list[dict] = []
    for feed_config in NEWS_FEEDS:
        news = fetch_news_from_feed(feed_config)
        all_news.extend(news)
        print(f"  [{feed_config['name']}] {len(news)}건 수집")
    return all_news


def find_cross_verified(articles: list[dict], threshold: float = 0.6) -> list[dict]:
    """교차검증: 2개 이상 언론사가 보도한 이슈를 찾는다.

    간단한 제목 유사도 기반 매칭.
    """
    verified: list[dict] = []
    used: set[int] = set()

    for i, a in enumerate(articles):
        if i in used:
            continue

        matching_sources = {a["source"]}
        best_article = a

        for j, b in enumerate(articles):
            if j <= i or j in used:
                continue
            if a["source"] == b["source"]:
                continue

            # 간단한 키워드 겹침 체크
            words_a = set(a["title"].split())
            words_b = set(b["title"].split())
            if len(words_a) == 0 or len(words_b) == 0:
                continue

            overlap = len(words_a & words_b) / min(len(words_a), len(words_b))
            if overlap >= threshold:
                matching_sources.add(b["source"])
                used.add(j)

        if len(matching_sources) >= 2:
            best_article["cross_verified"] = True
            best_article["verified_sources"] = list(matching_sources)
            verified.append(best_article)
            used.add(i)

    return verified


if __name__ == "__main__":
    news = fetch_all_news()
    print(f"\n총 수집: {len(news)}건")

    verified = find_cross_verified(news)
    print(f"교차검증 통과: {len(verified)}건")
    for article in verified[:5]:
        print(f"  - {article['title']} ({', '.join(article['verified_sources'])})")
