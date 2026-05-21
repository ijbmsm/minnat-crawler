"""역사 데이터 시드 스크립트 — 수동 실행

네이버 뉴스 아카이브에서 과거 정치 이슈를 검색하고
Claude API로 분류하여 Supabase에 저장한다.

사용법:
  python seed_historical.py --year 2020 --limit 50
"""
import argparse
import httpx
import os
import time
from datetime import datetime
from html import unescape
import re

from analyzer import analyze_article
from validator import validate_issue
from db import insert_issue, get_client
from config import CATEGORY_WEIGHT


NAVER_CLIENT_ID = os.environ.get("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "")

HISTORICAL_QUERIES = [
    "{year}년 국회의원 기소",
    "{year}년 정치인 뇌물",
    "{year}년 국회 법안 통과",
    "{year}년 정치인 막말",
    "{year}년 정치 스캔들",
    "{year}년 대통령 정책",
    "{year}년 국정감사",
    "{year}년 공약 이행",
    "{year}년 정치인 기부",
]


def search_historical(query: str, display: int = 20) -> list[dict]:
    """네이버 검색 API로 과거 뉴스를 검색한다."""
    if not NAVER_CLIENT_ID:
        print("[seed] 네이버 API 키 없음. NAVER_CLIENT_ID/SECRET 설정 필요.")
        return []

    try:
        resp = httpx.get(
            "https://openapi.naver.com/v1/search/news.json",
            params={"query": query, "display": display, "sort": "sim"},
            headers={
                "X-Naver-Client-Id": NAVER_CLIENT_ID,
                "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        results: list[dict] = []
        for item in data.get("items", []):
            title = unescape(re.sub(r"<[^>]+>", "", item.get("title", "")))
            desc = unescape(re.sub(r"<[^>]+>", "", item.get("description", "")))

            pub_date = item.get("pubDate", "")
            try:
                parsed = datetime.strptime(pub_date, "%a, %d %b %Y %H:%M:%S %z")
                published_at = parsed.isoformat()
            except (ValueError, TypeError):
                published_at = datetime.now().isoformat()

            results.append({
                "title": title,
                "summary": desc[:500],
                "source_url": item.get("originallink", item.get("link", "")),
                "published_at": published_at,
                "source": "네이버뉴스 아카이브",
                "source_tier": 3,
            })

        return results
    except Exception as e:
        print(f"[seed] 검색 실패: {e}")
        return []


def load_politicians_map() -> dict[str, str]:
    """정치인 DB에서 이름→camp 매핑 로드."""
    client = get_client()
    result = (
        client.table("politicians")
        .select("name, party:parties(camp)")
        .eq("active", True)
        .execute()
    )
    mapping: dict[str, str] = {}
    for row in result.data:
        name = row.get("name", "")
        party = row.get("party")
        if name and party and isinstance(party, dict):
            mapping[name] = party.get("camp", "")
    return mapping


def seed_year(year: int, limit: int = 50) -> int:
    """특정 연도의 이슈를 수집한다."""
    politicians_map = load_politicians_map()
    print(f"\n[seed] {year}년 데이터 수집 (정치인 DB: {len(politicians_map)}명)")

    all_articles: list[dict] = []
    for query_template in HISTORICAL_QUERIES:
        query = query_template.format(year=year)
        articles = search_historical(query, display=10)
        all_articles.extend(articles)
        time.sleep(0.5)  # API rate limit

    print(f"[seed] 수집: {len(all_articles)}건")

    inserted = 0
    for article in all_articles[:limit]:
        try:
            analysis = analyze_article(
                article["title"], article["summary"],
                article["source"], politicians_map,
            )
            if not analysis:
                continue

            validation = validate_issue(analysis, article, politicians_map)

            score = CATEGORY_WEIGHT.get(analysis["category"], 0)
            issue = {
                "title": article["title"],
                "summary": analysis.get("summary", article["summary"][:300]),
                "category": analysis["category"],
                "camp": analysis["camp"],
                "severity": analysis["severity"],
                "impact_scope": analysis["impact_scope"],
                "source_tier": article["source_tier"],
                "source_url": article["source_url"],
                "source_name": article["source"],
                "raw_score": score,
                "weighted_score": float(score),
                "ai_analysis": {
                    "confidence": analysis.get("confidence", 0),
                    "reasoning": analysis.get("reasoning", ""),
                    "category_rationale": analysis.get("category_reasoning", ""),
                    "severity_rationale": analysis.get("severity_reasoning", ""),
                    "camp_reasoning": analysis.get("camp_reasoning", ""),
                },
                "published_at": article["published_at"],
                "verified": False,
                "validation_status": "flagged" if not validation.passed else "passed",
                "validation_errors": validation.errors + validation.warnings,
            }

            result = insert_issue(issue)
            if result:
                print(f"  [{analysis['camp']}] {analysis['category']}: {article['title'][:50]}")
                inserted += 1

            time.sleep(1)  # API rate limit

        except Exception as e:
            print(f"  [error] {e}")

    print(f"[seed] {year}년 완료: {inserted}건 저장")
    return inserted


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="역사 데이터 시드")
    parser.add_argument("--year", type=int, default=2024, help="수집 연도")
    parser.add_argument("--limit", type=int, default=50, help="최대 건수")
    parser.add_argument("--range", type=str, help="연도 범위 (예: 2020-2024)")
    args = parser.parse_args()

    if args.range:
        start, end = map(int, args.range.split("-"))
        total = 0
        for year in range(start, end + 1):
            total += seed_year(year, args.limit)
        print(f"\n총 {total}건 저장 ({start}~{end})")
    else:
        seed_year(args.year, args.limit)
