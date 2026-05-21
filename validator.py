"""2차 검증 v1.1 — AI 분류 결과를 DB INSERT 전에 검증"""
from dataclasses import dataclass, field
from config import SCORED_CATEGORIES


@dataclass
class ValidationResult:
    passed: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    action: str = "insert"


def validate_issue(
    ai_result: dict,
    article: dict,
    politicians_map: dict[str, str],
) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []

    # 1. camp 검증
    actor = ai_result.get("actor_name", "")
    ai_camp = ai_result.get("camp", "")

    if ai_camp not in ("blue", "red"):
        errors.append(f"INVALID_CAMP: {ai_camp}")

    if actor and actor in politicians_map:
        expected = politicians_map[actor]
        if ai_camp != expected:
            errors.append(f"CAMP_MISMATCH: {actor}는 {expected}인데 {ai_camp}로 분류")

    # 2. criminal_conviction에 criminal_stage 필수
    category = ai_result.get("category", "")
    if category == "criminal_conviction" and not ai_result.get("criminal_stage"):
        errors.append("CRIMINAL_NO_STAGE: criminal_conviction인데 criminal_stage 없음")

    # 3. confidence
    confidence = ai_result.get("confidence", 0)
    if confidence < 0.5:
        errors.append(f"VERY_LOW_CONFIDENCE: {confidence}")
    elif confidence < 0.7:
        warnings.append(f"LOW_CONFIDENCE: {confidence}")

    # 4. evidence_sentence
    if not ai_result.get("evidence_sentence"):
        warnings.append("NO_EVIDENCE: 근거 문장 누락")

    # 결정
    if errors:
        return ValidationResult(passed=False, errors=errors, warnings=warnings, action="queue_review")
    elif warnings:
        return ValidationResult(passed=True, errors=[], warnings=warnings, action="insert_unverified")
    return ValidationResult(passed=True, errors=[], warnings=[], action="insert")
