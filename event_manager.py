"""Event 관리 — 생성, 머지, 점수 재계산, 비활성화

Event = issue_clusters 테이블의 1행 = 하나의 정치 사건
각 Event에는 여러 Issue(개별 보도)가 연결됨
"""
from datetime import datetime, timedelta

from config import MEDIA_LEAN, CRIMINAL_STAGE_WEIGHT, SCORED_CATEGORIES
from db import get_client
from event_matcher import get_embedding
from scorer import score_core, media_diversity


def _get_lean(source: str) -> str:
    for media, lean in MEDIA_LEAN.items():
        if media in source:
            return lean
    return "unknown"


# 다양도는 점수식의 일부다. scorer 에 한 벌만 둔다 — 여기에 사본을 두면
# 하네스가 대조하는 계단값이 두 곳이 되어 또 갈린다.
_calculate_media_diversity = media_diversity


# 종국 결정이 앞선다 — 발의보다 가결, 가결보다 헌재 결정이 사안의 현재 상태다
_INSTITUTIONAL_RANK = {
    "inquiry_launched": 1, "impeachment_proposed": 2, "censure_passed": 3,
    "impeachment_passed": 4, "impeachment_rejected": 5, "impeachment_upheld": 5,
}


def _best_institutional_stage(stages: list[str]) -> str | None:
    ranked = [s for s in stages if s in _INSTITUTIONAL_RANK]
    return max(ranked, key=lambda s: _INSTITUTIONAL_RANK[s]) if ranked else None


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


# 사건의 카테고리도 "가장 공식적인 것"이 이긴다 — criminal_stage 를 멤버 최댓값으로
# 끌어올리는 것과 같은 원칙이다. 이게 없으면 archive 로 시작한 사건에 나중에 공식 처분
# 기사가 붙어도 카테고리가 얼어붙어 점수가 영영 0 이 된다. 2026-09-19 김승원 자진사퇴가
# 그랬다 — self_admission 기사가 policy_record 사건에 흡수돼 20건짜리 사건이 0점이었다.
#
# 분류기가 확신하지 못한 건(confidence < 0.6)은 승격 근거로 쓰지 않는다. 프롬프트가
# "애매하면 confidence 를 0.6 이하로 낮추고 media_coverage 로 분류하라"고 지시하므로,
# 그 아래 값은 분류기 스스로 뒤집을 수 있다고 말한 것이다.
CATEGORY_PROMOTE_MIN_CONFIDENCE = 0.6


def _member_confidence(member: dict) -> float:
    ai = member.get("ai_analysis")
    return ai.get("confidence", 0.0) if isinstance(ai, dict) else 0.0


def _best_category(members: list[dict], fallback: str) -> str:
    """멤버 중 공식 처분 카테고리가 있으면 사건을 그쪽으로 승격한다."""
    scored = [
        m["category"]
        for m in members
        if m.get("category") in SCORED_CATEGORIES
        and _member_confidence(m) >= CATEGORY_PROMOTE_MIN_CONFIDENCE
    ]
    if not scored:
        return fallback
    # 한 사건에 여러 처분이 섞이면 더 무거운 쪽을 남긴다.
    # SCORED_CATEGORIES 선언 순서가 그 서열이다 (형사 → 민사 → 윤리 → …).
    return min(scored, key=SCORED_CATEGORIES.index)


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


def create_event(issue: dict, embedding: list[float] | None) -> dict | None:
    """새 Event를 생성하고 issue를 연결한다.

    Args:
        issue: insert_issue() 반환값 (id 포함)
        embedding: get_embedding() 결과. None 이면 NULL 로 저장한다 —
            영벡터를 넣으면 pgvector 코사인 거리가 NaN 이 되어 유사 사례가 깨진다.

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
        "institutional_stage": issue.get("institutional_stage"),
        "source_tier": issue.get("source_tier", 3),
        "media_diversity_score": 0.7,  # 단독
        "embedding": embedding,
        "next_branch": issue.get("next_branch"),
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
            .select("id, source_name, source_tier, published_at, criminal_stage, institutional_stage, "
                    "category, ai_analysis, verified, trust_level, position_weight, weighted_score")
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
        # 제도적 결정은 종국(파면·기각)이 있으면 그것을 남긴다
        inst = [m.get("institutional_stage") for m in members if m.get("institutional_stage")]
        best_inst = _best_institutional_stage(inst)

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

        # 카테고리 승격 — 공식 처분 기사가 붙었으면 사건이 archive 로 남지 않는다
        best_category = _best_category(members, event.get("category", ""))

        # weighted_score 재계산
        score = recalculate_event_score({
            "category": best_category,
            "source_tier": best_tier,
            "verified": verified,
            "coverage_count": coverage_count,
            "criminal_stage": best_stage,
            "institutional_stage": best_inst,
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
            "category": best_category,
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
            "institutional_stage": best_inst,
            "source_tier": best_tier,
            "media_diversity_score": diversity,
            "summary": event.get("summary"),  # 유지
        }
        if new_embedding:
            updates["embedding"] = new_embedding

        # 새 기사가 확정 일정을 물고 왔으면 갱신한다.
        # 기존 값이 이미 지난 날짜면 새 것으로 바꾸고, 아니면 더 이른 쪽을 남긴다.
        incoming = new_issue.get("next_branch")
        if incoming and incoming.get("date"):
            current = event.get("next_branch") or {}
            cur_date = current.get("date") if isinstance(current, dict) else None
            today = datetime.now().strftime("%Y-%m-%d")
            if not cur_date or cur_date < today or incoming["date"] < cur_date:
                updates["next_branch"] = incoming

        client.table("issue_clusters").update(updates).eq("id", event_id).execute()

        promoted = ""
        if best_category != event.get("category"):
            promoted = f", 카테고리 {event.get('category')} → {best_category}"
        print(
            f"  [event] 머지: +1 → {len(members)}건, "
            f"커버리지 {coverage_count}개 매체, "
            f"신뢰 {trust_level}{promoted}"
        )
        return {**event, **updates}

    except Exception as e:
        print(f"  [event] 머지 실패: {e}")
        return None


def recalculate_event_score(event: dict) -> float:
    """Event 레벨 점수.

    공식은 scorer.score_core 하나다. 예전에는 여기에 사본이 있었고
    diversity 와 100 상한이 빠져 있었다 — 같은 사건이 DB 와 화면에서
    다른 점수를 가졌다 (2026-09-25 하네스 M-01 이 잡았다).
    """
    return score_core(
        category=event.get("category", ""),
        source_tier=event.get("source_tier", 3),
        verified=event.get("verified", False),
        coverage_count=event.get("coverage_count", 1),
        criminal_stage=event.get("criminal_stage"),
        headline_days=event.get("headline_days", 1),
        position_weight=event.get("position_weight", 0.8),
        # 사건에는 계산해 둔 값이 있다. 없으면 출처 목록에서 다시 구한다.
        diversity=event.get("media_diversity_score")
        or media_diversity(event.get("cross_verified_sources")),
    )


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
