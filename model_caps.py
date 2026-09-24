"""모델별 능력 차이 — 한 곳에 모은다.

모델을 바꿀 때 조용히 깨지는 자리가 있다. 2026-09-25 실측으로 확인한 것:

- **assistant prefill**: Haiku 4.5 는 되고 Sonnet 5 는 400 을 낸다
  ("This model does not support assistant message prefill").
  event_matcher 의 Stage 3 가 고영향 사건(보도량 10건 이상)에만 Sonnet 을 쓰면서
  prefill 을 그대로 걸고 있었다. 즉 **중요한 사건일수록 매칭이 실패하고**
  새 사건으로 떨어져 중복이 됐다. 2026-09-19 에 고쳤다는 그 실패가
  고영향 경로에만 되살아나 있었다.

- **프롬프트 캐시 최소 길이**: Haiku 4.5 는 4,096 토큰(2,048 이 아니다).
  임계 밑이면 cache_control 이 조용히 무시된다. Sonnet 5 는 1,797 토큰에서
  걸리는 것을 확인했다.

새 모델을 추가하면 **여기서 먼저 확인한다.** 호출부에 흩어 놓으면 한 곳만
고치고 나머지를 놓친다.
"""

# prefill 을 받는 모델. 모르는 모델은 안 된다고 본다 —
# 400 으로 통째로 실패하는 것보다 서두가 붙는 편이 낫다.
_PREFILL_OK = {
    "claude-haiku-4-5-20251001",
}


def supports_prefill(model: str) -> bool:
    """assistant 턴으로 응답 앞부분을 미리 채울 수 있는가."""
    return model in _PREFILL_OK
