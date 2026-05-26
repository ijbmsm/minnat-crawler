"""민낯 크롤러 v2.0 — Event 기반 파이프라인

파이프라인:
0. 정치인 DB 동기화
1. 다중 소스 동시 수집
2. AI 분류 + Embedding 생성
3. Event 매칭 (4단계: 룰→임베딩→LLM)
4. 신뢰도 게이트
5. DB 저장 + Event 생성/머지
6. 미검증 Event 자동 승격
7. 비활성화 + 스냅샷 생성
"""
from datetime import datetime

from crawlers.assembly import fetch_recent_bills
from crawlers.factcheck import fetch_factchecks
from crawlers.news import fetch_all_news
from crawlers.naver_news import fetch_all_political_news
from crawlers.court import fetch_recent_rulings
from crawlers.dcinside_trend import fetch_trending_news
from analyzer import analyze_article
from trust_gate import evaluate_trust
from event_matcher import get_embedding, match_to_event
from event_manager import create_event, merge_into_event, deactivate_old_events
from expression_filter import filter_expression, needs_unconfirmed_label
from scorer import calculate_score, generate_daily_snapshot
from auto_verify import run_auto_verify
from sync_politicians import sync_to_db as sync_politicians
from db import insert_issue, get_client, get_active_events
from config import SCORED_CATEGORIES, POSITION_WEIGHT


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


def load_politicians_positions() -> dict[str, str]:
    """정치인 이름 → 직책 매핑"""
    client = get_client()
    result = client.table("politicians").select("name, position").eq("active", True).execute()
    return {row["name"]: row.get("position", "의원") for row in result.data}


def process_article(
    article: dict,
    source_tier: int,
    politicians_map: dict[str, str],
    politicians_positions: dict[str, str],
    active_events: list[dict],
    all_articles: list[dict],
) -> str:
    title = article.get("title", "")
    content = article.get("summary", article.get("content", ""))
    source = article.get("source", "")

    if not title:
        return "skip_empty"

    # ── AI 분석 ──
    analysis = analyze_article(title, content, source, politicians_map)
    if not analysis:
        return "skip_analysis"

    summary = analysis.get("summary", content[:300])

    # ── Embedding 생성 ──
    embedding_text = f"{title} {summary}"
    embedding = get_embedding(embedding_text)

    # ── Event 매칭 (4단계) ──
    preliminary = {
        "title": title,
        "summary": summary,
        "camp": analysis["camp"],
        "category": analysis["category"],
        "actor_name": analysis.get("actor_name", ""),
        "published_at": article.get("published_at", datetime.now().isoformat()),
    }
    matched_event = match_to_event(preliminary, active_events, embedding)

    # ── 신뢰도 게이트 ──
    trust_input = {
        **preliminary,
        "source_name": source,
        "source_tier": source_tier,
    }
    trust = evaluate_trust(trust_input, all_articles)

    # ── 직책 가중치 ──
    actor_name = analysis.get("actor_name", "")
    position = politicians_positions.get(actor_name, "의원")
    pos_weight = POSITION_WEIGHT.get(position, 0.8)

    # ── archive 여부 ──
    is_archive = analysis["category"] not in SCORED_CATEGORIES

    # ── DB 저장 ──
    # title은 AI 생성 headline 사용 (저작권 보호), 원본은 ai_analysis에 보관
    ai_title = analysis.get("headline", title[:60])
    issue = {
        "title": ai_title,
        "summary": summary,
        "category": analysis["category"],
        "camp": analysis["camp"],
        "source_tier": source_tier,
        "source_url": article.get("source_url", article.get("detail_link", "")),
        "source_name": source,
        "weighted_score": 0,  # event 레벨에서 재계산
        "ai_analysis": {
            "confidence": analysis.get("confidence", 0),
            "reasoning": analysis.get("reasoning", ""),
            "category_rationale": analysis.get("category_reasoning", ""),
            "camp_reasoning": analysis.get("camp_reasoning", ""),
            "evidence_sentence": analysis.get("evidence_sentence", ""),
            "criminal_stage_reasoning": analysis.get("criminal_stage", None),
            "source_title": title,
        },
        "published_at": article.get("published_at", datetime.now().isoformat()),
        "verified": trust["verified"],
        "verification_note": trust["note"],
        "trust_level": trust["trust_level"],
        "criminal_stage": analysis.get("criminal_stage"),
        "coverage_count": len(trust["matched_sources"]) + 1,
        "headline_days": 1,
        "is_archive": is_archive,
        "position_weight": pos_weight,
        "actor_name": actor_name,
        "actor_party": analysis.get("actor_party", ""),
        "cross_verified_sources": [{"name": s["name"], "lean": s["lean"]} for s in trust["matched_sources"][:5]],
    }

    # 개별 issue 점수 (참고용, event 점수가 실제 사용됨)
    issue["weighted_score"] = calculate_score(issue)

    result = insert_issue(issue)
    if not result:
        return "skip_db"

    issue_with_id = {**issue, "id": result["id"], "headline": analysis.get("headline", "")}

    # ── Event 생성 또는 머지 ──
    if matched_event:
        merged = merge_into_event(matched_event, issue_with_id)
        if merged:
            tag = "MERGED"
        else:
            # 머지 실패 시 새 event 생성
            create_event(issue_with_id, embedding)
            tag = "NEW(merge_fail)"
    else:
        event = create_event(issue_with_id, embedding)
        if event:
            # active_events에 추가하여 이후 기사와 매칭 가능하게
            active_events.append(event)
        tag = "NEW"

    camp_label = "파랑" if analysis["camp"] == "blue" else "빨강"
    trust_mark = "H" if trust["trust_level"] == "high" else ("M" if trust["trust_level"] == "medium" else "?")
    score_tag = "SCORED" if not is_archive and issue["weighted_score"] > 0 else "ARCHIVE"
    print(f"  [{trust_mark}] [{camp_label}] [{score_tag}] [{tag}] {analysis['category']}: {title[:50]}")
    return "inserted"


