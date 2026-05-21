"""민낯 크롤러 v1.1

파이프라인:
0. 정치인 DB 동기화
1. 다중 소스 동시 수집
2. 신뢰도 게이트
3. AI 분류 (v1.1 카테고리)
4. 표현 자동 검수
5. DB 저장
6. 미검증 이슈 자동 승격
7. 스냅샷 생성
"""
from datetime import datetime

from crawlers.assembly import fetch_recent_bills
from crawlers.factcheck import fetch_factchecks
from crawlers.news import fetch_all_news
from crawlers.naver_news import fetch_all_political_news
from crawlers.court import fetch_recent_rulings
from analyzer import analyze_article
from trust_gate import evaluate_trust
from dedup import is_duplicate
from expression_filter import filter_expression, needs_unconfirmed_label
from scorer import calculate_score, generate_daily_snapshot
from auto_verify import run_auto_verify
from sync_politicians import sync_to_db as sync_politicians
from db import insert_issue, get_client
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


def load_recent_issues() -> list[dict]:
    client = get_client()
    result = (
        client.table("issues")
        .select("id, title, summary, camp, category, actor_name, published_at")
        .order("published_at", desc=True)
        .limit(300)
        .execute()
    )
    return result.data


def process_article(
    article: dict,
    source_tier: int,
    politicians_map: dict[str, str],
    politicians_positions: dict[str, str],
    existing_issues: list[dict],
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

    # ── 중복 감지 ──
    preliminary = {
        "title": title,
        "summary": analysis.get("summary", content[:300]),
        "camp": analysis["camp"],
        "category": analysis["category"],
        "actor_name": analysis.get("actor_name", ""),
        "published_at": article.get("published_at", datetime.now().isoformat()),
    }
    dup_found, dup_info = is_duplicate(preliminary, existing_issues)
    if dup_found and dup_info:
        return "skip_duplicate"

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
    issue = {
        "title": title,
        "summary": analysis.get("summary", content[:300]),
        "category": analysis["category"],
        "camp": analysis["camp"],
        "source_tier": source_tier,
        "source_url": article.get("source_url", article.get("detail_link", "")),
        "source_name": source,
        "weighted_score": 0,  # 나중에 계산
        "ai_analysis": {
            "confidence": analysis.get("confidence", 0),
            "reasoning": analysis.get("reasoning", ""),
            "category_rationale": analysis.get("category_reasoning", ""),
            "camp_reasoning": analysis.get("camp_reasoning", ""),
            "evidence_sentence": analysis.get("evidence_sentence", ""),
            "criminal_stage_reasoning": analysis.get("criminal_stage", None),
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

    # 점수 계산
    issue["weighted_score"] = calculate_score(issue)

    result = insert_issue(issue)
    if result:
        camp_label = "파랑" if analysis["camp"] == "blue" else "빨강"
        trust_mark = "H" if trust["trust_level"] == "high" else ("M" if trust["trust_level"] == "medium" else "?")
        tag = "SCORED" if not is_archive and issue["weighted_score"] > 0 else "ARCHIVE"
        print(f"  [{trust_mark}] [{camp_label}] [{tag}] {analysis['category']}: {title[:50]} (점수: {issue['weighted_score']})")
        existing_issues.append(preliminary | {"id": result.get("id", "")})
        return "inserted"
    return "skip_db"


def _process_batch(
    articles: list[dict],
    tier: int,
    politicians_map: dict[str, str],
    politicians_positions: dict[str, str],
    existing_issues: list[dict],
    all_collected: list[dict],
    stats: dict[str, int],
    limit: int = 50,
) -> None:
    for article in articles[:limit]:
        try:
            result = process_article(article, tier, politicians_map, politicians_positions, existing_issues, all_collected)
            stats[result] = stats.get(result, 0) + 1
        except Exception as e:
            print(f"  [error] {e}")


def run_pipeline() -> None:
    print(f"\n{'='*60}")
    print(f"민낯 크롤러 v1.1: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
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

    existing_issues = load_recent_issues()
    print(f"  기존 이슈: {len(existing_issues)}건")

    stats: dict[str, int] = {
        "inserted": 0, "skip_analysis": 0, "skip_duplicate": 0,
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
    _process_batch(bills, 1, politicians_map, politicians_positions, existing_issues, all_collected, stats)

    # 2. Tier 2 — 팩트체크
    print("\n[2] 팩트체크 수집...")
    factchecks = fetch_factchecks()
    for fc in factchecks:
        fc["published_at"] = fc.get("date", datetime.now().isoformat())
        if not fc.get("summary"):
            fc["summary"] = fc.get("title", "")
    all_collected.extend(factchecks)
    print(f"  수집: {len(factchecks)}건")
    _process_batch(factchecks, 2, politicians_map, politicians_positions, existing_issues, all_collected, stats)

    # 3. Tier 3 — RSS
    print("\n[3] 뉴스 RSS 수집...")
    rss_news = fetch_all_news()
    all_collected.extend(rss_news)
    print(f"  RSS: {len(rss_news)}건")
    _process_batch(rss_news, 3, politicians_map, politicians_positions, existing_issues, all_collected, stats, limit=30)

    # 4. Tier 3 — 네이버
    print("\n[4] 네이버 뉴스 수집...")
    naver_news = fetch_all_political_news()
    all_collected.extend(naver_news)
    _process_batch(naver_news, 3, politicians_map, politicians_positions, existing_issues, all_collected, stats, limit=30)

    # 5. Tier 1 — 법원
    print("\n[5] 법원 판결 수집...")
    rulings = fetch_recent_rulings()
    for r in rulings:
        r["published_at"] = r.get("date", datetime.now().isoformat())
    all_collected.extend(rulings)
    print(f"  수집: {len(rulings)}건")
    _process_batch(rulings, 1, politicians_map, politicians_positions, existing_issues, all_collected, stats)

    # 6. 미검증 자동 승격
    print("\n[6] 교차검증 자동 승격...")
    run_auto_verify()

    # 7. 스냅샷
    print("\n[7] 스냅샷...")
    generate_daily_snapshot()

    print(f"\n{'='*60}")
    print(f"결과: 저장 {stats['inserted']} | 분석스킵 {stats['skip_analysis']} | 중복 {stats['skip_duplicate']} | DB스킵 {stats['skip_db']}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    run_pipeline()
