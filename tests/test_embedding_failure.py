"""임베딩 실패는 영벡터가 아니라 None 이어야 한다.

영벡터를 저장하면 pgvector 의 `<=>` 가 NaN 을 내고, Postgres 는 NaN 을 최댓값으로
취급하므로 그 행이 유사도 임계값을 통과하고 정렬 1위로 올라온다.
2026-09 프로덕션에서 실제로 클러스터 67건 중 16건이 이 상태였고 (나머지 51건은 NULL),
정상 임베딩은 0건이었다. 원인은 OpenAI 크레딧 소진.
"""
import event_matcher
from event_matcher import get_embedding, stage2_embedding_match, EMBEDDING_FAILURES


class _Boom:
    class embeddings:  # noqa: N801
        @staticmethod
        def create(**_kwargs):
            raise RuntimeError("You have no credits remaining")


class TestGetEmbedding:
    def setup_method(self):
        EMBEDDING_FAILURES.clear()

    def test_빈_문자열은_None(self):
        assert get_embedding("   ") is None
        assert get_embedding("") is None

    def test_API_실패시_영벡터가_아니라_None(self, monkeypatch):
        monkeypatch.setattr(event_matcher, "openai_client", _Boom)
        assert get_embedding("크레딧 소진 상황") is None

    def test_실패가_기록되어_조용히_넘어가지_않는다(self, monkeypatch):
        monkeypatch.setattr(event_matcher, "openai_client", _Boom)
        get_embedding("가")
        get_embedding("나")
        assert len(EMBEDDING_FAILURES) == 2
        assert "credits" in EMBEDDING_FAILURES[0]


class TestStage2Guard:
    def test_임베딩이_None_이면_매칭하지_않는다(self):
        candidates = [{"id": "e1", "embedding": [0.1] * 1536}]
        assert stage2_embedding_match(None, candidates) == (None, [])

    def test_저장된_영벡터는_후보에서_제외한다(self):
        # 과거에 잘못 저장된 영벡터 — 유사도 0 으로 계산되므로 버려야 한다
        candidates = [{"id": "zero", "embedding": [0.0] * 1536}]
        confirmed, gray = stage2_embedding_match([0.1] * 1536, candidates)
        assert confirmed is None
        assert gray == []

    def test_정상_벡터는_비교한다(self):
        vec = [0.0] * 1536
        vec[0] = 1.0
        candidates = [{"id": "same", "embedding": vec}]
        confirmed, gray = stage2_embedding_match(vec, candidates)
        # 동일 벡터이므로 유사도 1.0 → 확정 매칭
        assert confirmed is not None
        assert confirmed["id"] == "same"
