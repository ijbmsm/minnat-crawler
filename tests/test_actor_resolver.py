"""행위자 교정 — 약칭을 잘못 푼 이름이 사건을 쪼갠다.

2026-09-19 실측: "李대통령 청년정책" 한 사건의 기사들이 actor 로 이재명·이준석·윤석열을
달고 들어와 클러스터가 넷으로 갈렸다. Stage 1 매칭 키가 actor_name + category 이므로
이름이 갈리면 같은 사건이 만나지 못하고, coverage_count 가 1 에 묶여 tier3 가
점수를 받는 유일한 조건인 교차검증(verified)이 붙지 않는다.
"""
from actor_resolver import normalize_actor_name, resolve_actor

# 실제 DB 를 축약한 것. 이재명이 '대통령' 인 것이 핵심이다 — 이게 '의원' 이면
# 약칭 교정도, 직책 가중치 1.2 도 동작하지 않는다.
POSITIONS = {
    "이재명": "대통령",
    "이준석": "대표",
    "윤석열": "전 대통령",
    "김승원": "의원",
    "한동훈": "전 대표",
    "박찬대": "원내대표",
    "이소영": "의원",
    "오세훈": "서울시장",
}


class TestNormalize:
    def test_직책_접미사를_뗀다(self):
        assert normalize_actor_name("김승원 의원") == "김승원"
        assert normalize_actor_name("이재명 대통령") == "이재명"
        assert normalize_actor_name("오세훈 서울시장") == "오세훈"

    def test_이름만_있으면_그대로(self):
        assert normalize_actor_name("김승원") == "김승원"
        assert normalize_actor_name("") == ""
        assert normalize_actor_name(None) == ""

    def test_이름이_직책과_같으면_자르지_않는다(self):
        # "대통령" 한 단어만 오면 잘라서 빈 문자열을 만들면 안 된다
        assert normalize_actor_name("대통령") == "대통령"


class TestResolveActor:
    def test_본문에_실명이_있으면_그대로_둔다(self):
        text = "김승원 법무장관 후보자가 자진 사퇴했다"
        name, note = resolve_actor("김승원", text, POSITIONS)
        assert name == "김승원"
        assert note == ""

    def test_한자_약칭을_직책으로_푼다(self):
        # 실제로 이준석으로 저장됐던 기사
        text = "[속보]李대통령 \"청년 정책 총괄할 별도 전담 조직 만들어야\""
        name, note = resolve_actor("이준석", text, POSITIONS)
        assert name == "이재명"
        assert "교정" in note

    def test_한글_약칭을_직책으로_푼다(self):
        # 실제로 윤석열로 저장됐던 기사
        text = "\"국민 눈높이\" 이 대통령, '민심 역행' 김승원 임명 강행할까"
        name, _ = resolve_actor("윤석열", text, POSITIONS)
        assert name == "이재명"

    def test_전직은_현직_직책으로_매칭되지_않는다(self):
        # 윤석열은 '전 대통령' 이라 "이 대통령" 의 후보가 아니다
        assert "윤석열" not in resolve_actor("윤석열", "이 대통령이 말했다", POSITIONS)[0]

    def test_원내대표는_대표가_아니다(self):
        # "이 대표" 는 이준석(대표)이지 박찬대(원내대표)가 아니다
        name, _ = resolve_actor("김승원", "이 대표가 회견을 열었다", POSITIONS)
        assert name == "이준석"

    def test_시장은_지역이_붙어도_매칭된다(self):
        name, _ = resolve_actor("김승원", "오 시장이 예산안을 발표했다", POSITIONS)
        assert name == "오세훈"

    def test_같은_성_같은_직책이_둘이면_포기한다(self):
        positions = {"이소영": "의원", "이재명": "의원"}
        name, note = resolve_actor("김승원", "이 의원이 발의했다", positions)
        assert name == "김승원"  # LLM 값 유지
        assert note == ""

    def test_약칭이_없으면_본문의_유일한_실명으로_교정한다(self):
        text = "한동훈은 \"국민의 승리\"라고 말했다"
        name, note = resolve_actor("이재명", text, POSITIONS)
        assert name == "한동훈"
        assert "유일한 실명" in note

    def test_실명이_여럿이면_교정하지_않는다(self):
        text = "한동훈과 김승원이 공방을 벌였다"
        name, note = resolve_actor("이재명", text, POSITIONS)
        assert name == "이재명"
        assert note == ""

    def test_빈_이름은_빈_채로_돌려준다(self):
        assert resolve_actor("", "아무 기사", POSITIONS) == ("", "")
