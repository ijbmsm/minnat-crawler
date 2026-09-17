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


# 국회 OpenAPI와 달리 법원 포털은 비브라우저 UA를 차단한다 ("Bad Request.")
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
    )
}


def fetch_recent_rulings() -> list[dict]:
    """대법원 '새소식(중요판결)' 목록을 수집한다.

    한계: 판결문은 피고인을 익명화하므로("피고인", "甲") 정치인 실명 사건은
    거의 올라오지 않는다. 정치인 형사·민사 판결의 실용적 1차 소스는 뉴스 보도이며,
    여기는 보조 확인용이다. 0건이 정상일 수 있다.
    """
    try:
        resp = httpx.get(
            f"{BASE_URL}/portal/news/NewsListAction.work",
            params={"gubun": "4"},  # 4 = 주요 판결
            timeout=30,
            headers=HEADERS,
            follow_redirects=True,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        results: list[dict] = []
        for link in soup.select("a[href*=NewsView]"):
            title = link.get_text(strip=True)
            if not title:
                continue

            # 정치 관련 판결만 필터
            if not any(kw in title for kw in POLITICAL_KEYWORDS):
                continue

            href = link.get("href", "")
            url = href if href.startswith("http") else f"{BASE_URL}{href}"

            # 같은 행(tr)에서 날짜 칸을 찾는다
            date_str = ""
            row = link.find_parent("tr")
            if row:
                cells = [td.get_text(strip=True) for td in row.select("td")]
                for cell in reversed(cells):
                    if cell.count(".") >= 2 or cell.count("-") >= 2:
                        date_str = cell
                        break

            results.append({
                "title": title,
                "summary": title,
                "source_url": url,
                "date": date_str,
                "source": "대한민국 법원",
                "source_tier": 1,
            })

        return results

    except Exception as e:
        print(f"  [court][경고] 수집 실패: {e}")
        return []


if __name__ == "__main__":
    rulings = fetch_recent_rulings()
    print(f"수집된 정치 관련 판결: {len(rulings)}건")
    for r in rulings[:5]:
        print(f"  - {r['title']}")
