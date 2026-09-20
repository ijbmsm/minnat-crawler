"""issue_clusters 의 비어 있는 embedding 을 다시 채운다.

왜 필요한가:
  2026-09 에 OpenAI 크레딧이 소진되면서 임베딩이 몇 달간 전부 NULL/영벡터로 저장됐다.
  그 결과 (1) 사건 매칭 Stage 2 가 통째로 꺼져 같은 사건이 여러 건으로 쪼개졌고,
  (2) `match_similar_events` 가 영벡터 코사인 거리 NaN 을 최댓값으로 취급해
  "유사 사례"가 무작위로 나왔다(022 로 증상은 막았지만 데이터는 그대로였다).

이 스크립트는 **비어 있는 행만** 채운다. 정상 임베딩은 건드리지 않는다.
main.py 전체 실행과 달리 뉴스를 크롤하지 않고, LLM 도 부르지 않으며,
새 행을 만들지 않는다 — 기존 행의 embedding 컬럼 하나만 UPDATE 한다.

사용:
    python backfill_embeddings.py            # 드라이런 — 무엇을 채울지만 출력
    python backfill_embeddings.py --apply    # 실제로 기록
"""
import sys

from db import get_client
from event_matcher import get_embedding, EMBEDDING_FAILURES


def _parse(embedding) -> list[float] | None:
    """None(=NULL) 과 영벡터를 구분해 돌려준다."""
    if embedding is None:
        return None
    if isinstance(embedding, list):
        return embedding
    if isinstance(embedding, str):
        body = embedding.strip("[] ")
        if not body:
            return []
        try:
            return [float(x) for x in body.split(",")]
        except ValueError:
            return []
    return []


def needs_backfill(embedding) -> bool:
    v = _parse(embedding)
    if v is None:
        return True
    return not v or all(x == 0.0 for x in v)


def run(apply: bool) -> int:
    client = get_client()

    clusters = (
        client.table("issue_clusters")
        .select("id, representative_issue_id, embedding, actor_name, summary")
        .execute()
        .data
    )
    targets = [c for c in clusters if needs_backfill(c.get("embedding"))]

    print(f"issue_clusters {len(clusters)}건 중 재생성 대상 {len(targets)}건")
    if not targets:
        print("채울 것이 없습니다.")
        return 0

    # 대표 이슈 본문을 한 번에 가져온다
    rep_ids = [c["representative_issue_id"] for c in targets if c.get("representative_issue_id")]
    issues: dict[str, dict] = {}
    for i in range(0, len(rep_ids), 50):
        chunk = rep_ids[i : i + 50]
        rows = client.table("issues").select("id, title, summary").in_("id", chunk).execute().data
        issues.update({r["id"]: r for r in rows})

    filled = skipped = failed = 0

    for c in targets:
        rep = issues.get(c.get("representative_issue_id") or "")
        if not rep:
            print(f"  [skip] {c['id'][:8]} — 대표 이슈를 찾을 수 없음")
            skipped += 1
            continue

        # main.py / event_manager.py 와 같은 규칙이어야 벡터가 서로 비교 가능하다
        text = f"{rep.get('title', '')} {rep.get('summary', '')}".strip()
        if not text:
            print(f"  [skip] {c['id'][:8]} — 임베딩할 텍스트가 비어 있음")
            skipped += 1
            continue

        if not apply:
            print(f"  [dry] {c['id'][:8]} ← {text[:58]}…")
            filled += 1
            continue

        vector = get_embedding(text)
        # 영벡터를 절대 저장하지 않는다 — 그게 애초에 이 사태의 원인이었다
        if vector is None or all(x == 0.0 for x in vector):
            print(f"  [fail] {c['id'][:8]} — 임베딩 생성 실패")
            failed += 1
            continue

        client.table("issue_clusters").update({"embedding": vector}).eq("id", c["id"]).execute()
        filled += 1
        print(f"  [ok] {c['id'][:8]} ← {text[:58]}…")

    verb = "채울 예정" if not apply else "채움"
    print(f"\n{verb} {filled}건 · 건너뜀 {skipped}건 · 실패 {failed}건")
    if EMBEDDING_FAILURES:
        print(f"임베딩 오류 {len(EMBEDDING_FAILURES)}건 — 예시: {EMBEDDING_FAILURES[0][:140]}")
    if not apply:
        print("\n실제로 기록하려면: python backfill_embeddings.py --apply")
    return failed


if __name__ == "__main__":
    sys.exit(run(apply="--apply" in sys.argv))
