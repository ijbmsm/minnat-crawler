"""점수 산출 엔진 + 일별 스냅샷 생성"""
import math
from datetime import datetime, date

from config import (
    CATEGORY_WEIGHT,
    SEVERITY_MULTIPLIER,
    IMPACT_MULTIPLIER,
    POSITIVE_CATEGORIES,
    UNSCORED_CATEGORIES,
)
from db import get_client, save_snapshot

DECAY_LAMBDA = 0.01


def calculate_score(
    category: str,
    severity: str,
    impact_scope: str,
    published_at: str,
    source_tier: int,
    verified: bool,
) -> float:
    """개별 이슈의 가중 점수를 계산한다."""
    # 점수 미반영 카테고리
    if category in UNSCORED_CATEGORIES:
        return 0.0

    # Tier 4는 절대 미반영
    if source_tier == 4:
        return 0.0
    # Tier 3: 교차검증 완료(verified)된 것만 반영
    if source_tier == 3 and not verified:
        return 0.0

    base = CATEGORY_WEIGHT.get(category, 0)
    sev = SEVERITY_MULTIPLIER.get(severity, 1.0)
    imp = IMPACT_MULTIPLIER.get(impact_scope, 1.0)

    # 시간 감쇠
    try:
        pub_dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        days = max(0, (datetime.now(pub_dt.tzinfo) - pub_dt).total_seconds() / 86400)
    except (ValueError, TypeError):
        days = 0

    decay = math.exp(-DECAY_LAMBDA * days)

    return round(base * sev * imp * decay, 2)


def generate_daily_snapshot() -> dict:
    """오늘 날짜 기준 진영별 점수 스냅샷을 생성한다."""
    client = get_client()

    # 전체 이슈 조회
    result = client.table("issues").select("*").execute()
    issues = result.data

    blue_neg = 0.0
    blue_pos = 0.0
    red_neg = 0.0
    red_pos = 0.0

    for issue in issues:
        score = calculate_score(
            category=issue["category"],
            severity=issue["severity"],
            impact_scope=issue["impact_scope"],
            published_at=issue["published_at"],
            source_tier=issue["source_tier"],
            verified=issue["verified"],
        )
        if score == 0:
            continue

        is_positive = issue["category"] in POSITIVE_CATEGORIES

        if issue["camp"] == "blue":
            if is_positive:
                blue_pos += score
            else:
                blue_neg += score
        else:
            if is_positive:
                red_pos += score
            else:
                red_neg += score

    blue_net = blue_neg - blue_pos
    red_net = red_neg - red_pos
    total = blue_net + red_net

    blue_pct = round((blue_net / total) * 100) if total > 0 else 50
    red_pct = 100 - blue_pct if total > 0 else 50

    snapshot = {
        "date": date.today().isoformat(),
        "blue_negative": round(blue_neg, 2),
        "blue_positive": round(blue_pos, 2),
        "red_negative": round(red_neg, 2),
        "red_positive": round(red_pos, 2),
        "blue_net": round(blue_net, 2),
        "red_net": round(red_net, 2),
        "blue_pct": blue_pct,
        "red_pct": red_pct,
    }

    save_snapshot(snapshot)
    print(f"[scorer] 스냅샷 저장: 파랑 {blue_pct}% vs 빨강 {red_pct}%")
    return snapshot


if __name__ == "__main__":
    snapshot = generate_daily_snapshot()
    print(f"결과: {snapshot}")
