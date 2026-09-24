"""보관된 원문을 현재 분석기로 다시 본다.

이게 원문을 남기는 이유다. 프롬프트를 고치거나 모델을 바꾸면 `prompt_hash` 가
달라지고, 그 순간 과거 기사가 전부 "아직 이 버전으로 안 본 것" 이 된다.
여기서 다시 돌려 **전후를 비교**한다.

지금까지는 그게 안 됐다. 2026-09-25 에 임베딩 기준을 바꿀지 판단하려고 라이브
수집을 다시 하면서 $1.32 를 썼다 — 원문이 있었으면 조회로 끝났을 일이다.

    python reanalyze.py --dry --limit 30      # 무엇이 달라지는지만 본다 (LLM 호출 O, 저장 X)
    python reanalyze.py --limit 30            # 실제로 다시 분석하고 기록한다
    python reanalyze.py --compare --limit 30  # 기존 판정과 새 판정을 나란히 놓는다

⚠️ `--dry` 도 LLM 을 부른다. 분석해 봐야 달라지는지 알 수 있기 때문이다.
   비용이 드는 건 똑같고, 다른 건 **DB 를 안 건드린다**는 것뿐이다.
"""
import argparse
import sys
from collections import Counter

import raw_store
from analyzer import analyze_article, ANALYZER_VERSION, MODEL, prompt_hash, usage_report, SKIP_STATS
from db import get_client, load_politician_positions
from main import load_politicians_map


def _existing_by_url(urls: list[str]) -> dict[str, dict]:
    """같은 원문으로 이미 만들어진 issue. 비교 대상이다."""
    if not urls:
        return {}
    client = get_client()
    out: dict[str, dict] = {}
    for i in range(0, len(urls), 50):
        rows = (client.table("issues")
                .select("id, source_url, category, camp, actor_name, criminal_stage, "
                        "institutional_stage, analyzer_version, prompt_hash")
                .in_("source_url", urls[i:i + 50]).execute().data)
        for r in rows:
            out[r["source_url"]] = r
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--dry", action="store_true", help="분석은 하되 DB 를 안 건드린다")
    ap.add_argument("--compare", action="store_true", help="기존 판정과 나란히 출력")
    args = ap.parse_args()

    ph = prompt_hash()
    print(f"분석기 {ANALYZER_VERSION} · 모델 {MODEL} · 프롬프트 {ph}")

    rows = raw_store.pending(ANALYZER_VERSION, limit=args.limit)
    if not rows:
        print("다시 볼 원문이 없다 — 전부 현재 버전으로 분석됐거나 raw_articles 가 비어 있다")
        return 0
    print(f"대상 {len(rows)}건\n")

    politicians_map = load_politicians_map()
    politicians_positions = load_politician_positions()
    existing = _existing_by_url([r["source_url"] for r in rows]) if args.compare else {}

    changed = Counter()
    same = 0
    failed = 0

    for i, r in enumerate(rows, 1):
        analysis = analyze_article(
            r["title"], r.get("content") or "", r.get("source_name") or "",
            politicians_map, r.get("published_at"), politicians_positions,
        )
        if not analysis:
            failed += 1
            if not args.dry:
                raw_store.mark_analyzed(r["id"], ANALYZER_VERSION, ph, MODEL,
                                        skip_reason="재분석에서도 버려짐")
            print(f"  {i:3d}. [버림] {r['title'][:46]}")
            continue

        if not args.dry:
            raw_store.mark_analyzed(r["id"], ANALYZER_VERSION, ph, MODEL)

        old = existing.get(r["source_url"])
        if old:
            diffs = []
            for field in ("category", "camp", "actor_name", "criminal_stage", "institutional_stage"):
                a, b = old.get(field), analysis.get(field)
                if (a or None) != (b or None):
                    diffs.append(f"{field}: {a} → {b}")
                    changed[field] += 1
            if diffs:
                print(f"  {i:3d}. {r['title'][:44]}")
                for d in diffs:
                    print(f"        {d}")
            else:
                same += 1
        else:
            print(f"  {i:3d}. [신규] {analysis.get('category')} · {analysis.get('actor_name')} "
                  f"· {r['title'][:36]}")

    print(f"\n{'='*60}")
    print(usage_report())
    if args.compare:
        print(f"기존과 동일 {same}건 · 달라짐 {sum(changed.values())}건 · 분석 실패 {failed}건")
        for field, n in changed.most_common():
            print(f"   {field}: {n}건")
    else:
        print(f"분석 실패 {failed}건")
    if SKIP_STATS:
        print("버려진 이유:", dict(SKIP_STATS))
    if args.dry:
        print("\n--dry 였다. DB 는 안 건드렸다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
