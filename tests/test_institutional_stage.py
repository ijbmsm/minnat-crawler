"""제도적 결정 단계 — 형사 절차와 다른 축이다.

criminal_stage 는 수사→기소→1심→…만 담는다. 국회 탄핵소추 가결이나 헌재 인용은
형사 절차가 아닌데 담을 어휘가 없어 분류기가 억지로 형사 단계를 골랐다.
그 결과 "헌재 전원일치 파면"이 criminal_stage=indicted 로 저장돼 화면에서
근거등급 '혐의'로 표시됐다. 확정된 제도적 결정을 혐의로 적으면 이 제품이
메우려던 빈틈을 우리가 다시 만드는 셈이다.
"""
from backfill_institutional_stage import classify
from storyline_shape import event_grade


class TestClassify:
    def test_헌재_인용_파면(self):
        assert classify("윤석열 헌재 탄핵 인용 파면") == "impeachment_upheld"
        assert classify("윤석열 대통령 탄핵 인용 — 헌법재판소 전원일치 파면") == "impeachment_upheld"

    def test_헌재_기각(self):
        assert classify("노무현 국회 탄핵소추 — 헌재 기각") == "impeachment_rejected"
        assert classify("이상민 행안부장관 탄핵소추 헌재 기각") == "impeachment_rejected"

    def test_국회_가결(self):
        assert classify("윤석열 국회 탄핵소추안 가결") == "impeachment_passed"

    def test_인용이_소추보다_먼저_잡힌다(self):
        # 파면 기사에 "탄핵소추"가 같이 들어 있어도 결과는 인용이어야 한다
        assert classify("국회 탄핵소추 이후 헌재 탄핵 인용으로 파면") == "impeachment_upheld"

    def test_관계없는_기사는_None(self):
        assert classify("윤석열 비상계엄 선포") is None
        assert classify("김용현 국방부장관 내란 혐의 구속 기소") is None
        assert classify("") is None


class TestGradeWithInstitutional:
    def test_제도적_결정은_확정이다(self):
        # 국회가 가결했다·헌재가 인용했다는 일어난 사실이다
        for stage in ("impeachment_upheld", "impeachment_rejected", "impeachment_passed"):
            assert event_grade({"institutional_stage": stage, "criminal_stage": None}) == "confirmed"

    def test_형사_혐의와_섞이면_제도_쪽이_이긴다(self):
        # 실제로 났던 사고: 헌재 파면 기사에 criminal_stage=indicted 가 붙어
        # 확정된 결정이 '혐의'로 표시됐다
        got = event_grade({"institutional_stage": "impeachment_upheld", "criminal_stage": "indicted"})
        assert got == "confirmed"

    def test_제도_단계가_없으면_기존_규칙_그대로(self):
        assert event_grade({"institutional_stage": None, "criminal_stage": "indicted"}) == "alleged"
        assert event_grade({"criminal_stage": "confirmed"}) == "confirmed"
        assert event_grade({"criminal_stage": None, "category": "media_coverage"}) == "claim"

    def test_모르는_값은_무시한다(self):
        assert event_grade({"institutional_stage": "made_up", "criminal_stage": "indicted"}) == "alleged"
