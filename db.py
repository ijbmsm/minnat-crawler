"""Supabase DB 연동"""
from supabase import create_client, Client
from config import SUPABASE_URL, SUPABASE_SERVICE_KEY

_client: Client | None = None


def get_client() -> Client:
    global _client
    if _client is None:
        _client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    return _client


def insert_issue(issue: dict) -> dict | None:
    """이슈를 DB에 삽입한다. 중복(같은 title)이면 스킵."""
    # camp 사전 검증
    if issue.get("camp") not in ("blue", "red"):
        print(f"  [db] camp 값 거부: {issue.get('camp')}")
        return None

    client = get_client()

    # 중복 체크 (제목 기준)
    existing = (
        client.table("issues")
        .select("id")
        .eq("title", issue["title"])
        .execute()
    )
    if existing.data:
        return None

    try:
        result = client.table("issues").insert(issue).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        print(f"  [db] 삽입 실패: {e}")
        return None


def get_parties() -> dict[str, str]:
    """정당명 → party_id 매핑 반환"""
    client = get_client()
    result = client.table("parties").select("id, name").execute()
    return {row["name"]: row["id"] for row in result.data}


def get_politicians() -> list[dict]:
    """전체 정치인 목록 반환"""
    client = get_client()
    result = (
        client.table("politicians")
        .select("id, name, party_id")
        .eq("active", True)
        .execute()
    )
    return result.data


def save_snapshot(snapshot: dict) -> None:
    """일별 점수 스냅샷 저장 (upsert)"""
    client = get_client()
    client.table("score_snapshots").upsert(snapshot, on_conflict="date").execute()


# ── Event (issue_clusters) 관련 ──

def get_active_events(days: int = 7) -> list[dict]:
    """최근 N일 이내 active events를 embedding 포함해서 조회한다."""
    from datetime import datetime, timedelta
    client = get_client()
    since = (datetime.now() - timedelta(days=days)).isoformat()
    result = (
        client.table("issue_clusters")
        .select("*")
        .eq("is_active", True)
        .gte("last_reported_at", since)
        .execute()
    )
    return result.data


def insert_event(event: dict) -> dict | None:
    """issue_clusters에 새 event를 삽입한다."""
    client = get_client()
    try:
        result = client.table("issue_clusters").insert(event).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        print(f"  [db] event 삽입 실패: {e}")
        return None


def update_event(event_id: str, updates: dict) -> None:
    """issue_clusters의 event를 업데이트한다."""
    client = get_client()
    try:
        client.table("issue_clusters").update(updates).eq("id", event_id).execute()
    except Exception as e:
        print(f"  [db] event 업데이트 실패: {e}")


def link_issue_to_event(event_id: str, issue_id: str) -> None:
    """cluster_issues에 연결하고 issues.event_id를 설정한다."""
    client = get_client()
    try:
        client.table("cluster_issues").insert({
            "cluster_id": event_id,
            "issue_id": issue_id,
        }).execute()
        client.table("issues").update({
            "event_id": event_id,
        }).eq("id", issue_id).execute()
    except Exception as e:
        print(f"  [db] event 연결 실패: {e}")
