"""민낯 크롤러 메인 파이프라인

실행 순서:
1. 각 소스에서 뉴스/이슈 수집
2. Claude Haiku로 분류/심각도 분석
3. 점수 산출 후 Supabase 저장
4. 일별 스냅샷 생성
"""
import sys
from datetime import datetime

from crawlers.assembly import fetch_recent_bills
from crawlers.factcheck import fetch_recent_factchecks
from crawlers.news import fetch_all_news, find_cross_verified
from crawlers.court import fetch_recent_rulings
from analyzer import analyze_article
from scorer import calculate_score, generate_daily_snapshot
from db import insert_issue, get_parties
from config import CATEGORY_WEIGHT


def process_article(article: dict, source_tier: int, verified: bool = False) -> bool:
    """단일 기사를 분석하고 DB에 저장한다."""
    title = article.get("title", "")
    content = article.get("summary", article.get("content", ""))
    source = article.get("source", "")

    if not title:
        return False

    # AI 분석
    analysis = analyze_article(title, content, source)
    if not analysis:
        print(f"  [skip] 분석 실패: {title[:40]}")
        return False

    # camp 검증 — blue/red만 허용, 그 외(neutral 등)는 스킵
    if analysis["camp"] not in ("blue", "red"):
        print(f"  [skip] 진영 판별 불가 ({analysis['camp']}): {title[:40]}")
        return False

    # confidence 낮으면 미검증 상태로 저장
    is_verified = verified or analysis.get("confidence", 0) >= 0.7

    # 점수 계산
    score = calculate_score(
        category=analysis["category"],
        severity=analysis["severity"],
        impact_scope=analysis["impact_scope"],
        published_at=article.get("published_at", datetime.now().isoformat()),
        source_tier=source_tier,
        verified=is_verified,
    )

    # DB 저장
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
            "category_rationale": analysis.get("category_rationale", ""),
            "severity_rationale": analysis.get("severity_rationale", ""),
        },
        "published_at": article.get("published_at", datetime.now().isoformat()),
        "verified": is_verified,
    }

    result = insert_issue(issue)
    if result:
        camp_label = "파랑" if analysis["camp"] == "blue" else "빨강"
        print(f"  [new] [{camp_label}] {analysis['category']}: {title[:50]} (점수: {score})")
        return True
    else:
        return False  # 중복


def run_pipeline() -> None:
    """전체 크롤링 파이프라인 실행"""
    print(f"\n{'='*60}")
    print(f"민낯 크롤러 실행: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    total_new = 0

    # 1. 국회 의안정보시스템 (Tier 1)
    print("\n[1/4] 국회 법안 수집 중...")
    bills = fetch_recent_bills(days=7)
    print(f"  수집: {len(bills)}건")
    for bill in bills:
        try:
            bill["published_at"] = bill.get("propose_date", datetime.now().isoformat())
            if process_article(bill, source_tier=1, verified=True):
                total_new += 1
        except Exception as e:
            print(f"  [error] {e}")
            continue

    # 2. SNU 팩트체크 (Tier 2)
    print("\n[2/4] 팩트체크 수집 중...")
    factchecks = fetch_recent_factchecks()
    print(f"  수집: {len(factchecks)}건")
    for fc in factchecks:
        try:
            fc["published_at"] = fc.get("date", datetime.now().isoformat())
            fc["summary"] = f"팩트체크 결과: {fc.get('verdict', '확인 중')}"
            if process_article(fc, source_tier=2, verified=True):
                total_new += 1
        except Exception as e:
            print(f"  [error] {e}")
            continue

    # 3. 뉴스 RSS (Tier 3 — 교차검증)
    print("\n[3/4] 뉴스 수집 중...")
    all_news = fetch_all_news()
    verified_news = find_cross_verified(all_news)
    print(f"  교차검증 통과: {len(verified_news)}건 / 전체: {len(all_news)}건")
    for article in verified_news:
        try:
            if process_article(article, source_tier=3, verified=True):
                total_new += 1
        except Exception as e:
            print(f"  [error] {e}")
            continue

    # 교차검증 안 된 뉴스는 미검증 상태로 저장
    unverified_news = [n for n in all_news if not n.get("cross_verified")]
    for article in unverified_news[:20]:  # 상위 20건만
        try:
            if process_article(article, source_tier=3, verified=False):
                total_new += 1
        except Exception as e:
            print(f"  [error] {e}")
            continue

    # 4. 법원 판결 (Tier 1)
    print("\n[4/4] 법원 판결 수집 중...")
    rulings = fetch_recent_rulings()
    print(f"  수집: {len(rulings)}건")
    for ruling in rulings:
        try:
            ruling["published_at"] = ruling.get("date", datetime.now().isoformat())
            if process_article(ruling, source_tier=1, verified=True):
                total_new += 1
        except Exception as e:
            print(f"  [error] {e}")
            continue

    # 5. 일별 스냅샷 생성
    print("\n[스냅샷] 일별 점수 계산 중...")
    generate_daily_snapshot()

    print(f"\n{'='*60}")
    print(f"완료: 신규 이슈 {total_new}건 저장")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    run_pipeline()
