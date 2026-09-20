"""이미 만들어진 중복 사건(Event)을 합친다.

Stage 3 버그(2026-09-19 수정)로 같은 사건이 여러 클러스터로 쪼개져 저장됐다.
그 수정은 **앞으로** 생길 중복만 막는다. 이 스크립트는 이미 쌓인 것을 정리한다.

판정 기준 — 셋을 모두 만족해야 병합한다:
  1. actor_name 과 category 가 같다
  2. first_reported_at 이 EVENT_ACTIVE_DAYS(7일) 이내
  3. 임베딩 코사인 유사도 >= EVENT_MATCH_THRESHOLD(0.85)

3번이 핵심이다. 1·2만 보면 "같은 인물의 같은 범주, 같은 주" 라는 이유로 별개 사건까지
합쳐진다 — 예: 같은 주에 나온 서로 다른 정책 발표 두 건.

되돌리기: --apply 시 rollback_merge_<타임스탬프>.json 에 원래 매핑을 남긴다.
삭제는 하지 않는다. 흡수된 쪽은 is_active=false 로만 내린다.
"""
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta

from config import EVENT_ACTIVE_DAYS, EVENT_MATCH_THRESHOLD
from db import get_client
from event_matcher import cosine_similarity


def _date(v):
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def find_duplicate_pairs(clusters: list[dict]) -> list[tuple[dict, dict, float]]:
    """(유지할 것, 흡수될 것, 유사도) 목록. 보도량이 많은 쪽을 유지한다."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for c in clusters:
        if c.get("actor_name") and c.get("category") and c.get("embedding") is not None:
            groups[(c["actor_name"], c["category"])].append(c)

    pairs = []
    for items in groups.values():
        items = [i for i in items if _date(i.get("first_reported_at"))]
        items.sort(key=lambda i: _date(i["first_reported_at"]))
        for i, a in enumerate(items):
            for b in items[i + 1 :]:
                gap = abs((_date(b["first_reported_at"]) - _date(a["first_reported_at"])).days)
                if gap > EVENT_ACTIVE_DAYS:
                    break
                sim = cosine_similarity(a["embedding"], b["embedding"])
                if sim != sim or sim < EVENT_MATCH_THRESHOLD:
                    continue
                keep, absorb = (a, b) if (a.get("coverage_count") or 1) >= (b.get("coverage_count") or 1) else (b, a)
                pairs.append((keep, absorb, sim))
    return pairs


def run(apply: bool) -> int:
    client = get_client()
    clusters = (
        client.table("issue_clusters")
        .select("id, actor_name, category, camp, summary, embedding, coverage_count, "
                "issue_count, first_reported_at, last_reported_at, is_active")
        .execute()
        .data
    )
    # is_active 로 거르지 않는다 — 앱(getEvents)은 비활성 클러스터도 그대로 보여준다.
    # 오래됐다고 중복이 사라지는 것도 아니다.
    #
    # 대신 **이미 흡수된 클러스터**를 뺀다. 흡수되면 기사 링크가 전부 옮겨가 0개가 된다.
    # 이 필터가 없으면 재실행 때 같은 쌍을 또 합치려 든다(빈 껍데기가 계속 매칭된다).
    linked = {
        row["cluster_id"]
        for row in client.table("cluster_issues").select("cluster_id").execute().data
    }
    before = len(clusters)
    clusters = [c for c in clusters if c["id"] in linked]
    print(
        f"클러스터 {before}건 중 검사 대상 {len(clusters)}건 "
        f"(이미 흡수된 빈 클러스터 {before - len(clusters)}건 제외)"
    )

    pairs = find_duplicate_pairs(clusters)
    if not pairs:
        print("병합할 중복이 없습니다.")
        return 0

    # 한 클러스터가 여러 쌍에 끼면 체인이 꼬인다. 흡수 대상은 한 번만 쓴다
    used: set[str] = set()
    plan = []
    for keep, absorb, sim in sorted(pairs, key=lambda p: -p[2]):
        if keep["id"] in used or absorb["id"] in used:
            continue
        used.add(keep["id"])
        used.add(absorb["id"])
        plan.append((keep, absorb, sim))

    print(f"병합 대상 {len(plan)}쌍\n")
    for keep, absorb, sim in plan:
        print(f"  유사도 {sim:.3f}  [{keep['actor_name']}/{keep['category']}]")
        print(f"    유지 ← {(keep.get('summary') or '')[:60]}")
        print(f"    흡수 → {(absorb.get('summary') or '')[:60]}")

    if not apply:
        print(f"\n실제로 합치려면: python merge_duplicate_events.py --apply")
        return 0

    rollback = []
    for keep, absorb, sim in plan:
        links = client.table("cluster_issues").select("issue_id").eq("cluster_id", absorb["id"]).execute().data
        issue_ids = [l["issue_id"] for l in links]
        rollback.append({"keep": keep["id"], "absorbed": absorb["id"], "issue_ids": issue_ids, "similarity": sim})

        for iid in issue_ids:
            client.table("cluster_issues").update({"cluster_id": keep["id"]}).eq("cluster_id", absorb["id"]).eq("issue_id", iid).execute()
            client.table("issues").update({"event_id": keep["id"]}).eq("id", iid).execute()

        first = min(filter(None, [_date(keep["first_reported_at"]), _date(absorb["first_reported_at"])]))
        last = max(filter(None, [_date(keep["last_reported_at"]), _date(absorb["last_reported_at"])]))
        client.table("issue_clusters").update({
            "issue_count": (keep.get("issue_count") or 1) + (absorb.get("issue_count") or 1),
            "coverage_count": (keep.get("coverage_count") or 1) + (absorb.get("coverage_count") or 1),
            "first_reported_at": first.isoformat(),
            "last_reported_at": last.isoformat(),
        }).eq("id", keep["id"]).execute()

        # 삭제하지 않는다 — 되돌릴 여지를 남긴다
        client.table("issue_clusters").update({"is_active": False}).eq("id", absorb["id"]).execute()
        print(f"  [합침] {absorb['id'][:8]} → {keep['id'][:8]} (기사 {len(issue_ids)}건)")

    path = f"rollback_merge_{datetime.now():%Y%m%d_%H%M%S}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rollback, f, ensure_ascii=False, indent=1)
    print(f"\n{len(plan)}쌍 병합 · 되돌리기 정보: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(run(apply="--apply" in sys.argv))
