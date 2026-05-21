"""교차검증 v2 — 통신사 재인용 감지 + 좌우 매체 다양성"""
from dedup import text_similarity
from config import MEDIA_LEAN

# 통신사 재인용 패턴
WIRE_PATTERNS = [
    "연합뉴스 제공", "공동취재단", "/연합뉴스", "(연합뉴스)",
    "뉴스1 제공", "/뉴스1",
]


def _is_wire_republish(article: dict) -> bool:
    """통신사 기사 재인용인지 확인"""
    text = f"{article.get('title', '')} {article.get('summary', '')}"
    return any(p in text for p in WIRE_PATTERNS)


def _get_lean(source_name: str) -> str:
    """매체 성향 반환"""
    for media, lean in MEDIA_LEAN.items():
        if media in source_name:
            return lean
    return "unknown"


def cross_verify(
    issue: dict,
    all_articles: list[dict],
    similarity_threshold: float = 0.55,
) -> dict:
    """같은 이슈를 다른 매체가 보도했는지 확인한다.

    규칙 v2:
    - Tier 1/2 소스 확인 → 즉시 verified
    - 좌+우 (또는 좌+중 또는 우+중) 매체가 모두 보도 → verified
    - 같은 성향만 → unverified
    - 통신사 재인용은 1건으로 카운트
    """
    matching_sources: list[dict] = []
    issue_title = issue.get("title", "")
    issue_summary = issue.get("summary", "")
    issue_source = issue.get("source_name", issue.get("source", ""))
    seen_wire_content: set[str] = set()

    for article in all_articles:
        # 자기 자신 제외
        if article.get("source_url") == issue.get("source_url"):
            continue

        art_source = article.get("source", article.get("source_name", ""))

        # 같은 매체 제외
        if art_source == issue_source:
            continue

        # 유사도 비교
        title_sim = text_similarity(issue_title, article.get("title", ""))
        summary_sim = text_similarity(
            issue_summary, article.get("summary", article.get("content", ""))
        )
        combined = title_sim * 0.5 + summary_sim * 0.5

        if combined < similarity_threshold:
            continue

        # 통신사 재인용 감지
        if _is_wire_republish(article):
            wire_key = article.get("title", "")[:30]
            if wire_key in seen_wire_content:
                continue  # 이미 카운트된 통신사 기사
            seen_wire_content.add(wire_key)

        matching_sources.append({
            "name": art_source,
            "url": article.get("source_url", article.get("link", "")),
            "tier": article.get("source_tier", 3),
            "lean": _get_lean(art_source),
            "similarity": round(combined, 3),
        })

    # ── 판정 ──
    has_high_tier = any(s["tier"] <= 2 for s in matching_sources)
    unique_sources = len(set(s["name"] for s in matching_sources))

    # 좌우 다양성 체크
    leans = {s["lean"] for s in matching_sources}
    leans.add(_get_lean(issue_source))  # 원본 매체 포함

    has_progressive = "progressive" in leans
    has_conservative = "conservative" in leans
    has_center = "center" in leans

    diverse = (
        (has_progressive and has_conservative) or
        (has_progressive and has_center) or
        (has_conservative and has_center)
    )

    verified = has_high_tier or (unique_sources >= 2 and diverse)

    if verified:
        lean_list = ", ".join(sorted(leans - {"unknown"}))
        note = f"교차검증 완료: {unique_sources + 1}개 매체 ({lean_list})"
    elif unique_sources >= 2:
        note = f"교차검증 부분: {unique_sources + 1}개 매체이나 같은 성향"
    else:
        note = f"교차검증 미충족: {issue_source} 단독 보도"

    return {
        "verified": verified,
        "cross_verified_sources": matching_sources[:5],
        "verification_note": note,
    }
