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
    # 2026-09-30 추가. 진보 매체 수집량이 20.5% 로 낮아 다양도 배수가 0.7 에 묶였다.
    # 한국 언론사 RSS 는 대부분 죽었고(KBS·MBC·YTN·중앙·한국·문화·채널A·TV조선 전부
    # 404/0건) 실측으로 살아 있는 진보 매체는 여기뿐이었다.
    {"name": "프레시안", "url": "https://www.pressian.com/api/v3/site/rss/section/65"},
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
# 정치 기사 판정 키워드.
#
# ⚠️ 2026-09-30 실측: 24개짜리 옛 목록이 RSS 539건 중 **251건(47%)** 을 버렸다.
#    버려진 것들이 명백한 정치 기사였다 —
#      "'원내대표 멱살잡이' 국힘 권영진, 당원권 정지 1년"   ← 당원권·징계 없음
#      "국정원 '포로송환 시작부터 비공개 합의'"              ← 국정원 없음
#      "합참 '북 국경선 요새화 즉시 중단해야'"               ← 합참·외교·안보 없음
#      "배현진, 연일 JYP 소환"                              ← 인명은 원래 못 잡는다
#    한겨레는 30건 중 7건만 통과했다. 진보 매체 수집량이 20%에 묶인 주원인이
#    매체 수가 아니라 **이 목록**이었다.
#
# 넓힐 때 원칙 — 정치인·정당 이름을 넣지 않는다. 명단은 정치인 DB 가 갖고 있고
# (analyzer 가 프롬프트에 명단을 안 넣는 것과 같은 이유), 여기 넣으면 목록이
# 사람 수에 비례해 늘어난다. **직위·기관·절차**만 넣는다.
POLITICS_KEYWORDS = [
    # 입법·행정 기관
    "국회", "의원", "대통령", "총리", "부총리", "장관", "차관", "청장", "처장",
    "청와대", "대통령실", "국무회의", "내각", "정부", "당국",
    "국정원", "감사원", "권익위", "선관위", "헌재", "헌법재판소",
    "합참", "국방부", "외교부", "통일부", "법무부", "행안부",
    # 정당·정치 구조
    "여당", "야당", "여야", "정당", "당대표", "원내대표", "최고위", "지도부",
    "당원권", "징계", "제명", "탈당", "입당", "공천", "경선",
    # 선거·입법 절차
    "선거", "공약", "법안", "발의", "표결", "본회의", "상임위", "국정감사", "국감",
    "예산안", "시정연설", "대정부질문", "청문회", "인사청문", "특검", "국정조사",
    # 사법·수사
    "검찰", "경찰", "공수처", "기소", "구형", "수사", "압수수색", "영장",
    "재판", "판결", "선고", "송치", "고발", "고소", "뇌물", "비리", "횡령", "배임",
    # 제도적 결정
    "탄핵", "해임", "사퇴", "사의", "파면", "임명", "지명", "개각", "개헌",
    # 외교·안보 (정치면의 큰 축인데 옛 목록에 통째로 없었다)
    "외교", "안보", "정상회담", "북한", "남북", "한미", "한일", "한중", "방위비",
    "국방", "유엔사", "정전협정", "DMZ", "비무장지대", "주한미군", "연합훈련",
]

# ⚠️ 부분 문자열로 본다. 오탐이 난다 — `경선` 이 `국경선` 에, `정부` 가 `행정부` 에,
#    `재판` 이 `재판매` 에 걸린다. 고치지 않는다.
#
#    이 필터의 일은 **거친 사전 선별**이다. 오탐은 싸다 — 기사가 한 번 더 분석되고
#    LLM 이 media_coverage 로 떨군다. 누락은 비싸다 — 그 기사는 영영 안 보인다.
#    경계를 정밀하게 만들려다 누락을 늘리는 쪽이 더 나쁘다.


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
