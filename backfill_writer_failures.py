"""집필에 실패해 원문 제목이 그대로 저장된 행을 다시 쓴다.

2026-09-25: ThinkingBlock 버그로 집필 28건 중 7건이 실패했고, 그 기사들은
언론사 제목이 그대로 title 에 들어갔다 — AI 헤드라인을 쓰는 이유(저작권)를
정면으로 뚫은 것이다. 버그는 고쳤고, 이 스크립트는 이미 들어간 행을 교정한다.

    python backfill_writer_failures.py          # 대상만 보여준다
    python backfill_writer_failures.py --apply  # 다시 쓰고 갱신한다
"""
import argparse
import sys

import writer
from db import get_client


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    client = get_client()
    rows = client.table("issues").select(
        "id, title, summary, category, actor_name, criminal_stage, "
        "institutional_stage, ai_analysis, source_url"
    ).execute().data

    targets = [r for r in rows
               if isinstance(r.get("ai_analysis"), dict)
               and r["ai_analysis"].get("writer_failed")]
    print(f"집필 실패로 원문 제목이 저장된 행: {len(targets)}건")
    if not targets:
        return 0

    # 원문은 raw_articles 에 있다. 없으면 저장된 summary 로 대신한다.
    raws = {}
    urls = [r["source_url"] for r in targets if r.get("source_url")]
    for i in range(0, len(urls), 50):
        got = (client.table("raw_articles")
               .select("source_url, title, content")
               .in_("source_url", urls[i:i + 50]).execute().data)
        for g in got:
            raws[g["source_url"]] = g

    fixed = failed = 0
    for r in targets:
        raw = raws.get(r.get("source_url") or "")
        title = (raw or {}).get("title") or r["title"]
        content = (raw or {}).get("content") or r.get("summary") or ""
        if len(content) < 40:
            print(f"  [건너뜀] 본문이 없다 — {r['title'][:44]}")
            failed += 1
            continue

        draft = writer.write(title, content, {
            "actor_name": r.get("actor_name", ""),
            "category": r.get("category", ""),
            "criminal_stage": r.get("criminal_stage"),
            "institutional_stage": r.get("institutional_stage"),
            "evidence_sentence": r["ai_analysis"].get("evidence_sentence", ""),
        })
        if not draft:
            print(f"  [실패] {r['title'][:44]}")
            failed += 1
            continue

        print(f"  {r['title'][:40]}\n      → {draft['headline']}")
        fixed += 1
        if args.apply:
            ai = dict(r["ai_analysis"])
            ai["writer_failed"] = False
            ai["backfilled_at"] = "2026-09-25"
            client.table("issues").update({
                "title": draft["headline"],
                "summary": draft.get("summary") or r.get("summary"),
                "ai_analysis": ai,
            }).eq("id", r["id"]).execute()

    print(f"\n다시 쓴 것 {fixed}건 · 실패 {failed}건")
    print(writer.usage_report())
    if not args.apply:
        print("\n--apply 를 붙이면 실제로 갱신한다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
