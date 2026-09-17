"""next_branch 검증 — 확정 일정만 통과시킨다.

화면에 "다음 분기점" 으로 나가는 값이라, 추측이 섞이면
서비스 원칙("추측·평가·단정 표현 금지")을 정면으로 어긴다.
"""
from datetime import datetime, timedelta

from analyzer import sanitize_next_branch


def _future(days: int) -> str:
    return (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")


class TestSanitizeNextBranch:
    def test_확정_일정은_통과한다(self):
        out = sanitize_next_branch({
            "date": _future(3),
            "title": "인사청문경과보고서 채택 시한",
            "description": "시한을 넘기면 임명을 강행할 수 있다.",
        })
        assert out is not None
        assert out["title"] == "인사청문경과보고서 채택 시한"
        assert "시한을 넘기면" in out["description"]

    def test_설명이_없어도_통과한다(self):
        out = sanitize_next_branch({"date": _future(10), "title": "1심 선고"})
        assert out == {"date": _future(10), "title": "1심 선고"}

    def test_None_이나_잘못된_타입은_버린다(self):
        assert sanitize_next_branch(None) is None
        assert sanitize_next_branch("2026-09-19") is None
        assert sanitize_next_branch([]) is None

    def test_날짜나_제목이_비면_버린다(self):
        assert sanitize_next_branch({"title": "선고"}) is None
        assert sanitize_next_branch({"date": _future(3)}) is None
        assert sanitize_next_branch({"date": _future(3), "title": "  "}) is None

    def test_날짜_형식이_틀리면_버린다(self):
        assert sanitize_next_branch({"date": "2026년 9월 19일", "title": "선고"}) is None
        assert sanitize_next_branch({"date": "조만간", "title": "선고"}) is None
        assert sanitize_next_branch({"date": "2026-13-45", "title": "선고"}) is None

    def test_추측성_표현이_있으면_날짜가_있어도_버린다(self):
        for word in ["전망", "예상", "가능성", "보인다", "조만간", "이르면"]:
            assert sanitize_next_branch({
                "date": _future(5), "title": f"선고 {word}",
            }) is None, word

    def test_설명에_추측이_섞여도_버린다(self):
        assert sanitize_next_branch({
            "date": _future(5),
            "title": "1심 선고",
            "description": "유죄가 나올 가능성이 높다",
        }) is None

    def test_기사_발행일보다_과거면_버린다(self):
        past = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        assert sanitize_next_branch(
            {"date": past, "title": "선고"},
            published_at=datetime.now().isoformat(),
        ) is None

    def test_너무_먼_미래는_버린다(self):
        assert sanitize_next_branch({"date": _future(500), "title": "선고"}) is None

    def test_긴_문자열은_잘린다(self):
        out = sanitize_next_branch({
            "date": _future(3),
            "title": "가" * 200,
            "description": "나" * 500,
        })
        assert out is not None
        assert len(out["title"]) == 80
        assert len(out["description"]) == 300
