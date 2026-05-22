"""기존 issue → Event 소급 마이그레이션

기존 event_id=null인 issue들을 시간순으로 처리하여
Event(issue_clusters)에 할당한다.

실행: python migrate_to_events.py
1회성 스크립트. GitHub Actions workflow_dispatch로 실행 권장.
"""
from datetime import datetime

from db import get_client
from event_matcher import get_embedding, match_to_event
from event_manager import create_event, merge_into_event
from config import EVENT_ACTIVE_DAYS


def load_orphan_issues() -> list[dict]:
    """event_id가 없는 issue를 시간순으로 로드."""
    client = get_client()
    all_issues = []
    offset = 0
    batch_size = 100

    while True:
        result = (
            client.table("issues")
            .select("*")
            .is_("event_id", "null")
            .order("published_at", desc=False)
            .range(offset, offset + batch_size - 1)
            .execute()
        )
        if not result.data:
            break
        all_issues.extend(result.data)
        if len(result.data) < batch_size:
            break
        offset += batch_size

    return all_issues


def load_all_events() -> list[dict]:
    """모든 events를 로드 (active/inactive 무관)."""
    client = get_client()
    result = client.table("issue_clusters").select("*").execute()
    return result.data


def run_migration() -> None:
    print(f"\n{'='*60}")
    print(f"Event 소급 마이그레이션: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    orphans = load_orphan_issues()
    print(f"\nOrphan issues: {len(orphans)}건")

    if not orphans:
        print("마이그레이션할 issue 없음.")
        return

    # 기존 events 로드 (이미 만들어진 것)
    events = load_all_events()
    print(f"기존 events: {len(events)}건")

    stats = {"created": 0, "merged": 0, "failed": 0}

    for i, issue in enumerate(orphans):
        title = issue.get("title", "")[:50]
        progress = f"[{i+1}/{len(orphans)}]"

        # embedding 생성
        text = f"{issue.get('title', '')} {issue.get('summary', '')}"
        embedding = get_embedding(text)

        # 매칭 시도 (모든 events 대상, active 여부 무관)
        preliminary = {
            "title": issue.get("title", ""),
            "summary": issue.get("summary", ""),
            "camp": issue.get("camp"),
            "category": issue.get("category"),
            "actor_name": issue.get("actor_name", ""),
            "published_at": issue.get("published_at", ""),
        }

        matched = match_to_event(preliminary, events, embedding)

        if matched:
            result = merge_into_event(matched, issue)
            if result:
                stats["merged"] += 1
                print(f"  {progress} MERGED: {title}")
            else:
                stats["failed"] += 1
                print(f"  {progress} MERGE_FAIL: {title}")
        else:
            event = create_event(issue, embedding)
            if event:
                events.append(event)  # 다음 issue 매칭에 사용
                stats["created"] += 1
                print(f"  {progress} NEW: {title}")
            else:
                stats["failed"] += 1
                print(f"  {progress} CREATE_FAIL: {title}")

    print(f"\n{'='*60}")
    print(f"결과: 새 사건 {stats['created']} | 머지 {stats['merged']} | 실패 {stats['failed']}")
    print(f"총 events: {len(events)}건")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    run_migration()
