"""역사 데이터 시드 v2.0 — Event 매칭 포함

사용법:
  python seed_historical.py --range 2020-2025 --limit 100
  python seed_historical.py --year 2024 --limit 50
"""
import argparse
import time
from datetime import datetime

from analyzer import analyze_article
from db import insert_issue, get_client, get_active_events
from event_matcher import get_embedding, match_to_event
from event_manager import create_event, merge_into_event
from crawlers.naver_web import fetch_historical_news
from config import SCORED_CATEGORIES, POSITION_WEIGHT

HISTORICAL_QUERIES = [
    # ── 형사 처분 (scored) ──
    "국회의원 유죄 판결",
    "정치인 기소",
    "정치인 벌금형",
    "정치인 징역",
    "국회의원 실형",
    "국회의원 구속",
    "정치인 뇌물",
    "정치인 횡령",
    "정치인 배임",
    "공직선거법 위반 유죄",
    "정치자금법 위반",
    "국회의원 1심 선고",
    "국회의원 대법원 확정",
    "전 장관 기소",
    "전 시장 기소",
    "전 도지사 기소",
    # ── 윤리위·선관위·감사원 ──
    "윤리위 징계",
    "선관위 처분",
    "감사원 적발",
    "감사원 감사 결과",
    "국정감사 질의",
    "국정감사 적발",
    # ── 팩트체크 ──
    "팩트체크 거짓",
    "팩트체크 대부분 거짓",
    # ── 막말·논란 발언 (archive) ──
    "정치인 막말 논란",
    "국회의원 막말",
    "정치인 발언 논란",
    "정치인 사과 사퇴",
    "국회의원 사퇴",
    # ── 입법 (archive) ──
    "국회 법안 통과",
    "국회 본회의 가결",
    "쟁점 법안 국회",
    # ── 사회 이슈 (social_controversy) ──
    "불매운동 정치",
    "사회 논란 국회",
    "기업 논란 정치권",
    "정부 대응 논란",
    "재난 참사 정치 책임",
    "시위 집회 정치",
    "노동 파업 정부",
    # ── 주요 인물별 (빠짐 방지) ──
    "이재명 재판",
    "윤석열 수사",
    "조국 재판",
    "한동훈 논란",
    "이준석 논란",
    "나경원 논란",
    "오세훈 논란",
    "정청래 논란",
    "김건희 수사",
    "추미애 논란",
    "홍준표 논란",
    "박근혜 사면",
    "이명박 사면",
    "김경수 재판",
    "송영길 재판",
    # ── 정책 관련 ──
    "부동산 정책 논란",
    "의대 정원 논란",
    "탈원전 논란",
    "검수완박",
    "공수처 논란",
    "언론중재법 논란",
]



def load_politicians_map() -> dict[str, str]:
    """active/inactive 구분 없이 전체 정치인 조회 (과거 인물 포함)"""
    client = get_client()
    result = (
        client.table("politicians")
        .select("name, party:parties(camp)")
        .execute()
    )
    mapping: dict[str, str] = {}
    for row in result.data:
        name = row.get("name", "")
        party = row.get("party")
        if name and party and isinstance(party, dict):
            mapping[name] = party.get("camp", "")
    return mapping


def load_politicians_positions() -> dict[str, str]:
    """active/inactive 구분 없이 전체 정치인 직책 조회"""
    client = get_client()
    result = client.table("politicians").select("name, position").execute()
    return {row["name"]: row.get("position", "의원") for row in result.data}


def seed_year(year: int, limit: int, politicians_map: dict[str, str], politicians_positions: dict[str, str]) -> int:
    print(f"\n{'='*50}")
    print(f"[seed] {year}년 수집 시작 (limit={limit})")
    print(f"{'='*50}")

    # 활성 이벤트 로드 (이벤트 매칭용)
    active_events = get_active_events()
    print(f"  활성 사건: {len(active_events)}건")

    # 네이버 웹 크롤링 (API 대신 — 과거 날짜 필터 정확)
    unique = fetch_historical_news(HISTORICAL_QUERIES, year, max_per_query=10)
    print(f"  수집: {len(unique)}건 (웹 크롤링, 키워드 {len(HISTORICAL_QUERIES)}개)")
    inserted = 0
    merged = 0
    new_events = 0

    for article in unique[:limit]:
        try:
            analysis = analyze_article(
                article["title"], article["summary"],
                article["source"], politicians_map,
            )
            if not analysis:
                continue

            actor_name = analysis.get("actor_name", "")
            position = politicians_positions.get(actor_name, "의원")
            pos_weight = POSITION_WEIGHT.get(position, 0.8)
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
                "verified": True,
                "trust_level": "medium",
                "criminal_stage": analysis.get("criminal_stage"),
                "coverage_count": 1,
                "headline_days": 1,
                "is_archive": is_archive,
                "position_weight": pos_weight,
                "actor_name": actor_name,
                "actor_party": analysis.get("actor_party", ""),
                "cross_verified_sources": [],
            }

            result = insert_issue(issue)
            if not result:
                continue

            issue_with_id = {**issue, "id": result["id"], "headline": analysis.get("headline", "")}

            # ── Embedding + Event 매칭 ──
            embedding_text = f"{article['title']} {issue['summary']}"
            embedding = get_embedding(embedding_text)

            preliminary = {
                "title": article["title"],
                "summary": issue["summary"],
                "camp": analysis["camp"],
                "category": analysis["category"],
                "actor_name": actor_name,
                "published_at": article["published_at"],
            }
            matched_event = match_to_event(preliminary, active_events, embedding)

            if matched_event:
                merge_result = merge_into_event(matched_event, issue_with_id)
                if merge_result:
                    merged += 1
                    tag = "MERGED"
                else:
                    event = create_event(issue_with_id, embedding)
                    if event:
                        active_events.append(event)
                        new_events += 1
                    tag = "NEW(merge_fail)"
            else:
                event = create_event(issue_with_id, embedding)
                if event:
                    active_events.append(event)
                    new_events += 1
                tag = "NEW"

            camp_label = "파랑" if analysis["camp"] == "blue" else "빨강"
            score_tag = "SCORED" if not is_archive else "ARCHIVE"
            print(f"  [{camp_label}] [{score_tag}] [{tag}] {analysis['category']}: {article['title'][:50]}")
            inserted += 1

            time.sleep(0.8)
        except Exception as e:
            print(f"  [error] {e}")

    print(f"\n  {year}년 완료: 저장 {inserted} | 머지 {merged} | 새 사건 {new_events}")
    return inserted


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, default=2024)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--range", type=str)
    args = parser.parse_args()

    politicians_map = load_politicians_map()
    politicians_positions = load_politicians_positions()
    print(f"정치인 DB: {len(politicians_map)}명")

    if args.range:
        start, end = map(int, args.range.split("-"))
        total = 0
        for year in range(start, end + 1):
            total += seed_year(year, args.limit, politicians_map, politicians_positions)
        print(f"\n{'='*50}")
        print(f"총 {total}건 ({start}~{end})")
        print(f"{'='*50}")
    else:
        seed_year(args.year, args.limit, politicians_map, politicians_positions)