def _process_batch(
    articles: list[dict],
    tier: int,
    politicians_map: dict[str, str],
    politicians_positions: dict[str, str],
    active_events: list[dict],
    all_collected: list[dict],
    stats: dict[str, int],
    limit: int = 50,
) -> None:
    for article in articles[:limit]:
        try:
            result = process_article(article, tier, politicians_map, politicians_positions, active_events, all_collected)
            stats[result] = stats.get(result, 0) + 1
        except Exception as e:
            print(f"  [error] {e}")


def run_pipeline() -> None:
    print(f"\n{'='*60}")
    print(f"민낯 크롤러 v2.0 (Event 기반): {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    # 0. 정치인 DB 동기화
    print("\n[0] 정치인 DB 동기화...")
    try:
        sync_politicians()
    except Exception as e:
        print(f"  [warn] {e}")

    politicians_map = load_politicians_map()
    politicians_positions = load_politicians_positions()
    print(f"  정치인 DB: {len(politicians_map)}명")

    # Active events 로드 (dedup 대체)
    active_events = get_active_events()
    print(f"  활성 사건: {len(active_events)}건")

    stats: dict[str, int] = {
        "inserted": 0, "skip_analysis": 0,
        "skip_db": 0, "skip_empty": 0,
    }
    all_collected: list[dict] = []

    # 1. Tier 1 — 국회 의안정보
    print("\n[1] 국회 법안 수집...")
    bills = fetch_recent_bills(days=7)
    for b in bills:
        b["published_at"] = b.get("propose_date", datetime.now().isoformat())
    all_collected.extend(bills)
    print(f"  수집: {len(bills)}건")
    _process_batch(bills, 1, politicians_map, politicians_positions, active_events, all_collected, stats)

    # 2. Tier 2 — 팩트체크
    print("\n[2] 팩트체크 수집...")
    factchecks = fetch_factchecks()
    for fc in factchecks:
        fc["published_at"] = fc.get("date", datetime.now().isoformat())
        if not fc.get("summary"):
            fc["summary"] = fc.get("title", "")
    all_collected.extend(factchecks)
    print(f"  수집: {len(factchecks)}건")
    _process_batch(factchecks, 2, politicians_map, politicians_positions, active_events, all_collected, stats)

    # 3. Tier 3 — RSS
    print("\n[3] 뉴스 RSS 수집...")
    rss_news = fetch_all_news()
    all_collected.extend(rss_news)
    print(f"  RSS: {len(rss_news)}건")
    _process_batch(rss_news, 3, politicians_map, politicians_positions, active_events, all_collected, stats, limit=30)

    # 4. Tier 3 — 네이버
    print("\n[4] 네이버 뉴스 수집...")
    naver_news = fetch_all_political_news()
    all_collected.extend(naver_news)
    _process_batch(naver_news, 3, politicians_map, politicians_positions, active_events, all_collected, stats, limit=30)

    # 5. Tier 1 — 법원
    print("\n[5] 법원 판결 수집...")
    rulings = fetch_recent_rulings()
    for r in rulings:
        r["published_at"] = r.get("date", datetime.now().isoformat())
    all_collected.extend(rulings)
    print(f"  수집: {len(rulings)}건")
    _process_batch(rulings, 1, politicians_map, politicians_positions, active_events, all_collected, stats)

    # 6. Tier 3 — 디시 실베 트렌드 → 네이버 뉴스
    print("\n[6] 디시 실베 트렌드...")
    trending = fetch_trending_news()
    all_collected.extend(trending)
    _process_batch(trending, 3, politicians_map, politicians_positions, active_events, all_collected, stats, limit=20)

    # 7. 미검증 Event 자동 승격
    print("\n[7] 교차검증 자동 승격...")
    run_auto_verify()

    # 8. 비활성화 + 스냅샷
    print("\n[8] 비활성화 + 스냅샷...")
    deactivate_old_events()
    generate_daily_snapshot()

    print(f"\n{'='*60}")
    print(f"결과: 저장 {stats['inserted']} | 분석스킵 {stats['skip_analysis']} | DB스킵 {stats['skip_db']}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    run_pipeline()
