"""역사 데이터 시드 v1.1

사용법:
  python seed_historical.py --range 2000-2025 --limit 30
"""
import argparse
import os
import time
from datetime import datetime
from html import unescape
import re

import httpx

from analyzer import analyze_article
from db import insert_issue, get_client
from config import SCORED_CATEGORIES, POSITION_WEIGHT

NAVER_CLIENT_ID = os.environ.get("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "")

# v1.1 키워드 — 공식 처분 위주
HISTORICAL_QUERIES = [
    "{year}년 국회의원 유죄 판결",
    "{year}년 정치인 기소",
    "{year}년 정치인 벌금형",
    "{year}년 정치인 징역",
    "{year}년 윤리위 징계",
    "{year}년 선관위 처분",
    "{year}년 감사원 적발",
    "{year}년 정치인 사과",
    "{year}년 팩트체크 거짓",
    # archive
    "{year}년 정치인 막말",
    "{year}년 국회 법안 통과",
]


def search_naver(query: str, display: int = 20) -> list[dict]:
    if not NAVER_CLIENT_ID:
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
        results: list[dict] = []
        for item in resp.json().get("items", []):
            title = unescape(re.sub(r"<[^>]+>", "", item.get("title", "")))
            desc = unescape(re.sub(r"<[^>]+>", "", item.get("description", "")))
            pub = item.get("pubDate", "")
            try:
                parsed = datetime.strptime(pub, "%a, %d %b %Y %H:%M:%S %z")
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
        print(f"  [search] {e}")
        return []


def load_politicians_map() -> dict[str, str]:
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


def seed_year(year: int, limit: int, politicians_map: dict[str, str]) -> int:
    print(f"\n[seed] {year}년 수집...")

    all_articles: list[dict] = []
    for tmpl in HISTORICAL_QUERIES:
        query = tmpl.format(year=year)
        articles = search_naver(query, display=10)
        all_articles.extend(articles)
        time.sleep(0.5)

    # 중복 제거
    seen: set[str] = set()
    unique: list[dict] = []
    for a in all_articles:
        key = a["title"][:40]
        if key not in seen:
            seen.add(key)
            unique.append(a)

    print(f"  수집: {len(unique)}건 (중복 제거)")
    inserted = 0

    for article in unique[:limit]:
        try:
            analysis = analyze_article(
                article["title"], article["summary"],
                article["source"], politicians_map,
            )
            if not analysis:
                continue

            is_archive = analysis["category"] not in SCORED_CATEGORIES

            issue = {
                "title": article["title"],
                "summary": analysis.get("summary", article["summary"][:300]),
                "category": analysis["category"],
                "camp": analysis["camp"],
                "source_tier": 3,
                "source_url": article["source_url"],
                "source_name": article["source"],
                "weighted_score": 0,
                "ai_analysis": {
                    "confidence": analysis.get("confidence", 0),
                    "reasoning": analysis.get("reasoning", ""),
                    "category_rationale": analysis.get("category_reasoning", ""),
                    "camp_reasoning": analysis.get("camp_reasoning", ""),
                    "evidence_sentence": analysis.get("evidence_sentence", ""),
                    "criminal_stage_reasoning": None,
                },
                "published_at": article["published_at"],
                "verified": True,  # 역사 데이터: 시간이 검증
                "trust_level": "medium",
                "criminal_stage": analysis.get("criminal_stage"),
                "coverage_count": 1,
                "headline_days": 1,
                "is_archive": is_archive,
                "position_weight": 0.8,
                "actor_name": analysis.get("actor_name", ""),
                "actor_party": analysis.get("actor_party", ""),
                "cross_verified_sources": [],
            }

            result = insert_issue(issue)
            if result:
                tag = "SCORED" if not is_archive else "ARCHIVE"
                print(f"  [{tag}] [{analysis['camp']}] {analysis['category']}: {article['title'][:50]}")
                inserted += 1

            time.sleep(1)
        except Exception as e:
            print(f"  [error] {e}")

    print(f"  {year}년 완료: {inserted}건")
    return inserted


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, default=2024)
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--range", type=str)
    args = parser.parse_args()

    politicians_map = load_politicians_map()
    print(f"정치인 DB: {len(politicians_map)}명")

    if args.range:
        start, end = map(int, args.range.split("-"))
        total = 0
        for year in range(start, end + 1):
            total += seed_year(year, args.limit, politicians_map)
        print(f"\n총 {total}건 ({start}~{end})")
    else:
        seed_year(args.year, args.limit, politicians_map)
