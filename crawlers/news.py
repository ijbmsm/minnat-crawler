"""주요 언론사 RSS 뉴스 크롤러

- 진보 3 · 중도 4 · 보수 3 균형 구성 (bias_audit 전제)
- Tier 3 소스 (교차검증 필요)
- 스트레이트 뉴스만 수집 (사설/칼럼 제외)
- 매체별 균등 할당으로 특정 매체 쏠림 방지

※ 모든 URL은 2026-09-16 실측으로 동작 확인.
  KBS·MBC는 정치 섹션 RSS가 폐기되어 제외(각각 파싱 실패/에러 페이지 리다이렉트).
"""
import feedparser
from datetime import datetime
from time import mktime

from config import MEDIA_LEAN

# feedparser 기본 UA는 일부 언론사에서 차단된다
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)

# 매체명은 config.MEDIA_LEAN의 키와 정확히 일치해야 한다 (진영 매핑·편향 감사용)
NEWS_FEEDS: list[dict[str, str]] = [
    # ── 진보 ──
    {"name": "한겨레", "url": "https://www.hani.co.kr/rss/politics/"},
    {"name": "경향신문", "url": "https://www.khan.co.kr/rss/rssdata/politic_news.xml"},
    {"name": "오마이뉴스", "url": "http://rss.ohmynews.com/rss/politics.xml"},
    # ── 중도 ──
    {"name": "연합뉴스", "url": "https://www.yna.co.kr/rss/politics.xml"},
    {"name": "뉴시스", "url": "https://newsis.com/RSS/politics.xml"},
    {"name": "SBS", "url": "https://news.sbs.co.kr/news/SectionRssFeed.do?sectionId=01&plink=RSSREADER"},
    {"name": "JTBC", "url": "https://fs.jtbc.co.kr/RSS/politics.xml"},
    # ── 보수 ──
    {"name": "조선일보", "url": "https://www.chosun.com/arc/outboundfeeds/rss/category/politics/?outputType=xml"},
    {"name": "동아일보", "url": "https://rss.donga.com/politics.xml"},
    {"name": "세계일보", "url": "https://www.segye.com/Articles/RSSList/segye_politic.xml"},
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
    name = feed_config["name"]
    try:
        feed = feedparser.parse(feed_config["url"], agent=USER_AGENT)
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
            if getattr(entry, "published_parsed", None):
                published = datetime.fromtimestamp(mktime(entry.published_parsed)).isoformat()
            elif getattr(entry, "updated_parsed", None):
                published = datetime.fromtimestamp(mktime(entry.updated_parsed)).isoformat()
            else:
                published = datetime.now().isoformat()

            results.append({
                "title": title,
                "summary": summary[:500],  # 요약 길이 제한
                "source_url": link,
                "published_at": published,
                "source": name,
                "source_tier": 3,
                "media_lean": MEDIA_LEAN.get(name, "unknown"),
            })

        # 피드가 죽으면 조용히 0건이 된다 → 눈에 띄게 만든다
        if not feed.entries:
            status = getattr(feed, "status", "?")
            print(f"  [news][경고] {name} 피드에 항목이 없습니다 (HTTP {status}) — URL 점검 필요")

        return results

    except Exception as e:
        print(f"  [news][경고] {name} 수집 실패: {e}")
        return []


def fetch_all_news() -> list[dict]:
    """모든 RSS 피드에서 뉴스를 수집한다."""
    all_news: list[dict] = []
    for feed_config in NEWS_FEEDS:
        news = fetch_news_from_feed(feed_config)
        all_news.extend(news)
        print(f"  [{feed_config['name']}] {len(news)}건 수집")
    return all_news


def balanced_sample(articles: list[dict], total: int) -> list[dict]:
    """매체별 라운드로빈으로 total건을 고른다.

    단순히 앞에서 N개를 자르면 기사 수가 많은 매체(연합뉴스 등)가 전부 차지해
    매체 다양성이 무너진다. 매체를 번갈아 뽑아 진영 균형을 유지한다.
    각 매체 안에서는 원래 순서(대개 최신순)를 지킨다.
    """
    if total <= 0 or not articles:
        return []

    buckets: dict[str, list[dict]] = {}
    for a in articles:
        buckets.setdefault(a.get("source", "unknown"), []).append(a)

    picked: list[dict] = []
    round_idx = 0
    # 가장 기사가 많은 매체의 건수만큼만 돌면 모든 기사를 한 번씩 훑는다
    max_len = max(len(v) for v in buckets.values())
    while len(picked) < total and round_idx < max_len:
        for source in sorted(buckets):
            if len(picked) >= total:
                break
            bucket = buckets[source]
            if round_idx < len(bucket):
                picked.append(bucket[round_idx])
        round_idx += 1

    return picked


if __name__ == "__main__":
    news = fetch_all_news()
    print(f"\n총 수집: {len(news)}건")

    sample = balanced_sample(news, 60)
    from collections import Counter
    print(f"균등 할당 {len(sample)}건:")
    for src, cnt in Counter(a["source"] for a in sample).most_common():
        print(f"  {src}: {cnt}건 ({MEDIA_LEAN.get(src, '?')})")
