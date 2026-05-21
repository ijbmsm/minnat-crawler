"""교차검증 — Tier 3 소스의 다중 매체 확인"""
from dedup import text_similarity


def cross_verify(
    issue: dict,
    all_articles: list[dict],
    similarity_threshold: float = 0.55,
) -> dict:
    """같은 이슈를 다른 매체가 보도했는지 확인한다.

    규칙:
    - Tier 1/2 소스가 같은 사실 확인 → 즉시 verified
    - 서로 다른 Tier 3 매체 2개 이상 → verified
    - 단일 매체만 → unverified
    """
    matching_sources: list[dict] = []
    issue_title = issue.get("title", "")
    issue_summary = issue.get("summary", "")
    issue_source = issue.get("source_name", "")

    for article in all_articles:
        # 자기 자신 제외
        if article.get("source_url") == issue.get("source_url"):
            continue
        # 같은 매체 제외
        if article.get("source", article.get("source_name", "")) == issue_source:
            continue

        # 유사도 비교
        title_sim = text_similarity(issue_title, article.get("title", ""))
        summary_sim = text_similarity(
            issue_summary, article.get("summary", article.get("content", ""))
        )
        combined = title_sim * 0.5 + summary_sim * 0.5

        if combined >= similarity_threshold:
            source_name = article.get("source", article.get("source_name", "unknown"))
            matching_sources.append({
                "name": source_name,
                "url": article.get("source_url", article.get("link", "")),
                "tier": article.get("source_tier", 3),
                "similarity": round(combined, 3),
            })

    # 판정
    has_high_tier = any(s["tier"] <= 2 for s in matching_sources)
    unique_sources = len(set(s["name"] for s in matching_sources))
    verified = has_high_tier or unique_sources >= 2

    if verified:
        note = f"교차검증 완료: {unique_sources + 1}개 매체 확인"
    else:
        note = f"교차검증 미충족: {issue_source} 단독 보도"

    return {
        "verified": verified,
        "cross_verified_sources": matching_sources[:5],  # 최대 5개
        "verification_note": note,
    }
