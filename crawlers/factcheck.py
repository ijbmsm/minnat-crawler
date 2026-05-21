"""팩트체크 매체 크롤러 (Tier 2)

※ SNU 팩트체크: 2024년 8월 무기한 중단 — 제거됨
활성 매체:
- JTBC 팩트체크 (유일한 IFCN 인증)
- MBC 알고보니
- KBS 팩트체크K
- SBS 사실은
- 연합뉴스 팩트체크
"""
import httpx
from bs4 import BeautifulSoup

FACTCHECK_SOURCES = [
    {
        "name": "JTBC 팩트체크",
        "url": "https://news.jtbc.co.kr/section/list.aspx?scode=20",
        "selector": ".bd_newslist li, .news_list li, article",
    },
    {
        "name": "MBC 알고보니",
        "url": "https://imnews.imbc.com/newszoomin/turnedout/",
        "selector": ".list_article li, .news_list li, article",
    },
    {
        "name": "KBS 팩트체크K",
        "url": "https://news.kbs.co.kr/vod/program.do?bcd=0076&ref=pMenu",
        "selector": ".list-item, .news_list li, article",
    },
    {
        "name": "SBS 사실은",
        "url": "https://news.sbs.co.kr/news/programMain.do?prog_cd=R1&plink=GNB",
        "selector": ".w_news_list li, .news_list li, article",
    },
]

HEADERS = {"User-Agent": "minnat-crawler/2.0 (factcheck research)"}


def _parse_items(html: str, selector: str, base_url: str) -> list[dict]:
    """공통 HTML 파싱"""
    soup = BeautifulSoup(html, "lxml")
    items: list[dict] = []

    for el in soup.select(selector)[:20]:
        title_el = el.select_one("a, h3, h4, .title, .tit, .headline, dt a")
        if not title_el:
            continue

        title = title_el.get_text(strip=True)
        if not title or len(title) < 5:
            continue

        href = ""
        link_el = el.select_one("a[href]") or title_el
        if link_el and link_el.get("href"):
            href = link_el["href"]
            if not href.startswith("http"):
                href = base_url.rstrip("/") + "/" + href.lstrip("/")

        date_el = el.select_one(".date, time, .time, .info, .meta, .day")
        date_str = date_el.get_text(strip=True) if date_el else ""

        desc_el = el.select_one(".desc, .summary, .txt, p, dd")
        summary = desc_el.get_text(strip=True)[:200] if desc_el else ""

        items.append({
            "title": title,
            "source_url": href,
            "summary": summary,
            "date": date_str,
        })

    return items


def fetch_factchecks() -> list[dict]:
    """모든 팩트체크 매체에서 최근 기사를 수집한다."""
    all_items: list[dict] = []

    for source in FACTCHECK_SOURCES:
        try:
            resp = httpx.get(source["url"], headers=HEADERS, timeout=20, follow_redirects=True)
            resp.raise_for_status()
            items = _parse_items(resp.text, source["selector"], source["url"])

            for item in items:
                item["source"] = source["name"]
                item["source_tier"] = 2

            all_items.extend(items)
            print(f"  [{source['name']}] {len(items)}건 수집")

        except Exception as e:
            print(f"  [{source['name']}] 수집 실패: {e}")

    return all_items


if __name__ == "__main__":
    results = fetch_factchecks()
    print(f"\n총 팩트체크 수집: {len(results)}건")
    for r in results[:5]:
        print(f"  [{r['source']}] {r['title']}")
