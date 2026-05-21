"""2차 검증 로직 v2 — AI 분류 결과를 DB INSERT 전에 검증"""
from dataclasses import dataclass, field
from config import POSITIVE_CATEGORIES

# 발언/제안 키워드 (성과 아님)
PROPOSAL_KEYWORDS = [
    "제안", "추진", "계획", "검토", "공약 발표", "약속", "밝혔다",
    "입장을 표명", "강조", "주장", "촉구", "비판", "논평",
    "추진하겠다", "하겠다고", "뜻을 밝", "의지를 보",
]

# 실제 성과 키워드
RESULT_KEYWORDS = [
    "통과", "시행", "집행", "완료", "달성", "이행", "가결", "의결",
    "판결", "선고", "확정", "기소", "구속", "체포", "적발",
    "시행령", "공포", "발효",
]

# 입법 5단계 카테고리
BILL_CATEGORIES = {
    "bill_proposed", "bill_committee", "bill_plenary",
    "bill_promulgated", "bill_enforced",
}


@dataclass
class ValidationResult:
    passed: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    action: str = "insert"  # "insert" | "insert_unverified" | "queue_review" | "reject"


def validate_issue(
    ai_result: dict,
    article: dict,
    politicians_map: dict[str, str],
) -> ValidationResult:
    """AI 분류 결과를 검증한다."""
    errors: list[str] = []
    warnings: list[str] = []

    # ── 1. camp 검증: 행위자 소속 일치 ──
    actor = ai_result.get("actor_name", "")
    ai_camp = ai_result.get("camp", "")

    if actor and actor in politicians_map:
        expected_camp = politicians_map[actor]
        if ai_camp != expected_camp:
            errors.append(
                f"CAMP_MISMATCH: {actor}는 {expected_camp}인데 {ai_camp}로 분류됨"
            )
    elif actor and actor not in politicians_map:
        warnings.append(f"UNKNOWN_POLITICIAN: {actor}가 DB에 없음")

    # ── 2. camp 값 자체 검증 ──
    if ai_camp not in ("blue", "red"):
        errors.append(f"INVALID_CAMP: {ai_camp}")

    # ── 3. 입법 카테고리 — 키워드 매칭 검증 ──
    category = ai_result.get("category", "")
    title_summary = f"{article.get('title', '')} {article.get('summary', '')}"

    if category in BILL_CATEGORIES:
        # 입법 카테고리인데 관련 키워드가 전혀 없으면 의심
        all_bill_kw = ["발의", "법안", "법률", "개정", "통과", "가결", "시행", "공포"]
        if not any(kw in title_summary for kw in all_bill_kw):
            warnings.append("BILL_NO_KEYWORD: 입법 카테고리인데 관련 키워드 없음")

    # ── 4. 가점 카테고리인데 발언만 있는 경우 ──
    if category in POSITIVE_CATEGORIES and category not in BILL_CATEGORIES:
        has_proposal = any(kw in title_summary for kw in PROPOSAL_KEYWORDS)
        has_result = any(kw in title_summary for kw in RESULT_KEYWORDS)
        if has_proposal and not has_result:
            errors.append("PROPOSAL_NOT_RESULT: 발언/제안만으로 가점 불가")

    # ── 5. confidence 검증 ──
    confidence = ai_result.get("confidence", 0)
    if confidence < 0.5:
        errors.append(f"VERY_LOW_CONFIDENCE: {confidence}")
    elif confidence < 0.7:
        warnings.append(f"LOW_CONFIDENCE: {confidence}")

    # ── 6. Tier 3 교차검증 필요 ──
    source_tier = article.get("source_tier", 3)
    if source_tier == 3:
        warnings.append("TIER3_NEEDS_CROSS_CHECK")

    # ── 7. severity 과장 체크 ──
    severity = ai_result.get("severity", "normal")
    if severity == "extreme" and confidence < 0.85:
        warnings.append("EXTREME_LOW_CONF: 극심 판정인데 confidence 낮음")

    # ── 8. evidence_sentence 유무 ──
    if not ai_result.get("evidence_sentence"):
        warnings.append("NO_EVIDENCE: 근거 문장 누락")

    # ── 결정 ──
    if errors:
        return ValidationResult(
            passed=False, errors=errors, warnings=warnings, action="queue_review"
        )
    elif warnings:
        return ValidationResult(
            passed=True, errors=[], warnings=warnings, action="insert_unverified"
        )
    else:
        return ValidationResult(
            passed=True, errors=[], warnings=[], action="insert"
        )
