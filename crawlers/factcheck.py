"""SNU 팩트체크센터 크롤러

https://factcheck.snu.ac.kr
- 정치인 발언 팩트체크 결과 수집
- Tier 2 소스
"""
import httpx
from bs4 import BeautifulSoup

BASE_URL = "https://factcheck.snu.ac.kr"


def fetch_recent_factchecks(page: int = 1) -> list[dict]:
    """최근 팩트체크 결과 목록을 수집한다."""
    try:
        resp = httpx.get(
            f"{BASE_URL}/v2/facts",
            params={"page": page},
            timeout=30,
            headers={"User-Agent": "minnat-crawler/1.0 (factcheck research)"},
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        results: list[dict] = []
        # 팩트체크 카드 파싱
        cards = soup.select(".fact-card, .card, article")

        for card in cards:
            title_el = card.select_one("h3, h4, .title, .card-title")
            link_el = card.select_one("a[href]")
            verdict_el = card.select_one(".verdict, .badge, .label")
            date_el = card.select_one(".date, time, .meta")

            if not title_el:
                continue

            title = title_el.get_text(strip=True)
            link = ""
            if link_el and link_el.get("href"):
                href = link_el["href"]
                link = href if href.startswith("http") else f"{BASE_URL}{href}"

            verdict = verdict_el.get_text(strip=True) if verdict_el else ""
            date = date_el.get_text(strip=True) if date_el else ""

            results.append({
                "title": title,
                "source_url": link,
                "verdict": verdict,
                "date": date,
                "source": "SNU 팩트체크센터",
                "source_tier": 2,
            })

        return results

    except Exception as e:
        print(f"[factcheck] 수집 실패: {e}")
        return []


if __name__ == "__main__":
    checks = fetch_recent_factchecks()
    print(f"수집된 팩트체크: {len(checks)}건")
    for fc in checks[:5]:
        print(f"  - [{fc['verdict']}] {fc['title']}")
