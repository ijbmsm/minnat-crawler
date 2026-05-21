"""신뢰도 게이트 v1.1 — cross_verify + auto_verify 통합"""
from config import MEDIA_LEAN, EVIDENCE_KEYWORDS
from dedup import text_similarity


def _get_lean(source: str) -> str:
    for media, lean in MEDIA_LEAN.items():
        if media in source:
            return lean
    return "unknown"


def _has_evidence_keywords(text: str) -> bool:
    """물증 키워드 존재 여부"""
    return any(kw in text for kw in EVIDENCE_KEYWORDS)


def _is_costly_signal(source_lean: str, actor_camp: str) -> bool:
    """자기 진영 매체가 자기 진영을 비판하는 costly signal인지"""
    if source_lean == "progressive" and actor_camp == "blue":
        return True
    if source_lean == "conservative" and actor_camp == "red":
        return True
    return False


def evaluate_trust(
    issue: dict,
    all_articles: list[dict],
    similarity_threshold: float = 0.55,
) -> dict:
    """이슈의 신뢰도를 평가한다.

    Returns:
        {
            "trust_level": "high" | "medium" | "low" | "pending",
            "verified": bool,
            "matched_sources": [...],
            "signals": [...],
            "note": str,
        }
    """
    signals: list[str] = []
    matched_sources: list[dict] = []

    title = issue.get("title", "")
    summary = issue.get("summary", "")
    source = issue.get("source_name", issue.get("source", ""))
    actor_camp = issue.get("camp", "")
    source_lean = _get_lean(source)
    text = f"{title} {summary}"

    # ── 공식 기관 처분 (Tier 1) → HIGH ──
    source_tier = issue.get("source_tier", 3)
    if source_tier <= 1:
        signals.append("TIER1_OFFICIAL")

    # ── 물증 키워드 ──
    if _has_evidence_keywords(text):
        signals.append("EVIDENCE_KEYWORD")

    # ── 본인 시인 감지 ──
    admission_kw = ["사과", "시인", "인정", "죄송", "반성", "사퇴"]
    if any(kw in text for kw in admission_kw):
        signals.append("SELF_ADMISSION_HINT")

    # ── 다른 매체에서 같은 이슈 찾기 ──
    for article in all_articles:
        art_source = article.get("source", article.get("source_name", ""))
        if art_source == source:
            continue

        t_sim = text_similarity(title, article.get("title", ""))
        s_sim = text_similarity(summary, article.get("summary", ""))
        combined = t_sim * 0.5 + s_sim * 0.5

        if combined < similarity_threshold:
            continue

        art_lean = _get_lean(art_source)
        matched_sources.append({
            "name": art_source,
            "lean": art_lean,
            "similarity": round(combined, 3),
        })

        # costly signal 체크
        if _is_costly_signal(art_lean, actor_camp):
            signals.append(f"COSTLY_SIGNAL:{art_source}")

    # ── 매체 다양성 ──
    all_leans = {_get_lean(s["name"]) for s in matched_sources}
    all_leans.add(source_lean)
    all_leans.discard("unknown")

    has_prog = "progressive" in all_leans
    has_cons = "conservative" in all_leans
    has_center = "center" in all_leans

    if has_prog and has_cons and has_center:
        signals.append("DIVERSITY_3WAY")  # 좌+중+우
    elif (has_prog and has_cons) or (has_prog and has_center) or (has_cons and has_center):
        signals.append("DIVERSITY_2WAY")

    unique_sources = len(set(s["name"] for s in matched_sources))

    # ── 판정 ──
    trust_level = "pending"
    verified = False

    high_signals = {"TIER1_OFFICIAL", "DIVERSITY_3WAY", "SELF_ADMISSION_HINT"}
    has_high = bool(high_signals & set(signals))
    has_costly = any(s.startswith("COSTLY_SIGNAL") for s in signals)
    has_evidence = "EVIDENCE_KEYWORD" in signals
    has_diversity2 = "DIVERSITY_2WAY" in signals

    if has_high or (has_costly and unique_sources >= 2):
        trust_level = "high"
        verified = True
    elif has_diversity2 and unique_sources >= 2:
        trust_level = "medium"
        verified = True
    elif has_evidence and unique_sources >= 1:
        trust_level = "medium"
        verified = True
    elif unique_sources >= 1:
        trust_level = "low"
        verified = False
    else:
        trust_level = "pending"
        verified = False

    note_parts = []
    if verified:
        note_parts.append(f"검증 완료: {unique_sources + 1}개 매체")
        if signals:
            note_parts.append(f"신호: {', '.join(signals[:3])}")
    else:
        note_parts.append(f"검증 대기: {source} 단독")

    return {
        "trust_level": trust_level,
        "verified": verified,
        "matched_sources": matched_sources[:5],
        "signals": signals,
        "note": " | ".join(note_parts),
    }
