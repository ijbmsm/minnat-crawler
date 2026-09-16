"""팩트체크 매체 크롤러 (Tier 2) — 현재 비활성

2026-09-16 실측 결과 국내 팩트체크 소스가 사실상 전멸했다:
  - SNU팩트체크 : 2024-08 무기한 중단 (현재 SSL 인증서도 만료)
  - 팩트체크넷   : 종료
  - JTBC 팩트체크: 섹션 URL 404 (유일한 국내 IFCN 인증 매체였음)
  - 뉴스톱       : 도메인이 타 매체(newstopkorea.com)로 이전
  - 연합뉴스 팩트체크 RSS : 404
  - MBC 알고보니 / KBS 팩트체크K : 0건

`factcheck_false` 카테고리는 "IFCN 인증 매체의 false 판정"만 인정하는데,
그 조건을 만족하는 국내 매체가 남아 있지 않다. SBS '사실은'은 IFCN 인증이
아니어서 점수 소스로 쓰면 방법론을 위반하고, 어차피 SBS RSS로 함께 들어온다.

→ 소스를 되살리는 대신 정직하게 중단한다. 방법론 페이지에 사유를 공개할 것.
   (plan-v1.1 "한계 솔직 공개" 원칙)

새 IFCN 인증 매체가 생기면 FACTCHECK_SOURCES에 추가하고 ENABLED를 켜면 된다.
"""
import httpx
from bs4 import BeautifulSoup

# 살아 있는 IFCN 인증 국내 매체가 없어 비활성. 위 주석 참조.
ENABLED = False

FACTCHECK_SOURCES: list[dict] = []

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
    if not ENABLED:
        print("  [팩트체크] 비활성 — 국내 IFCN 인증 매체 부재 (crawlers/factcheck.py 주석 참조)")
        return []

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
