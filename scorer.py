"""점수 산출 v1.1 + 스냅샷 생성

## 정본은 웹이다 (하네스 M-01, 2026-09-25 결정)

점수가 두 곳에서 계산된다. 크롤러가 weighted_score 를 DB 에 넣고(매일 찍는
진영 스냅샷의 근거), 웹이 화면에서 다시 계산한다(홈·이슈목록·정치인 페이지·정렬).
규칙이 갈려도 화면 어디에도 그 사실이 안 보인다.

    저장 (크롤러) = min(base × diversity × position_weight × 100, 100)
    화면 (웹)     = 저장값 × viewDecay(view)

decay 는 저장하지 않는다. 뷰별 곡선이 4개(hot·recent·midterm·alltime)고
"지금" 에 의존해서, 저장하면 다음 날 틀린 값이 된다. 렌더 시점에만 곱한다.

## 구현은 하나다

예전에는 크롤러 안에만 둘이었다 — scorer.calculate_score(기사)와
event_manager.recalculate_event_score(사건). 웹까지 합치면 같은 공식이 넷이었고,
그중 크롤러 둘에는 diversity 와 100 상한이 빠져 있었다(독스트링에는 있었다).
단독 보도면 DB 쪽이 약 1.43배 높게 저장됐다.

이제 둘 다 score_core() 를 통과한다. 공식을 고칠 자리는 여기 하나다.
"""
from datetime import date
from db import get_client, save_snapshot
from config import SCORED_CATEGORIES, CRIMINAL_STAGE_WEIGHT


def media_diversity(sources: list[dict] | None) -> float:
    """진영 다양도 0.7(단독) / 1.0(2진영) / 1.3(3진영).

    웹 score.ts 의 diversityMultiplier 와 같은 규칙이어야 한다.
    하네스 score-parity 가 계단값을 대조한다.
    """
    leans = {s.get("lean", "unknown") for s in (sources or [])}
    leans.discard("unknown")
    if len(leans) >= 3:
        return 1.3
    if len(leans) >= 2:
        return 1.0
    return 0.7


def score_core(
    *,
    category: str,
    source_tier: int,
    verified: bool,
    coverage_count: int,
    criminal_stage: str | None,
    headline_days: int,
    position_weight: float,
    diversity: float,
) -> float:
    """웹 공식에서 decay 를 뺀 부분. 기사도 사건도 여기를 통과한다."""
    if category not in SCORED_CATEGORIES:
        return 0.0
    if source_tier == 4:
        return 0.0
    if source_tier == 3 and not verified:
        return 0.0

    coverage_norm = min((coverage_count or 1) / 15, 1.0)

    official_stage = 5.0
    if category == "criminal_conviction":
        official_stage = float(CRIMINAL_STAGE_WEIGHT.get(criminal_stage or "", 0))
        if official_stage == 0:
            return 0.0
    stage_norm = min(official_stage / 10, 1.0)

    headline_norm = min((headline_days or 1) / 20, 1.0)

    base = coverage_norm * 0.40 + stage_norm * 0.35 + headline_norm * 0.25
    # 웹: Math.min(raw * 100, 100). 상한이 없으면 화면과 값이 갈린다.
    return round(min(base * diversity * position_weight * 100, 100), 2)


def calculate_score(issue: dict) -> float:
    """기사 하나의 점수. 사건 점수가 실제로 쓰이고 이건 참고용이다."""
    return score_core(
        category=issue.get("category", ""),
        source_tier=issue.get("source_tier", 3),
        verified=issue.get("verified", False),
        coverage_count=issue.get("coverage_count", 1),
        criminal_stage=issue.get("criminal_stage"),
        headline_days=issue.get("headline_days", 1),
        position_weight=issue.get("position_weight", 0.8),
        # 기사 단위에는 media_diversity_score 컬럼이 없다. 웹과 같이
        # cross_verified_sources 에서 계산한다.
        diversity=media_diversity(issue.get("cross_verified_sources")),
    )


def generate_daily_snapshot() -> dict:
    """일별 스냅샷 생성 — Event(issue_clusters) 기반으로 집계.

    Event가 있는 경우 event.weighted_score를 사용하고,
    Event가 없는 기존 issue는 개별 점수를 사용한다 (하위 호환).
    """
    client = get_client()

    blue_score = 0.0
    red_score = 0.0
    blue_count = 0
    red_count = 0

    # 1. Event 기반 점수 집계
    events_result = client.table("issue_clusters").select(
        "weighted_score, camp"
    ).execute()
    for event in events_result.data:
        score = float(event.get("weighted_score", 0))
        if score <= 0:
            continue
        camp = event.get("camp")
        if camp == "blue":
            blue_score += score
            blue_count += 1
        elif camp == "red":
            red_score += score
            red_count += 1

    # 2. Event에 속하지 않은 기존 issue 집계 (하위 호환)
    orphan_result = client.table("issues").select("*").is_("event_id", "null").execute()
    for issue in orphan_result.data:
        score = calculate_score(issue)
        if score <= 0:
            continue
        if issue["camp"] == "blue":
            blue_score += score
            blue_count += 1
        else:
            red_score += score
            red_count += 1

    total = blue_score + red_score
    blue_pct = round((blue_score / total) * 100) if total > 0 else 50
    red_pct = 100 - blue_pct if total > 0 else 50

    snapshot = {
        "date": date.today().isoformat(),
        "blue_score": round(blue_score, 2),
        "red_score": round(red_score, 2),
        "blue_pct": blue_pct,
        "red_pct": red_pct,
        "blue_count": blue_count,
        "red_count": red_count,
    }

    save_snapshot(snapshot)
    print(f"[scorer] 스냅샷: 파랑 {blue_pct}% ({blue_count}건) vs 빨강 {red_pct}% ({red_count}건)")
    return snapshot
