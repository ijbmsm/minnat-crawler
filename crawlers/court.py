"""대한민국 법원 판결문 크롤러

https://www.scourt.go.kr
- 정치인 관련 판결 수집
- Tier 1 소스
"""
import httpx
from bs4 import BeautifulSoup
from datetime import datetime

BASE_URL = "https://www.scourt.go.kr"

# 정치인 관련 키워드
POLITICAL_KEYWORDS = [
    "의원", "국회", "장관", "대통령", "총리", "도지사", "시장",
    "뇌물", "직권남용", "공직선거법", "정치자금법", "배임",
]


def fetch_recent_rulings() -> list[dict]:
    """최근 주요 판결 목록을 수집한다."""
    try:
        resp = httpx.get(
            f"{BASE_URL}/portal/news/NewsListAction/list.do",
            params={
                "type_cd": "4",  # 판결 소식
                "pageSize": 20,
            },
            timeout=30,
            headers={"User-Agent": "minnat-crawler/1.0 (legal research)"},
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        results: list[dict] = []
        rows = soup.select("table tbody tr, .board-list li, .list-item")

        for row in rows:
            title_el = row.select_one("a, .title")
            date_el = row.select_one(".date, td:last-child")

            if not title_el:
                continue

            title = title_el.get_text(strip=True)

            # 정치 관련 판결만 필터
            if not any(kw in title for kw in POLITICAL_KEYWORDS):
                continue

            link = ""
            if title_el.name == "a" and title_el.get("href"):
                href = title_el["href"]
                link = href if href.startswith("http") else f"{BASE_URL}{href}"

            date_str = date_el.get_text(strip=True) if date_el else ""

            results.append({
                "title": title,
                "source_url": link,
                "date": date_str,
                "source": "대한민국 법원",
                "source_tier": 1,
            })

        return results

    except Exception as e:
        print(f"[court] 수집 실패: {e}")
        return []


if __name__ == "__main__":
    rulings = fetch_recent_rulings()
    print(f"수집된 정치 관련 판결: {len(rulings)}건")
    for r in rulings[:5]:
        print(f"  - {r['title']}")
