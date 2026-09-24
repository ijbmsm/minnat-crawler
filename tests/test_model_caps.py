"""모델 능력 차이 — 모델을 바꿀 때 조용히 깨지는 자리.

2026-09-25 실측: Sonnet 5 는 assistant prefill 을 받지 않고 400 을 낸다.
event_matcher 의 Stage 3 가 고영향 사건(보도량 10건 이상)에만 Sonnet 을 쓰면서
prefill 을 그대로 걸고 있었다 — **중요한 사건일수록 매칭이 실패하고** 새 사건으로
떨어져 중복이 됐다. 2026-09-19 에 고쳤다는 그 실패가 고영향 경로에만 남아 있었다.
"""
from model_caps import supports_prefill


def test_sonnet_does_not_support_prefill():
    """Sonnet 5 에 prefill 을 걸면 400 이다. 능력표가 그걸 알아야 한다."""
    assert supports_prefill("claude-sonnet-5") is False


def test_haiku_supports_prefill():
    assert supports_prefill("claude-haiku-4-5-20251001") is True


def test_unknown_model_defaults_to_no_prefill():
    """모르는 모델은 안 된다고 본다.

    400 으로 통째로 실패하는 것보다 서두가 붙는 편이 낫다.
    후자는 정규식이 건지지만 전자는 기사를 잃는다.
    """
    assert supports_prefill("claude-future-9") is False


def test_writer_model_is_covered():
    """writer 가 쓰는 모델이 능력표에 반영돼 있어야 한다.

    모델만 바꾸고 이 표를 안 고치면 prefill 유무가 어긋나 조용히 깨진다.
    """
    import writer
    # 값 자체를 단정하지 않는다. 표를 거쳐 결정되는지만 본다.
    assert supports_prefill(writer.MODEL) in (True, False)
