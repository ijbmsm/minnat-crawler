"""사건 카테고리 승격 — 얼어붙은 카테고리가 점수를 0 으로 묶는다.

2026-09-19: 김승원 자진사퇴 기사 20건 중 1건이 self_admission(공식 시인)이었는데,
그 기사가 policy_record 사건에 머지되면서 사건 카테고리가 바뀌지 않았다.
criminal_stage·source_tier·verified·position_weight 는 전부 멤버 최댓값으로
올라가는데 category 만 최초 생성값으로 얼어 있었다. 그래서 20건짜리 사건이 0점.

같은 파일에서 매칭 쪽도 함께 본다. 카테고리가 다른 사건에 임베딩만으로 자동 확정하면
공식 처분 기사가 archive 사건으로 조용히 빨려 들어간다.
"""
from event_manager import _best_category, recalculate_event_score
from event_matcher import stage2_embedding_match


def member(category: str, confidence: float = 0.9) -> dict:
    return {"category": category, "ai_analysis": {"confidence": confidence}}


class TestBestCategory:
    def test_공식_처분이_archive_를_이긴다(self):
        members = [member("policy_record"), member("media_coverage"), member("self_admission")]
        assert _best_category(members, "policy_record") == "self_admission"

    def test_전부_archive_면_그대로_둔다(self):
        members = [member("policy_record"), member("media_coverage")]
        assert _best_category(members, "policy_record") == "policy_record"

    def test_더_무거운_처분이_이긴다(self):
        # SCORED_CATEGORIES 선언 순서가 서열이다 — 형사가 윤리·시인보다 앞
        members = [member("self_admission"), member("criminal_conviction")]
        assert _best_category(members, "media_coverage") == "criminal_conviction"

    def test_확신하지_못한_분류는_승격_근거가_아니다(self):
        # 프롬프트가 "애매하면 0.6 이하" 라고 지시한다. 그 아래는 분류기가
        # 스스로 뒤집을 수 있다고 말한 것이므로 20건짜리 사건을 뒤집게 두지 않는다
        members = [member("policy_record"), member("criminal_conviction", confidence=0.4)]
        assert _best_category(members, "policy_record") == "policy_record"

    def test_confidence_가_없으면_승격하지_않는다(self):
        members = [{"category": "criminal_conviction"}]
        assert _best_category(members, "media_coverage") == "media_coverage"

    def test_멤버가_없으면_기존_값(self):
        assert _best_category([], "media_coverage") == "media_coverage"


class TestScoreAfterPromotion:
    """승격이 실제로 점수를 살리는지 — 김승원 사건의 실제 값으로 본다."""

    EVENT = {
        "source_tier": 3,
        "verified": True,
        "coverage_count": 3,
        "headline_days": 1,
        "position_weight": 0.8,
    }

    def test_승격_전에는_0점(self):
        assert recalculate_event_score({**self.EVENT, "category": "policy_record"}) == 0.0

    def test_승격_후에는_점수가_붙는다(self):
        assert recalculate_event_score({**self.EVENT, "category": "self_admission"}) > 0

    def test_미검증_tier3_는_승격해도_0점(self):
        # 카테고리를 살려도 교차검증이 없으면 점수는 안 준다 — 방법론 그대로
        event = {**self.EVENT, "category": "self_admission", "verified": False}
        assert recalculate_event_score(event) == 0.0


class TestCrossCategoryConfirm:
    """카테고리가 다른 사건은 임베딩만으로 확정하지 않는다."""

    EVENT = {"id": "e1", "category": "policy_record", "embedding": [1.0, 0.0]}

    def test_기본값은_확정을_허용한다(self):
        confirmed, gray = stage2_embedding_match([1.0, 0.0], [self.EVENT])
        assert confirmed is not None
        assert gray == []

    def test_allow_confirm_False_면_회색지대로_넘긴다(self):
        confirmed, gray = stage2_embedding_match([1.0, 0.0], [self.EVENT], allow_confirm=False)
        assert confirmed is None
        assert len(gray) == 1
        assert gray[0]["event"]["id"] == "e1"

    def test_임베딩이_없으면_아무것도_하지_않는다(self):
        assert stage2_embedding_match(None, [self.EVENT], allow_confirm=False) == (None, [])
