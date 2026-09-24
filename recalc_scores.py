"""저장된 점수를 현재 공식으로 다시 계산한다.

## 왜 필요한가

크롤러가 저장하던 점수에 진영 다양도(0.7~1.3)와 100 상한이 빠져 있었다.
웹은 둘 다 곱한다. 그래서 같은 사건이 DB 와 화면에서 다른 값을 가졌고,
단독 보도면 DB 쪽이 약 1.43배 높았다 (2026-09-25 하네스 M-01 이 잡았다).

코드는 고쳤다(scorer.score_core 하나로 통합). 이미 저장된 값은 여기서 맞춘다.

## 과거 스냅샷은 건드리지 않는다

score_snapshots 는 **발행 시점의 기록**이다. 소급해서 고치면 그날 실제로
내보낸 숫자와 기록이 달라진다. 지금 5행이 있고 전부 같은 값인데(크레딧 소진으로
9/22~24 저장이 0건이었다) 그래도 원칙은 지킨다. 이 스크립트는
issues.weighted_score 와 issue_clusters.weighted_score 만 고친다.

공식이 바뀐 날 이후로 스냅샷 값이 한 번 점프한다. 그건 왜곡이 아니라 사실이다.

    python recalc_scores.py            # 얼마나 달라지는지만 본다
    python recalc_scores.py --apply    # 실제로 갱신한다
"""
import argparse
import sys

from db import get_client
from scorer import score_core, media_diversity

_EVENT_COLS = ("id, category, source_tier, verified, coverage_count, criminal_stage, "
               "headline_days, position_weight, media_diversity_score, "
               "cross_verified_sources, weighted_score")
_ISSUE_COLS = ("id, category, source_tier, verified, coverage_count, criminal_stage, "
               "headline_days, position_weight, cross_verified_sources, weighted_score")


def _event_score(e: dict) -> float:
    return score_core(
        category=e.get("category", ""),
        source_tier=e.get("source_tier", 3),
        verified=e.get("verified", False),
        coverage_count=e.get("coverage_count", 1),
        criminal_stage=e.get("criminal_stage"),
        headline_days=e.get("headline_days", 1),
        position_weight=e.get("position_weight", 0.8),
        diversity=e.get("media_diversity_score")
        or media_diversity(e.get("cross_verified_sources")),
    )


def _issue_score(i: dict) -> float:
    return score_core(
        category=i.get("category", ""),
        source_tier=i.get("source_tier", 3),
        verified=i.get("verified", False),
        coverage_count=i.get("coverage_count", 1),
        criminal_stage=i.get("criminal_stage"),
        headline_days=i.get("headline_days", 1),
        position_weight=i.get("position_weight", 0.8),
        diversity=media_diversity(i.get("cross_verified_sources")),
    )


def _run(client, table: str, cols: str, fn, apply: bool) -> tuple[int, float, float]:
    rows = client.table(table).select(cols).execute().data
    changed = 0
    before = after = 0.0
    for r in rows:
        old = float(r.get("weighted_score") or 0)
        new = fn(r)
        before += old
        after += new
        if abs(old - new) > 0.005:
            changed += 1
            if apply:
                client.table(table).update({"weighted_score": new}).eq("id", r["id"]).execute()
    print(f"  {table:16s} {len(rows):4d}행 · 달라짐 {changed:4d}행 "
          f"· 합계 {before:9.1f} → {after:9.1f} ({(after/before*100 - 100) if before else 0:+.1f}%)")
    return changed, before, after


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    client = get_client()
    print("현재 공식: min(base × diversity × position_weight × 100, 100)")
    print("  (decay 는 웹이 렌더 시점에 곱한다 — 저장하지 않는다)\n")

    _run(client, "issue_clusters", _EVENT_COLS, _event_score, args.apply)
    _run(client, "issues", _ISSUE_COLS, _issue_score, args.apply)

    print()
    if args.apply:
        print("갱신했다. 다음 실행의 스냅샷부터 새 공식이 반영된다.")
        print("과거 score_snapshots 는 발행 시점 기록이므로 건드리지 않았다.")
    else:
        print("--apply 를 붙이면 실제로 갱신한다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
