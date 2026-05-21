"""민낯 크롤러 메인 파이프라인 v3

파이프라인:
0. 정치인 DB 동기화
1. 각 소스에서 뉴스/이슈 수집 (국회, 팩트체크, RSS, 네이버, 법원)
2. 중복 감지
3. Claude Haiku 분류 (정치인 DB 주입 + 입법 5단계 결정론적 판정)
4. 2차 검증 (validator)
5. 교차검증 (좌우 매체 다양성)
6. Supabase 저장
7. 일별 스냅샷 생성
"""
from datetime import datetime

from crawlers.assembly import fetch_recent_bills
from crawlers.factcheck import fetch_factchecks
from crawlers.news import fetch_all_news
from crawlers.naver_news import fetch_all_political_news
from crawlers.court import fetch_recent_rulings
from sync_politicians import sync_to_db as sync_politicians
from analyzer import analyze_article
from validator import validate_issue
from dedup import is_duplicate
from cross_verify import cross_verify
from scorer import calculate_score, generate_daily_snapshot
from db import insert_issue, get_client
from config import CATEGORY_WEIGHT


def load_politicians_map() -> dict[str, str]:
    """DB에서 정치인 이름 → camp 매핑을 로드한다."""
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


def load_recent_issues() -> list[dict]:
    """최근 7일 이슈를 DB에서 로드 (중복 감지용)."""
    client = get_client()
    result = (
        client.table("issues")
        .select("id, title, summary, camp, category, published_at")
        .order("published_at", desc=True)
        .limit(200)
        .execute()
    )
    return result.data


def process_article(
    article: dict,
    source_tier: int,
    politicians_map: dict[str, str],
    existing_issues: list[dict],
    all_articles: list[dict],
) -> str:
    """단일 기사를 분석 → 검증 → 저장. 결과 상태를 반환."""
    title = article.get("title", "")
    content = article.get("summary", article.get("content", ""))
    source = article.get("source", "")

    if not title:
        return "skip_empty"

    # ── 1. AI 분석 (정치인 DB 주입) ──
    analysis = analyze_article(title, content, source, politicians_map)
    if not analysis:
        return "skip_analysis"

    # ── 2. 중복 감지 ──
    preliminary_issue = {
        "title": title,
        "summary": analysis.get("summary", content[:300]),
        "camp": analysis["camp"],
        "category": analysis["category"],
        "published_at": article.get("published_at", datetime.now().isoformat()),
    }
    dup_found, dup_info = is_duplicate(preliminary_issue, existing_issues)
    if dup_found and dup_info:
        if dup_info["camp_conflict"]:
            print(f"  [conflict] 같은 이슈 진영 충돌: {title[:40]} (기존: {dup_info['existing_camp']}, 새: {analysis['camp']})")
            return "skip_conflict"
        else:
            return "skip_duplicate"

    # ── 3. 2차 검증 ──
    validation = validate_issue(analysis, article, politicians_map)

    # ── 4. 교차검증 (Tier 3) ──
    cross_result = {"verified": True, "cross_verified_sources": [], "verification_note": ""}
    if source_tier == 3:
        cross_result = cross_verify(
            {"title": title, "summary": content, "source_name": source, "source_url": article.get("source_url", "")},
            all_articles,
        )

    # ── 5. 최종 verified 판정 ──
    is_verified = False
    if source_tier <= 2:
        is_verified = True
    elif source_tier == 3:
        is_verified = cross_result["verified"]

    if not validation.passed:
        is_verified = False

    # ── 6. 점수 계산 ──
    score = calculate_score(
        category=analysis["category"],
        severity=analysis["severity"],
        impact_scope=analysis["impact_scope"],
        published_at=article.get("published_at", datetime.now().isoformat()),
        source_tier=source_tier,
        verified=is_verified,
    )

    # ── 7. DB 저장 ──
    issue = {
        "title": title,
        "summary": analysis.get("summary", content[:300]),
        "category": analysis["category"],
        "camp": analysis["camp"],
        "severity": analysis["severity"],
        "impact_scope": analysis["impact_scope"],
        "source_tier": source_tier,
        "source_url": article.get("source_url", article.get("detail_link", "")),
        "source_name": source,
        "raw_score": CATEGORY_WEIGHT.get(analysis["category"], 0),
        "weighted_score": score,
        "ai_analysis": {
            "confidence": analysis.get("confidence", 0),
            "reasoning": analysis.get("reasoning", ""),
            "category_rationale": analysis.get("category_reasoning", ""),
            "severity_rationale": analysis.get("severity_reasoning", ""),
            "camp_reasoning": analysis.get("camp_reasoning", ""),
            "is_actionable_result": analysis.get("is_actionable_result", False),
        },
        "published_at": article.get("published_at", datetime.now().isoformat()),
        "verified": is_verified,
        "actor_name": analysis.get("actor_name", ""),
        "actor_party": analysis.get("actor_party", ""),
        "validation_status": validation.action.replace("insert_unverified", "passed").replace("insert", "passed").replace("queue_review", "flagged"),
        "validation_errors": validation.errors + validation.warnings,
        "cross_verified_sources": cross_result.get("cross_verified_sources", []),
        "verification_note": cross_result.get("verification_note", ""),
    }

    result = insert_issue(issue)
    if result:
        camp_label = "파랑" if analysis["camp"] == "blue" else "빨강"
        status = "V" if is_verified else "?"
        flag = " [FLAGGED]" if not validation.passed else ""
        print(f"  [{status}] [{camp_label}] {analysis['category']}: {title[:50]} (점수: {score}){flag}")
        # 중복 감지용 리스트에 추가
        existing_issues.append(preliminary_issue | {"id": result.get("id", "")})
        return "inserted"
    else:
        return "skip_db"


