"""사안 뼈대 — LLM 없이 결정론으로 나오는 부분.

등급(확정/혐의/주장)은 사실관계다. 인터넷 검색이 이 셋을 섞어 주는 게 이 제품이
메우려는 빈틈인데, 우리가 다시 섞으면 존재 이유가 없다. 그래서 LLM 이 아니라
규칙으로 뽑고, 그 규칙을 여기서 고정한다.
"""
from storyline_shape import (
    event_grade, chapter_grade, split_chapters, when_label, story_status,
    MAX_CHAPTERS, CHAPTER_GAP_DAYS,
)


def ev(date, stage=None, category="media_coverage", tier=3, verified=False):
    return {
        "first_reported_at": date, "criminal_stage": stage,
        "category": category, "source_tier": tier, "verified": verified,
    }


class TestEventGrade:
    def test_법원_판단이_난_단계는_확정(self):
        for stage in ("guilty_1st", "guilty_2nd", "confirmed", "pardoned"):
            assert event_grade(ev("2024-01-01", stage)) == "confirmed"

    def test_무죄_혐의없음도_확정이다(self):
        # 유죄만 확정으로 치면 한쪽만 기록된다
        assert event_grade(ev("2024-01-01", "not_guilty")) == "confirmed"
        assert event_grade(ev("2024-01-01", "no_charges")) == "confirmed"

    def test_수사_기소는_혐의지_확정이_아니다(self):
        for stage in ("investigation", "indicted", "suspended_indictment"):
            assert event_grade(ev("2024-01-01", stage)) == "alleged"

    def test_본인_시인과_공식기관_처분은_확정(self):
        assert event_grade(ev("2024-01-01", None, "self_admission", verified=True)) == "confirmed"
        assert event_grade(ev("2024-01-01", None, "official_misconduct", verified=True)) == "confirmed"

    def test_검증_안_된_시인은_확정이_아니다(self):
        assert event_grade(ev("2024-01-01", None, "self_admission", verified=False)) == "alleged"

    def test_1차_출처_기록은_확정(self):
        assert event_grade(ev("2024-01-01", None, "policy_record", tier=1, verified=True)) == "confirmed"

    def test_보도와_논란발언은_주장(self):
        assert event_grade(ev("2024-01-01", None, "media_coverage")) == "claim"
        assert event_grade(ev("2024-01-01", None, "controversial_statement")) == "claim"


class TestChapterGrade:
    def test_가장_단단한_근거를_따른다(self):
        events = [ev("2024-01-01", None, "media_coverage"), ev("2024-01-05", "confirmed")]
        assert chapter_grade(events) == "confirmed"

    def test_전부_보도면_주장(self):
        assert chapter_grade([ev("2024-01-01"), ev("2024-01-02")]) == "claim"

    def test_빈_목록은_주장(self):
        assert chapter_grade([]) == "claim"


class TestSplitChapters:
    def test_형사_단계가_오르면_자른다(self):
        events = [
            ev("2024-01-01", "investigation"),
            ev("2024-01-05", "investigation"),
            ev("2024-01-10", "indicted"),
        ]
        chapters = split_chapters(events)
        assert len(chapters) == 2
        assert len(chapters[0]) == 2

    def test_단계가_내려가면_자르지_않는다(self):
        # 오래된 기사가 뒤늦게 들어오는 경우까지 국면 전환으로 보면 장이 난도질된다
        events = [ev("2024-01-01", "indicted"), ev("2024-01-05", "investigation")]
        assert len(split_chapters(events)) == 1

    def test_보도_공백이_길면_자른다(self):
        events = [ev("2024-01-01"), ev("2024-01-02"), ev("2025-01-01")]
        assert len(split_chapters(events)) == 2

    def test_공백이_짧으면_한_장(self):
        events = [ev("2024-01-01"), ev("2024-02-01"), ev("2024-03-01")]
        assert len(split_chapters(events)) == 1

    def test_최대_장수를_넘지_않는다(self):
        events = [ev(f"20{y:02d}-01-01", "investigation") for y in range(10, 30)]
        chapters = split_chapters(events)
        assert len(chapters) <= MAX_CHAPTERS
        # 합치는 과정에서 사건이 사라지면 안 된다
        assert sum(len(c) for c in chapters) == len(events)

    def test_시간순으로_정렬된다(self):
        events = [ev("2024-03-01"), ev("2024-01-01"), ev("2024-02-01")]
        flat = [e for c in split_chapters(events) for e in c]
        assert [e["first_reported_at"] for e in flat] == ["2024-01-01", "2024-02-01", "2024-03-01"]

    def test_날짜없는_사건은_빠진다(self):
        assert split_chapters([{"first_reported_at": None}]) == []


class TestWhenLabel:
    def test_같은_달이면_연월(self):
        assert when_label([ev("2021-10-01"), ev("2021-10-20")]) == "2021.10"

    def test_같은_해_다른_달(self):
        assert when_label([ev("2021-09-01"), ev("2021-12-20")]) == "2021.9 – 12"

    def test_해를_넘기면_연도_범위(self):
        assert when_label([ev("2022-01-01"), ev("2023-12-20")]) == "2022 – 2023"


class TestStoryStatus:
    def test_확정_사면이면_종결(self):
        status, ended = story_status([ev("2020-01-01", "indicted"), ev("2021-12-31", "pardoned")])
        assert status == "closed"
        assert ended == "2021-12-31"

    def test_무죄도_종결이다(self):
        status, _ = story_status([ev("2021-01-01", "not_guilty")])
        assert status == "closed"

    def test_재판_중이면_진행중(self):
        status, ended = story_status([ev("2024-01-01", "guilty_1st")])
        assert status == "ongoing"
        assert ended is None
