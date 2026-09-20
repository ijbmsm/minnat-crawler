"""원고 안전장치 — 없는 날짜를 만들지 않는가.

첫 시험에서 모델이 "헌법재판소는 12월 14일 파면했다"고 썼다. 실제 파면은 이듬해
4월이었다. 우리가 넘긴 값이 사건 발생일이 아니라 **보도일**이었기 때문이다.
틀린 날짜가 실린 페이지는 없느니만 못하므로, 근거 없는 날짜가 하나라도 있으면
원고 전체를 버린다.
"""
from storyline_author import ungrounded_dates, _day_in_source

SOURCE = "12·3 비상계엄 선포. 내란수괴 혐의 기소. 헌재 탄핵 인용(8:0) 파면. 2024-12-04"


class TestUngroundedDates:
    def test_입력에_있는_월일은_통과(self):
        # "12·3" 이 입력에 있으므로 "12월 3일" 은 근거가 있다
        assert ungrounded_dates("12월 3일 비상계엄이 선포됐다", SOURCE) == []

    def test_입력에_없는_월일은_걸린다(self):
        # 이게 실제로 났던 사고다
        assert ungrounded_dates("헌법재판소는 12월 14일 파면했다", SOURCE) == ["12월 14일"]

    def test_입력_숫자에_있는_월은_통과(self):
        assert ungrounded_dates("2024년 12월 비상계엄", SOURCE) == []

    def test_입력에_없는_월은_걸린다(self):
        assert ungrounded_dates("이듬해 4월 파면됐다", SOURCE) == ["4월"]

    def test_날짜가_없으면_통과(self):
        assert ungrounded_dates("검찰은 내란수괴 혐의로 기소했다", SOURCE) == []

    def test_개월은_날짜가_아니다(self):
        # "3개월" 의 "3" 을 월로 읽으면 안 된다
        assert ungrounded_dates("수사가 3개월 이어졌다", SOURCE) == []

    def test_빈_입력에도_죽지_않는다(self):
        assert ungrounded_dates("", "") == []
        assert ungrounded_dates(None, None) == []


class TestDayInSource:
    def test_여러_표기를_모두_인정한다(self):
        for src in ("12·3 비상계엄", "12월 3일 선포", "2024-12-03 보도", "12.3 사건"):
            assert _day_in_source("12", "3", src) is True

    def test_다른_날짜는_인정하지_않는다(self):
        assert _day_in_source("12", "14", "12·3 비상계엄") is False
