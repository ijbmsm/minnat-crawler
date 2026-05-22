"""교차검증 자동 승격 v2.0 — Event 기반

Event에 issue가 추가될 때마다 event_manager에서 trust_level을 재평가하므로,
이 모듈은 event에 속하지 않은 orphan issue 처리 + event 레벨 재검증을 담당.

실행 타이밍: 매 크롤러 실행 마지막에 호출.
"""
from datetime import datetime, timedelta

from config import MEDIA_LEAN
from db import get_client
from dedup import text_similarity


def _get_lean(source: str) -> str:
    for media, lean in MEDIA_LEAN.items():
        if media in source:
            return lean
    return "unknown"


def run_auto_verify() -> int:
    """미검증 Event를 재평가하고, orphan issue들도 검증한다."""
    client = get_client()
    since = (datetime.now() - timedelta(days=7)).isoformat()
    verified_count = 0

    # 1. 미검증 Event 재평가
    unverified_events = (
        client.table("issue_clusters")
        .select("id, coverage_count, cross_verified_sources, source_tier, verified")
        .eq("verified", False)
        .eq("is_active", True)
        .execute()
    )

    for event in unverified_events.data:
        sources = event.get("cross_verified_sources", [])
        leans = {s.get("lean", "unknown") for s in sources}
        leans.discard("unknown")
        unique_count = len({s.get("name") for s in sources})
        source_tier = event.get("source_tier", 3)

        should_verify = False
        trust_level = "pending"

        if source_tier <= 1:
            should_verify = True
            trust_level = "high"
        elif len(leans) >= 3:
            should_verify = True
            trust_level = "high"
        elif len(leans) >= 2 and unique_count >= 2:
            should_verify = True
            trust_level = "medium"
        elif unique_count >= 2:
            should_verify = True
            trust_level = "low"

        if should_verify:
            try:
                client.table("issue_clusters").update({
                    "verified": True,
                    "trust_level": trust_level,
                }).eq("id", event["id"]).execute()

                # 소속 issue들도 verified 업데이트
                members = (
                    client.table("cluster_issues")
                    .select("issue_id")
                    .eq("cluster_id", event["id"])
                    .execute()
                )
                for m in members.data:
                    client.table("issues").update({
                        "verified": True,
                    }).eq("id", m["issue_id"]).execute()

                verified_count += 1
            except Exception as e:
                print(f"  [error] event 승격 실패: {e}")

    # 2. Orphan issue 검증 (event_id가 없는 미검증 issue)
    orphan_result = (
        client.table("issues")
        .select("id, title, summary, camp, category, actor_name, source_name, published_at")
        .eq("verified", False)
        .eq("source_tier", 3)
        .is_("event_id", "null")
        .gte("published_at", since)
        .execute()
    )
    orphans = orphan_result.data

    if orphans:
        all_result = (
            client.table("issues")
            .select("id, title, summary, source_name, published_at, actor_name, category")
            .gte("published_at", since)
            .execute()
        )
        all_issues = all_result.data

        for issue in orphans:
            matched_sources: list[str] = []
            issue_lean = _get_lean(issue["source_name"])

            for other in all_issues:
                if other["id"] == issue["id"]:
                    continue
                if other["source_name"] == issue["source_name"]:
                    continue

                # actor + category 매칭
                if (
                    issue.get("actor_name")
                    and issue["actor_name"] == other.get("actor_name")
                    and issue["category"] == other["category"]
                ):
                    matched_sources.append(other["source_name"])
                    continue

                # 텍스트 유사도 매칭
                title_sim = text_similarity(issue["title"], other["title"])
                summary_sim = text_similarity(issue["summary"], other["summary"])
                combined = title_sim * 0.5 + summary_sim * 0.5
                if combined >= 0.6:
                    matched_sources.append(other["source_name"])

            if not matched_sources:
                continue

            other_leans = {_get_lean(s) for s in matched_sources}
            all_leans = other_leans | {issue_lean}
            has_diversity = len(all_leans - {"unknown"}) >= 2
            unique_other = len(set(matched_sources))
            should_verify = unique_other >= 2 or (unique_other >= 1 and has_diversity)

            if should_verify:
                source_list = list(set(matched_sources))[:5]
                try:
                    client.table("issues").update({
                        "verified": True,
                        "cross_verified_sources": [{"name": s, "lean": _get_lean(s)} for s in source_list],
                        "verification_note": f"교차검증 완료: {', '.join(source_list)}",
                    }).eq("id", issue["id"]).execute()
                    verified_count += 1
                except Exception as e:
                    print(f"  [error] orphan 승격 실패: {e}")

    print(f"[verify] 승격 완료: {verified_count}건")
    return verified_count


if __name__ == "__main__":
    run_auto_verify()
