"""중복 이슈 감지 — 제목+요약 유사도 기반"""
from difflib import SequenceMatcher
from datetime import datetime, timedelta


def text_similarity(a: str, b: str) -> float:
    """두 텍스트의 유사도를 0~1로 반환"""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def find_duplicates(
    new_issue: dict,
    existing_issues: list[dict],
    threshold: float = 0.65,
    days_window: int = 7,
) -> list[dict]:
    """새 이슈와 기존 이슈들 사이에서 중복을 찾는다.

    Returns:
        중복 후보 리스트 (유사도 내림차순)
    """
    candidates: list[dict] = []
    new_title = new_issue.get("title", "")
    new_summary = new_issue.get("summary", "")

    try:
        new_date = datetime.fromisoformat(
            new_issue.get("published_at", "").replace("Z", "+00:00")
        )
    except (ValueError, TypeError):
        new_date = datetime.now()

    for existing in existing_issues:
        # 1단계: 날짜 필터 (N일 이내만)
        try:
            ex_date = datetime.fromisoformat(
                existing.get("published_at", "").replace("Z", "+00:00")
            )
            if abs((new_date - ex_date).days) > days_window:
                continue
        except (ValueError, TypeError):
            continue

        # 2단계: 제목 + 요약 유사도
        title_sim = text_similarity(new_title, existing.get("title", ""))
        summary_sim = text_similarity(new_summary, existing.get("summary", ""))
        combined = title_sim * 0.5 + summary_sim * 0.5

        if combined >= threshold:
            camp_conflict = new_issue.get("camp") != existing.get("camp")
            candidates.append({
                "existing_id": existing["id"],
                "existing_title": existing["title"],
                "similarity": round(combined, 3),
                "existing_camp": existing["camp"],
                "new_camp": new_issue.get("camp"),
                "camp_conflict": camp_conflict,
            })

    return sorted(candidates, key=lambda x: x["similarity"], reverse=True)


def is_duplicate(
    new_issue: dict,
    existing_issues: list[dict],
    threshold: float = 0.65,
) -> tuple[bool, dict | None]:
    """중복 여부와 가장 유사한 기존 이슈를 반환"""
    dupes = find_duplicates(new_issue, existing_issues, threshold)
    if dupes:
        return True, dupes[0]
    return False, None
