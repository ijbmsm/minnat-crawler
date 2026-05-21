"""편향 자동 감사 — 주간 cron으로 실행

진영별 점수 분포, 카테고리별 편향, 매체별 분류 분포를 분석하고
통계적으로 유의미한 차이가 있으면 알림.
"""
import math
from datetime import datetime, timedelta
from db import get_client


def welch_t_test(mean1: float, std1: float, n1: int, mean2: float, std2: float, n2: int) -> float:
    """Welch's t-test 간이 구현. t-statistic 반환."""
    if n1 < 2 or n2 < 2:
        return 0.0
    se1 = (std1 ** 2) / n1
    se2 = (std2 ** 2) / n2
    denom = math.sqrt(se1 + se2)
    if denom == 0:
        return 0.0
    return (mean1 - mean2) / denom


def run_audit(days: int = 30) -> dict:
    """편향 감사 리포트를 생성한다."""
    client = get_client()
    since = (datetime.now() - timedelta(days=days)).isoformat()

    result = (
        client.table("issues")
        .select("category, camp, weighted_score, source_name, validation_status")
        .gte("created_at", since)
        .execute()
    )
    issues = result.data
    if not issues:
        print("[audit] 최근 이슈 없음")
        return {}

    # ── 1. 진영별 전체 점수 분포 ──
    blue_scores = [i["weighted_score"] for i in issues if i["camp"] == "blue" and i["weighted_score"] > 0]
    red_scores = [i["weighted_score"] for i in issues if i["camp"] == "red" and i["weighted_score"] > 0]

    def stats(scores: list[float]) -> tuple[float, float, int]:
        n = len(scores)
        if n == 0:
            return 0.0, 0.0, 0
        mean = sum(scores) / n
        variance = sum((x - mean) ** 2 for x in scores) / max(n - 1, 1)
        return mean, math.sqrt(variance), n

    blue_mean, blue_std, blue_n = stats(blue_scores)
    red_mean, red_std, red_n = stats(red_scores)
    t_stat = welch_t_test(blue_mean, blue_std, blue_n, red_mean, red_std, red_n)

    print(f"\n{'='*50}")
    print(f"편향 감사 리포트 ({days}일)")
    print(f"{'='*50}")
    print(f"\n[1] 진영별 점수 분포")
    print(f"  파랑: mean={blue_mean:.2f}, std={blue_std:.2f}, n={blue_n}")
    print(f"  빨강: mean={red_mean:.2f}, std={red_std:.2f}, n={red_n}")
    print(f"  Welch's t = {t_stat:.3f}", end="")
    if abs(t_stat) > 2.0:
        print(" ⚠️ 유의미한 차이 (|t| > 2.0)")
    else:
        print(" ✓ 정상 범위")

    # ── 2. 카테고리별 진영 분포 ──
    print(f"\n[2] 카테고리별 진영 분포")
    cat_camp: dict[str, dict[str, int]] = {}
    for i in issues:
        cat = i["category"]
        camp = i["camp"]
        if cat not in cat_camp:
            cat_camp[cat] = {"blue": 0, "red": 0}
        cat_camp[cat][camp] += 1

    for cat, camps in sorted(cat_camp.items()):
        total = camps["blue"] + camps["red"]
        blue_ratio = camps["blue"] / total * 100 if total > 0 else 0
        imbalance = "⚠️" if abs(blue_ratio - 50) > 30 else "  "
        print(f"  {imbalance} {cat:20s}: 파랑 {camps['blue']:3d} ({blue_ratio:.0f}%) | 빨강 {camps['red']:3d}")

    # ── 3. 매체별 분류 분포 ──
    print(f"\n[3] 매체별 수집 건수")
    source_count: dict[str, int] = {}
    for i in issues:
        src = i["source_name"]
        source_count[src] = source_count.get(src, 0) + 1

    for src, cnt in sorted(source_count.items(), key=lambda x: -x[1]):
        print(f"  {src:25s}: {cnt}건")

    # ── 4. 검증 상태 분포 ──
    print(f"\n[4] 검증 상태")
    status_count: dict[str, int] = {}
    for i in issues:
        s = i.get("validation_status", "unknown")
        status_count[s] = status_count.get(s, 0) + 1

    for status, cnt in sorted(status_count.items()):
        print(f"  {status:15s}: {cnt}건")

    print(f"\n총 이슈: {len(issues)}건")
    print(f"{'='*50}\n")

    return {
        "blue_mean": blue_mean,
        "red_mean": red_mean,
        "t_statistic": t_stat,
        "total_issues": len(issues),
        "bias_warning": abs(t_stat) > 2.0,
    }


if __name__ == "__main__":
    run_audit()
