"""기존 기사·사건에 institutional_stage 를 채운다 (마이그레이션 028 이후 1회).

분류기가 앞으로는 채우지만, 이미 저장된 행은 형사 단계 칸에 탄핵을 억지로 적어 뒀다.
그 결과 "헌재 전원일치 파면"이 근거등급 '혐의'로 표시됐다.

**제목만** 본다. 처음엔 summary 까지 훑었는데 기사 주제가 아닌 배경 언급에 반응해
오분류가 났다 — "박근혜 국정농단 대법원 확정"(형사 확정 기사)이 요약에 섞인
탄핵 얘기 때문에 impeachment_upheld 가 됐다. 제목이 그 기사가 기록하는 것이다.

클러스터는 제목이 없으므로 **소속 기사에서 물려받는다.** event_manager 가
평소에 쓰는 규칙과 같다(종국 결정이 발의·가결보다 앞선다).

사용:
    python backfill_institutional_stage.py            # 드라이런
    python backfill_institutional_stage.py --apply
"""
import sys

from db import get_client
from event_manager import _best_institutional_stage

# 순서가 중요하다 — 위에서부터 먼저 맞는 것을 쓴다.
# "탄핵 인용"이 "탄핵소추"보다 앞서야 파면 기사가 소추로 분류되지 않는다.
RULES: list[tuple[str, tuple[str, ...]]] = [
    ("impeachment_upheld",   ("탄핵 인용", "탄핵인용", "인용 파면", "전원일치 파면")),
    ("impeachment_rejected", ("헌재 기각", "헌법재판소 기각", "헌재 각하", "탄핵 기각", "탄핵 각하", "탄핵소추(기각)")),
    ("impeachment_passed",   ("탄핵소추안 가결", "탄핵안 가결", "탄핵소추 가결")),
    ("impeachment_proposed", ("탄핵소추", "탄핵안 발의", "탄핵 추진")),
    ("censure_passed",       ("해임건의안 가결", "해임건의 가결")),
    ("inquiry_launched",     ("국정조사 실시", "특검법 통과", "특별검사 임명")),
]

# 탄핵 문맥이 아예 없으면 판정하지 않는다. "기각"만 보고 형사 각하 기사를 잡으면 안 된다
IMPEACHMENT_CONTEXT = ("탄핵", "해임건의", "국정조사", "특검법", "특별검사")


def classify(title: str) -> str | None:
    """**제목만** 받는다. 요약을 넣으면 배경 언급에 반응해 오분류가 난다."""
    text = title or ""
    if not any(k in text for k in IMPEACHMENT_CONTEXT):
        return None
    for stage, keywords in RULES:
        if any(k in text for k in keywords):
            return stage
    return None


def run(apply: bool) -> int:
    client = get_client()
    try:
        client.table("issues").select("institutional_stage").limit(1).execute()
    except Exception as e:
        print("컬럼이 없습니다 — supabase/028-institutional-stage.sql 을 먼저 적용하세요")
        print(f"  ({e})")
        return 0
    changed = 0

    # 1) 기사 — 제목으로 판정
    issues = client.table("issues").select("id, title, event_id, institutional_stage").execute().data
    print(f"\n[issues] {len(issues)}건 검사")
    resolved: dict[str, str] = {}
    for r in issues:
        stage = r.get("institutional_stage") or classify(r.get("title"))
        if not stage:
            continue
        resolved[r["id"]] = stage
        if r.get("institutional_stage"):
            continue
        if apply:
            client.table("issues").update({"institutional_stage": stage}).eq("id", r["id"]).execute()
        print(f"  [{'ok' if apply else 'dry'}] {stage:22s} {(r.get('title') or '')[:56]}")
        changed += 1

    # 2) 클러스터 — 소속 기사에서 물려받는다 (event_manager 와 같은 규칙)
    links = client.table("cluster_issues").select("cluster_id, issue_id").execute().data
    by_cluster: dict[str, list[str]] = {}
    for l in links:
        by_cluster.setdefault(l["cluster_id"], []).append(l["issue_id"])
    for r in issues:
        if r.get("event_id"):
            by_cluster.setdefault(r["event_id"], []).append(r["id"])

    clusters = client.table("issue_clusters").select("id, summary, institutional_stage").execute().data
    print(f"\n[issue_clusters] {len(clusters)}건 검사")
    for c in clusters:
        if c.get("institutional_stage"):
            continue
        stages = [resolved[i] for i in set(by_cluster.get(c["id"], [])) if i in resolved]
        stage = _best_institutional_stage(stages)
        if not stage:
            continue
        if apply:
            client.table("issue_clusters").update({"institutional_stage": stage}).eq("id", c["id"]).execute()
        print(f"  [{'ok' if apply else 'dry'}] {stage:22s} {(c.get('summary') or '')[:56]}")
        changed += 1

    print(f"\n{'채움' if apply else '채울 예정'} {changed}건")
    if not apply:
        print("실제로 기록하려면: python backfill_institutional_stage.py --apply")
    return 0


if __name__ == "__main__":
    sys.exit(run(apply="--apply" in sys.argv))
