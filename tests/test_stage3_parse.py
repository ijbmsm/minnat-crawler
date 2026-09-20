"""Stage 3 판정 응답 파싱 — JSON 이 아닌 형태로 와도 중복을 만들지 않아야 한다.

2026-09-19 실행에서 Stage 3 가 24번 호출돼 24번 모두 파싱에 실패했다.
원인은 모델이 "주어진 정보를 분석하겠습니다." 같은 서두를 먼저 쓰고 max_tokens 에서
잘려 JSON 이 아예 나오지 않은 것. 실패하면 그 기사가 전부 "새 사건"으로 떨어져
중복이 된다 — 그래서 이 파서는 dedup 의 마지막 방어선이다.
"""
from event_matcher import _parse_match, PREFILL


class TestParse:
    def test_prefill_로_시작한_정상_응답(self):
        assert _parse_match(PREFILL + ' 1}') == 1
        assert _parse_match(PREFILL + ' 0}') == 0

    def test_뒤에_설명이_붙어도_읽는다(self):
        assert _parse_match(PREFILL + ' 2}\n\n**분석 근거:** 행위자가 같다') == 2

    def test_닫는_중괄호가_잘려도_읽는다(self):
        # max_tokens 에서 끊긴 경우 — 정규식 최후 수단이 받는다
        assert _parse_match(PREFILL + ' 3') == 3

    def test_숫자가_없으면_None(self):
        assert _parse_match(PREFILL + ' }') is None
        assert _parse_match(PREFILL) is None

    def test_서두만_오면_None(self):
        # 실패해도 예외를 던지지 않고 None 을 줘야 한다. 던지면 기사 처리가 통째로 죽는다
        assert _parse_match("주어진 정보를 분석하겠습니다.\n\n**새 기사 분석:**") is None

    def test_문자열_값은_받지_않는다(self):
        assert _parse_match('{"match": "one"}') is None
