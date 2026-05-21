"""2차 검증 로직 — AI 분류 결과를 DB INSERT 전에 검증"""
from dataclasses import dataclass, field

# 성과가 아닌 키워드 (제안/발언/계획 단계)
PROPOSAL_KEYWORDS = [
    "제안", "발의", "추진", "계획", "검토", "공약 발표", "약속", "밝혔다",
    "입장을 표명", "강조", "주장", "촉구", "비판", "논평", "발표 예정",
    "추진하겠다", "하겠다고", "뜻을 밝", "의지를 보",
]

# 실제 성과 키워드
RESULT_KEYWORDS = [
    "통과", "시행", "집행", "완료", "달성", "이행", "가결", "의결",
    "판결", "선고", "확정", "기소", "구속", "체포", "적발",
    "시행령", "공포", "발효",
]


@dataclass
class ValidationResult:
    passed: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    action: str = "insert"  # "insert" | "insert_unverified" | "queue_review" | "reject"


def validate_issue(
    ai_result: dict,
    article: dict,
    politicians_map: dict[str, str],  # {이름: camp}
) -> ValidationResult:
    """AI 분류 결과를 검증한다."""
    errors: list[str] = []
    warnings: list[str] = []

    # ── 1. camp 검증: 행위자의 실제 소속과 AI 판단 비교 ──
    actor = ai_result.get("actor_name", "")
    ai_camp = ai_result.get("camp", "")

    if actor and actor in politicians_map:
        expected_camp = politicians_map[actor]
        if ai_camp != expected_camp:
            errors.append(
                f"CAMP_MISMATCH: {actor}는 {expected_camp}인데 {ai_camp}로 분류됨"
            )
    elif actor and actor not in politicians_map:
        # DB에 없는 정치인 — 경고만
        warnings.append(f"UNKNOWN_POLITICIAN: {actor}가 DB에 없음")

    # ── 2. camp 값 자체 검증 ──
    if ai_camp not in ("blue", "red"):
        errors.append(f"INVALID_CAMP: {ai_camp}")

    # ── 3. policy_win / promise_kept 성과 검증 ──
    category = ai_result.get("category", "")
    if category in ("policy_win", "promise_kept"):
        title_summary = f"{article.get('title', '')} {article.get('summary', '')}"

        has_proposal = any(kw in title_summary for kw in PROPOSAL_KEYWORDS)
        has_result = any(kw in title_summary for kw in RESULT_KEYWORDS)

        if has_proposal and not has_result:
            errors.append(
                "PROPOSAL_NOT_RESULT: 성과 키워드 없이 제안/발언 키워드만 존재. "
                "policy_win이 아닌 controversial 또는 분류 불가"
            )

        if not ai_result.get("is_actionable_result", True):
            errors.append("NOT_ACTIONABLE: AI가 실제 성과가 아님을 인정함")

    # ── 4. confidence 검증 ──
    confidence = ai_result.get("confidence", 0)
    if confidence < 0.5:
        errors.append(f"VERY_LOW_CONFIDENCE: {confidence}")
    elif confidence < 0.7:
        warnings.append(f"LOW_CONFIDENCE: {confidence}")

    # ── 5. Tier 3 교차검증 필요 플래그 ──
    source_tier = article.get("source_tier", 3)
    if source_tier == 3:
        warnings.append("TIER3_NEEDS_CROSS_CHECK")

    # ── 6. severity 과장 체크 ──
    severity = ai_result.get("severity", "normal")
    if severity == "extreme" and confidence < 0.85:
        warnings.append("EXTREME_LOW_CONF: 극심 판정인데 confidence가 낮음")

    # ── 결정 ──
    if errors:
        return ValidationResult(
            passed=False,
            errors=errors,
            warnings=warnings,
            action="queue_review",
        )
    elif warnings:
        return ValidationResult(
            passed=True,
            errors=[],
            warnings=warnings,
            action="insert_unverified",
        )
    else:
        return ValidationResult(
            passed=True, errors=[], warnings=[], action="insert"
        )
