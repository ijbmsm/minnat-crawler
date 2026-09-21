"""이미 저장된 사건의 카테고리·직책 가중치·점수를 다시 계산한다 (1회성).

고친 버그 두 개가 과거 데이터에는 반영되지 않는다:

1. `merge_into_event` 가 category 를 재계산하지 않아, archive 로 시작한 사건은
   나중에 공식 처분 기사가 붙어도 카테고리가 얼어붙었다. 2026-09-19 김승원
   자진사퇴 사건(기사 20건, self_admission 1건 포함)이 policy_record 로 남아 0점.
2. `sync_politicians` 가 정부 직책자를 의원 목록에 밀려 무시해, 현직 대통령이
   DB 에 '의원' 으로 남았다. 그래서 직책 가중치가 1.2 가 아니라 0.8 로 저장됐다.

이 스크립트는 사건 단위로 멤버 기사를 다시 읽어 category·position_weight·
weighted_score 를 고쳐 쓴다. 기사(issues) 행은 건드리지 않는다 — 기사 하나하나의
분류는 그때 그 기사를 본 판단이고, 사건 단위 집계만 우리가 다시 하는 일이다.

    python backfill_cluster_category.py             # 미리보기 (아무것도 안 쓴다)
    python backfill_cluster_category.py --apply     # category·position_weight 반영

정치인 직책을 먼저 고쳐야 가중치가 제대로 나온다:

    python sync_politicians.py && python backfill_cluster_category.py --apply

weighted_score 는 기본적으로 건드리지 않는다. 화면 점수는 프런트(lib/score.ts)가
이 필드들로 직접 계산하므로 category·position_weight 만 고치면 곧바로 반영된다.
DB 의 weighted_score 를 쓰는 곳은 일별 스냅샷 하나뿐인데, 시드로 넣은 역사 데이터는
손으로 매긴 값이라 공식으로 다시 계산하면 40 → 120 처럼 크게 튄다. 그 판단까지
이 스크립트가 대신하지 않는다. 스냅샷까지 맞추려면 --rescore 를 붙인다.
"""
import sys
from collections import defaultdict

from config import POSITION_WEIGHT, SCORED_CATEGORIES
from db import get_client, load_politician_positions
from event_manager import _best_category, recalculate_event_score

CLUSTER_COLUMNS = (
    "id, category, source_tier, verified, coverage_count, headline_days, "
    "criminal_stage, position_weight, weighted_score, actor_name"
)


def load_positions() -> dict[str, str]:
    positions = load_politician_positions()
    if "대통령" not in positions.values():
        # 낡은 직책 테이블로 가중치를 다시 매기면 현직 대통령이 0.8 로 *내려간다*.
        # 고치러 와서 망가뜨리는 경우라 그냥 멈춘다.
        sys.exit(
            "[중단] 정치인 DB 에 현직 대통령이 없습니다.\n"
            "       python sync_politicians.py 를 먼저 실행하세요."
        )
    return positions


def load_members(client) -> dict[str, list[dict]]:
    """사건 → 멤버 기사. 클러스터마다 조회하지 않고 두 번에 끝낸다."""
    links = client.table("cluster_issues").select("cluster_id, issue_id").execute().data
    if not links:
        return {}

    by_issue: dict[str, str] = {link["issue_id"]: link["cluster_id"] for link in links}

    issues: list[dict] = []
    ids = list(by_issue)
    for start in range(0, len(ids), 200):  # PostgREST URL 길이 한계를 피한다
        chunk = ids[start:start + 200]
        rows = (
            client.table("issues")
            .select("id, category, ai_analysis, actor_name")
            .in_("id", chunk)
            .execute()
        )
        issues.extend(rows.data)

    members: dict[str, list[dict]] = defaultdict(list)
    for issue in issues:
        members[by_issue[issue["id"]]].append(issue)
    return members


def best_position_weight(members: list[dict], positions: dict[str, str], fallback: float) -> float:
    """멤버 행위자의 현재 직책으로 가중치를 다시 매긴다.

    DB 에 없는 사람(장관·후보자 등)은 기존 값을 유지한다 — 낮춰 잡을 근거가 없다.
    """
    weights = [
        POSITION_WEIGHT.get(positions[m["actor_name"]], 0.8)
        for m in members
        if m.get("actor_name") in positions
    ]
    return max(weights) if weights else fallback


def run(apply: bool, rescore: bool) -> None:
    client = get_client()
    positions = load_positions()
    members_by_cluster = load_members(client)

    clusters = client.table("issue_clusters").select(CLUSTER_COLUMNS).execute().data
    print(f"사건 {len(clusters)}건 검사\n")

    changes: list[tuple[dict, dict]] = []

    for cluster in clusters:
        members = members_by_cluster.get(cluster["id"], [])
        if not members:
            continue

        new_category = _best_category(members, cluster.get("category", ""))
        new_weight = best_position_weight(members, positions, cluster.get("position_weight") or 0.8)

        new_score = recalculate_event_score({
            **cluster,
            "category": new_category,
            "position_weight": new_weight,
        })

        update: dict = {}
        if new_category != cluster.get("category"):
            update["category"] = new_category
        # 가중치는 올리기만 한다. 고치려는 버그가 "현직 대통령이 0.8 로 저장됨" 이라는
        # 과소 계상이기 때문이다. 반대로 내리는 건 다른 얘기다 — 직책 가중치는 사건
        # 당시의 자리를 뜻하는데 DB 는 지금 자리만 안다. 퇴임했다는 이유로 재임 중
        # 사건의 무게를 사후에 깎으면, 시드로 넣은 역사 데이터가 조용히 가벼워진다.
        if new_weight - (cluster.get("position_weight") or 0.8) > 1e-9:
            update["position_weight"] = new_weight
        if rescore and abs(new_score - float(cluster.get("weighted_score") or 0)) > 1e-9:
            update["weighted_score"] = new_score

        if update:
            changes.append((cluster, update))

    for cluster, update in changes:
        parts = []
        if "category" in update:
            parts.append(f"카테고리 {cluster['category']} → {update['category']}")
        if "position_weight" in update:
            parts.append(f"직책가중치 {cluster['position_weight']} → {update['position_weight']}")
        if "weighted_score" in update:
            parts.append(f"점수 {cluster['weighted_score']} → {update['weighted_score']}")
        print(f"  [{cluster.get('actor_name') or '?'}] " + ", ".join(parts))

    revived = sum(1 for _, u in changes if u.get("category") in SCORED_CATEGORIES)
    print(f"\n변경 대상 {len(changes)}건 (archive → 공식 처분 승격: {revived}건)")
    if not rescore:
        print("weighted_score 는 건드리지 않았습니다 (스냅샷까지 맞추려면 --rescore).")

    if not apply:
        print("미리보기입니다. 반영하려면 --apply 를 붙이세요.")
        return

    failed = 0
    for cluster, update in changes:
        try:
            client.table("issue_clusters").update(update).eq("id", cluster["id"]).execute()
        except Exception as e:
            failed += 1
            print(f"  [실패] {cluster['id']}: {e}")

    print(f"반영 완료: {len(changes) - failed}건 성공, {failed}건 실패")


if __name__ == "__main__":
    run(apply="--apply" in sys.argv, rescore="--rescore" in sys.argv)