def _process_batch(
    articles: list[dict],
    tier: int,
    politicians_map: dict[str, str],
    existing_issues: list[dict],
    all_collected: list[dict],
    stats: dict[str, int],
    limit: int = 50,
) -> None:
    """기사 배치를 처리한다."""
    for article in articles[:limit]:
        try:
            result = process_article(article, tier, politicians_map, existing_issues, all_collected)
            stats[result] = stats.get(result, 0) + 1
        except Exception as e:
            print(f"  [error] {e}")


def run_pipeline() -> None:
    """전체 크롤링 파이프라인 실행"""
    print(f"\n{'='*60}")
    print(f"민낯 크롤러 v3: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    # 0. 정치인 DB 동기화
    print("\n[0/5] 정치인 DB 동기화 중...")
    try:
        sync_politicians()
    except Exception as e:
        print(f"  [warn] 동기화 실패 (기존 DB 사용): {e}")

    # 준비
    politicians_map = load_politicians_map()
    print(f"정치인 DB: {len(politicians_map)}명 로드")

    existing_issues = load_recent_issues()
    print(f"기존 이슈: {len(existing_issues)}건 로드 (중복 감지용)")

    stats: dict[str, int] = {
        "inserted": 0, "skip_analysis": 0, "skip_duplicate": 0,
        "skip_conflict": 0, "skip_db": 0, "skip_empty": 0,
    }
    all_collected: list[dict] = []

    # 1. 국회 의안정보시스템 (Tier 1)
    print("\n[1/5] 국회 법안 수집 중...")
    bills = fetch_recent_bills(days=7)
    print(f"  수집: {len(bills)}건")
    for bill in bills:
        bill["published_at"] = bill.get("propose_date", datetime.now().isoformat())
    all_collected.extend(bills)
    _process_batch(bills, 1, politicians_map, existing_issues, all_collected, stats)

    # 2. 팩트체크 매체 (Tier 2) — JTBC, MBC, KBS, SBS, 연합
    print("\n[2/5] 팩트체크 수집 중...")
    factchecks = fetch_factchecks()
    print(f"  수집: {len(factchecks)}건")
    for fc in factchecks:
        fc["published_at"] = fc.get("date", datetime.now().isoformat())
        if not fc.get("summary"):
            fc["summary"] = fc.get("title", "")
    all_collected.extend(factchecks)
    _process_batch(factchecks, 2, politicians_map, existing_issues, all_collected, stats)

    # 3. 뉴스 RSS (Tier 3)
    print("\n[3/5] 뉴스 RSS 수집 중...")
    rss_news = fetch_all_news()
    all_collected.extend(rss_news)
    print(f"  RSS: {len(rss_news)}건")
    _process_batch(rss_news, 3, politicians_map, existing_issues, all_collected, stats, limit=30)

    # 4. 네이버 검색 API (Tier 3)
    print("\n[4/5] 네이버 뉴스 수집 중...")
    naver_news = fetch_all_political_news()
    all_collected.extend(naver_news)
    _process_batch(naver_news, 3, politicians_map, existing_issues, all_collected, stats, limit=30)

    # 5. 법원 판결 (Tier 1)
    print("\n[5/5] 법원 판결 수집 중...")
    rulings = fetch_recent_rulings()
    print(f"  수집: {len(rulings)}건")
    for ruling in rulings:
        ruling["published_at"] = ruling.get("date", datetime.now().isoformat())
    all_collected.extend(rulings)
    _process_batch(rulings, 1, politicians_map, existing_issues, all_collected, stats)

    # 스냅샷
    print("\n[스냅샷] 일별 점수 계산 중...")
    generate_daily_snapshot()

    # 결과
    print(f"\n{'='*60}")
    print(f"결과:")
    print(f"  신규 저장: {stats['inserted']}건")
    print(f"  분석 스킵: {stats['skip_analysis']}건")
    print(f"  중복 스킵: {stats['skip_duplicate']}건")
    print(f"  진영 충돌: {stats['skip_conflict']}건")
    print(f"  DB 스킵:   {stats['skip_db']}건")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    run_pipeline()
