"""Event 관리 — 생성, 머지, 점수 재계산, 비활성화

Event = issue_clusters 테이블의 1행 = 하나의 정치 사건
각 Event에는 여러 Issue(개별 보도)가 연결됨
"""
from datetime import datetime, timedelta

from config import MEDIA_LEAN, CRIMINAL_STAGE_WEIGHT, SCORED_CATEGORIES
from db import get_client
from event_matcher import get_embedding


def _get_lean(source: str) -> str:
    for media, lean in MEDIA_LEAN.items():
        if media in source:
            return lean
    return "unknown"


def _calculate_media_diversity(sources: list[dict]) -> float:
    """매체 다양성 점수: 0.7 (단독) / 1.0 (2진영) / 1.3 (3진영)"""
    leans = {s.get("lean", "unknown") for s in sources}
    leans.discard("unknown")
    if len(leans) >= 3:
        return 1.3
    elif len(leans) >= 2:
        return 1.0
    return 0.7


def _best_criminal_stage(stages: list[str]) -> str | None:
    """멤버 issues 중 가장 높은 형사 단계 반환."""
    if not stages:
        return None
    best = None
    best_weight = -1
    for stage in stages:
        w = CRIMINAL_STAGE_WEIGHT.get(stage, 0)
        if w > best_weight:
            best_weight = w
            best = stage
    return best


def _evaluate_event_trust(
    sources: list[dict],
    source_tier: int,
) -> tuple[str, bool]:
    """Event 레벨 신뢰도 평가.

    Returns: (trust_level, verified)
    """
    leans = {s.get("lean", "unknown") for s in sources}
    leans.discard("unknown")
    unique_count = len({s.get("name") for s in sources})

    # Tier 1 → 즉시 high
    if source_tier <= 1:
        return "high", True

    has_3way = len(leans) >= 3
    has_2way = len(leans) >= 2

    if has_3way:
        return "high", True
    if has_2way and unique_count >= 2:
        return "medium", True
    if unique_count >= 2:
        return "low", True
    return "pending", False


def create_event(issue: dict, embedding: list[float]) -> dict | None:
    """새 Event를 생성하고 issue를 연결한다.

    Args:
        issue: insert_issue() 반환값 (id 포함)
        embedding: get_embedding() 결과

    Returns: 생성된 event dict 또는 None
    """
    client = get_client()
    issue_id = issue["id"]
    published = issue.get("published_at", datetime.now().isoformat())
    source_name = issue.get("source_name", "")
    source_lean = _get_lean(source_name)

    cross_sources = [{"name": source_name, "lean": source_lean}]

    event_data = {
        "representative_issue_id": issue_id,
        "issue_count": 1,
        "actor_name": issue.get("actor_name"),
        "category": issue.get("category"),
        "camp": issue.get("camp"),
        "coverage_count": 1,
        "headline_days": 1,
        "first_reported_at": published,
        "last_reported_at": published,
        "weighted_score": 0,  # 아래에서 재계산
        "cross_verified_sources": cross_sources,
        "verified": issue.get("verified", False),
        "trust_level": issue.get("trust_level", "pending"),
        "position_weight": issue.get("position_weight", 0.8),
        "criminal_stage": issue.get("criminal_stage"),
        "source_tier": issue.get("source_tier", 3),
        "media_diversity_score": 0.7,  # 단독
        "embedding": embedding,
        "is_active": True,
        "summary": issue.get("headline", issue.get("summary", issue.get("title", "")))[:300],
    }

    try:
        result = client.table("issue_clusters").insert(event_data).execute()
        if not result.data:
            return None
        event = result.data[0]
        event_id = event["id"]

        # cluster_issues 연결
        client.table("cluster_issues").insert({
            "cluster_id": event_id,
            "issue_id": issue_id,
        }).execute()

        # issues.event_id 업데이트
        client.table("issues").update({
            "event_id": event_id,
        }).eq("id", issue_id).execute()

        # 점수 재계산
        score = recalculate_event_score(event_data)
        if score > 0:
            client.table("issue_clusters").update({"weighted_score": score}).eq("id", event_id).execute()
            event["weighted_score"] = score

        print(f"  [event] 새 사건 생성: {issue.get('title', '')[:40]}")
        return event

    except Exception as e:
        print(f"  [event] 생성 실패: {e}")
        return None


