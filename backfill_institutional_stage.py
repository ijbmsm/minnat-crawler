"""기존 기사·사건에 institutional_stage 를 채운다 (마이그레이션 028 이후 1회).

분류기가 앞으로는 채우지만, 이미 저장된 행은 형사 단계 칸에 탄핵을 억지로 적어 뒀다.
그 결과 "헌재 전원일치 파면"이 근거등급 '혐의'로 표시됐다.

제목 키워드로 판정한다. 제도적 결정은 표현이 정형화돼 있어 규칙으로 충분하고,
LLM 을 쓰면 같은 입력에 매번 다른 답이 나올 수 있다.

사용:
    python backfill_institutional_stage.py            # 드라이런
    python backfill_institutional_stage.py --apply
"""
import sys

from db import get_client

# 순서가 중요하다 — 위에서부터 먼저 맞는 것을 쓴다.
# "탄핵 인용"이 "탄핵소추"보다 앞서야 파면 기사가 소추로 분류되지 않는다.
RULES: list[tuple[str, tuple[str, ...]]] = [
    ("impeachment_upheld",   ("탄핵 인용", "탄핵인용", "인용 파면", "전원일치 파면", "탄핵 인용 파면")),
    ("impeachment_rejected", ("탄핵소추 헌재 기각", "헌재 기각", "탄핵 기각", "탄핵 각하")),
    ("impeachment_passed",   ("탄핵소추안 가결", "탄핵안 가결", "탄핵소추 가결")),
    ("impeachment_proposed", ("탄핵소추", "탄핵안 발의", "탄핵 추진")),
    ("censure_passed",       ("해임건의안 가결", "해임건의 가결")),
    ("inquiry_launched",     ("국정조사 실시", "특검법 통과", "특별검사 임명")),
]


def classify(text: str) -> str | None:
    for stage, keywords in RULES:
        if any(k in (text or "") for k in keywords):
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

    for table, text_cols in (("issues", ("title", "summary")), ("issue_clusters", ("summary",))):
        rows = client.table(table).select("id, " + ", ".join(text_cols) + ", institutional_stage").execute().data
        print(f"\n[{table}] {len(rows)}건 검사")
        for r in rows:
            if r.get("institutional_stage"):
                continue
            text = " ".join(str(r.get(c) or "") for c in text_cols)
            stage = classify(text)
            if not stage:
                continue
            label = (r.get(text_cols[0]) or "")[:56]
            if apply:
                client.table(table).update({"institutional_stage": stage}).eq("id", r["id"]).execute()
                print(f"  [ok]  {stage:22s} {label}")
            else:
                print(f"  [dry] {stage:22s} {label}")
            changed += 1

    print(f"\n{'채움' if apply else '채울 예정'} {changed}건")
    if not apply:
        print("실제로 기록하려면: python backfill_institutional_stage.py --apply")
    return 0


if __name__ == "__main__":
    sys.exit(run(apply="--apply" in sys.argv))
