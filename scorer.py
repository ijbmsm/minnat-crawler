"""점수 산출 v1.1 + 스냅샷 생성"""
from datetime import date
from db import get_client, save_snapshot
from config import SCORED_CATEGORIES, CRIMINAL_STAGE_WEIGHT


def calculate_score(issue: dict) -> float:
    """v1.1 점수 공식:
    base = coverage_norm × 0.40 + stage_norm × 0.35 + headline_norm × 0.25
    final = base × diversity × position_weight
    """
    category = issue.get("category", "")
    if category not in SCORED_CATEGORIES:
        return 0.0

    if issue.get("source_tier", 3) == 4:
        return 0.0
    if issue.get("source_tier", 3) == 3 and not issue.get("verified", False):
        return 0.0

    # 보도량 정규화
    coverage = issue.get("coverage_count", 1)
    coverage_norm = min(coverage / 15, 1.0)

    # 공식 처리 단계
    official_stage = 5.0
    if category == "criminal_conviction":
        stage = issue.get("criminal_stage", "")
        official_stage = float(CRIMINAL_STAGE_WEIGHT.get(stage, 0))
        if official_stage == 0:
            return 0.0
    stage_norm = min(official_stage / 10, 1.0)

    # 헤드라인 지속
    headline = issue.get("headline_days", 1)
    headline_norm = min(headline / 20, 1.0)

    base = coverage_norm * 0.40 + stage_norm * 0.35 + headline_norm * 0.25

    # 직책 가중치
    pos_weight = issue.get("position_weight", 0.8)

    return round(base * pos_weight * 100, 2)


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
