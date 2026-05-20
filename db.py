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
    """이슈를 DB에 삽입한다. 중복(같은 title+camp)이면 스킵."""
    client = get_client()

    # 중복 체크
    existing = (
        client.table("issues")
        .select("id")
        .eq("title", issue["title"])
        .eq("camp", issue["camp"])
        .execute()
    )
    if existing.data:
        return None

    result = client.table("issues").insert(issue).execute()
    return result.data[0] if result.data else None


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
