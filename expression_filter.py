"""표현 자동 검수 — 단정/평가 표현 차단 + attribution 변환"""
from config import BANNED_EXPRESSIONS

# 단정 → attribution 변환
ATTRIBUTION_MAP = {
    "뇌물을 받았다": "뇌물 수수 혐의로 기소됐다",
    "거짓말을 했다": "사실과 다른 발언을 한 것으로 확인됐다",
    "범죄를 저질렀다": "혐의를 받고 있다",
    "횡령했다": "횡령 혐의를 받고 있다",
    "배임했다": "배임 혐의로 조사를 받고 있다",
    "유죄다": "유죄 판결을 받았다",
}


def filter_expression(text: str) -> tuple[str, list[str]]:
    """텍스트에서 금지 표현을 제거/변환한다.

    Returns:
        (정제된 텍스트, 변환 목록)
    """
    changes: list[str] = []
    result = text

    # 1. 금지 형용사 제거
    for banned in BANNED_EXPRESSIONS:
        if banned in result:
            result = result.replace(banned, "")
            changes.append(f"금지 표현 제거: '{banned}'")

    # 2. 단정 → attribution 변환
    for assertion, attribution in ATTRIBUTION_MAP.items():
        if assertion in result:
            result = result.replace(assertion, attribution)
            changes.append(f"attribution 변환: '{assertion}' → '{attribution}'")

    return result.strip(), changes


def needs_unconfirmed_label(criminal_stage: str | None) -> bool:
    """'확정 전' 라벨이 필요한지 판단한다."""
    if criminal_stage is None:
        return False
    return criminal_stage not in ("confirmed", "not_guilty", "no_charges", "dismissed")