def merge_into_event(event: dict, new_issue: dict) -> dict | None:
    """기존 Event에 새 issue를 머지한다.

    coverage_count, headline_days, trust_level, weighted_score 등 재계산.
    """
    client = get_client()
    event_id = event["id"]
    issue_id = new_issue["id"]

    try:
        # 1. cluster_issues 연결
        client.table("cluster_issues").insert({
            "cluster_id": event_id,
            "issue_id": issue_id,
        }).execute()

        # 2. issues.event_id 업데이트
        client.table("issues").update({
            "event_id": event_id,
        }).eq("id", issue_id).execute()

        # 3. event에 속한 전체 issues 조회
        member_result = (
            client.table("cluster_issues")
            .select("issue_id")
            .eq("cluster_id", event_id)
            .execute()
        )
        member_ids = [r["issue_id"] for r in member_result.data]

        issues_result = (
            client.table("issues")
            .select("id, source_name, source_tier, published_at, criminal_stage, "
                    "ai_analysis, verified, trust_level, position_weight, weighted_score")
            .in_("id", member_ids)
            .execute()
        )
        members = issues_result.data

        # 4. 집계 계산
        # 고유 매체 수
        source_names = list({m["source_name"] for m in members})
        coverage_count = len(source_names)

        # cross_verified_sources
        cross_sources = []
        seen_sources = set()
        for m in members:
            name = m["source_name"]
            if name not in seen_sources:
                seen_sources.add(name)
                cross_sources.append({"name": name, "lean": _get_lean(name)})
        cross_sources = cross_sources[:10]

        # media_diversity_score
        diversity = _calculate_media_diversity(cross_sources)

        # headline_days
        dates = []
        for m in members:
            try:
                d = datetime.fromisoformat(m["published_at"].replace("Z", "+00:00"))
                dates.append(d)
            except (ValueError, TypeError):
                pass
        if dates:
            first = min(dates)
            last = max(dates)
            headline_days = max(1, (last - first).days + 1)
            first_reported = first.isoformat()
            last_reported = last.isoformat()
        else:
            headline_days = 1
            first_reported = event.get("first_reported_at")
            last_reported = event.get("last_reported_at")

        # best source_tier (최저 = 최고 신뢰)
        best_tier = min(m.get("source_tier", 3) for m in members)

        # criminal_stage (최고 가중치)
        stages = [m["criminal_stage"] for m in members if m.get("criminal_stage")]
        best_stage = _best_criminal_stage(stages)

        # trust_level + verified
        trust_level, verified = _evaluate_event_trust(cross_sources, best_tier)

        # position_weight (최대값)
        pos_weight = max(m.get("position_weight", 0.8) for m in members)

        # representative_issue_id (최고 tier → 최고 confidence)
        def _issue_priority(m: dict) -> tuple:
            tier = m.get("source_tier", 3)
            conf = 0.0
            ai = m.get("ai_analysis")
            if isinstance(ai, dict):
                conf = ai.get("confidence", 0.0)
            return (tier, -conf)  # 낮은 tier 우선, 높은 confidence 우선

        members_sorted = sorted(members, key=_issue_priority)
        representative_id = members_sorted[0]["id"] if members_sorted else event.get("representative_issue_id")

        # weighted_score 재계산
        score = recalculate_event_score({
            "category": event.get("category"),
            "source_tier": best_tier,
            "verified": verified,
            "coverage_count": coverage_count,
            "criminal_stage": best_stage,
            "headline_days": headline_days,
            "position_weight": pos_weight,
        })

        # embedding 재계산 (대표 issue 기준)
        rep_issue = next((m for m in members if m["id"] == representative_id), None)
        new_embedding = None
        if rep_issue and representative_id != event.get("representative_issue_id"):
            # 대표가 바뀌었으면 embedding 재계산
            rep_full = client.table("issues").select("title, summary").eq("id", representative_id).single().execute()
            if rep_full.data:
                text = f"{rep_full.data['title']} {rep_full.data.get('summary', '')}"
                new_embedding = get_embedding(text)

        # 5. Event 업데이트
        updates = {
            "representative_issue_id": representative_id,
            "issue_count": len(members),
            "coverage_count": coverage_count,
            "headline_days": headline_days,
            "first_reported_at": first_reported,
            "last_reported_at": last_reported,
            "weighted_score": score,
            "cross_verified_sources": cross_sources,
            "verified": verified,
            "trust_level": trust_level,
            "position_weight": pos_weight,
            "criminal_stage": best_stage,
            "source_tier": best_tier,
            "media_diversity_score": diversity,
            "summary": event.get("summary"),  # 유지
        }
        if new_embedding:
            updates["embedding"] = new_embedding

        client.table("issue_clusters").update(updates).eq("id", event_id).execute()

        print(
            f"  [event] 머지: +1 → {len(members)}건, "
            f"커버리지 {coverage_count}개 매체, "
            f"신뢰 {trust_level}"
        )
        return {**event, **updates}

    except Exception as e:
        print(f"  [event] 머지 실패: {e}")
        return None


def recalculate_event_score(event: dict) -> float:
    """Event 레벨 점수를 v1.1 공식으로 계산한다.

    base = coverage_norm × 0.40 + stage_norm × 0.35 + headline_norm × 0.25
    score = base × position_weight × 100
    """
    category = event.get("category", "")
    if category not in SCORED_CATEGORIES:
        return 0.0

    source_tier = event.get("source_tier", 3)
    if source_tier == 4:
        return 0.0
    if source_tier == 3 and not event.get("verified", False):
        return 0.0

    # 보도량 정규화
    coverage = event.get("coverage_count", 1)
    coverage_norm = min(coverage / 15, 1.0)

    # 공식 처리 단계
    official_stage = 5.0
    if category == "criminal_conviction":
        stage = event.get("criminal_stage", "")
        official_stage = float(CRIMINAL_STAGE_WEIGHT.get(stage, 0))
        if official_stage == 0:
            return 0.0
    stage_norm = min(official_stage / 10, 1.0)

    # 헤드라인 지속
    headline = event.get("headline_days", 1)
    headline_norm = min(headline / 20, 1.0)

    base = coverage_norm * 0.40 + stage_norm * 0.35 + headline_norm * 0.25

    # 직책 가중치
    pos_weight = event.get("position_weight", 0.8)

    return round(base * pos_weight * 100, 2)


def deactivate_old_events(days: int = 7) -> int:
    """마지막 보도 후 N일 지난 events를 비활성화한다."""
    client = get_client()
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()

    try:
        result = (
            client.table("issue_clusters")
            .update({"is_active": False})
            .eq("is_active", True)
            .lt("last_reported_at", cutoff)
            .execute()
        )
        count = len(result.data) if result.data else 0
        if count > 0:
            print(f"  [event] {count}개 사건 비활성화 (마지막 보도 {days}일 이상 경과)")
        return count
    except Exception as e:
        print(f"  [event] 비활성화 실패: {e}")
        return 0
