"""같은 사안이 여러 벌로 갈린 것을 하나로 합친다.

## 왜 생겼나

slug 를 LLM 이 매번 새로 줬고, 그 slug 로 기존 사안을 찾았다. 로마자 표기가
흔들리면(lee-jae-myung- 과 leejaemyung- 이 공존) 조회가 빗나가고, 빗나가면
UPDATE 가 아니라 INSERT 가 됐다. 3시간마다 사안 전부를 다시 쓰고 있었으니
표기가 흔들릴 기회도 하루 8번이었다.

2026-09-25 실측: storylines 106건 안에 김건희 8벌 · 조국 6벌 · 박근혜 6벌 ·
의대정원 5벌 · 이재명 성남FC 5벌.

원인은 코드에서 고쳤다(group_key 로 신원을 잡고 slug 는 한 번만 정한다).
이 스크립트는 이미 쌓인 중복을 정리한다.

## 무엇을 남기나

같은 묶음에서 **장(chapter)이 가장 많은 것**을 남긴다. 장이 많다는 건 그 사안이
가장 완전하게 조립됐다는 뜻이다. 같으면 먼저 만들어진 것(created_at)을 남긴다 —
검색 유입이 붙어 있을 가능성이 높은 쪽이다.

    python merge_duplicate_storylines.py            # 무엇을 지울지만 보여준다
    python merge_duplicate_storylines.py --apply    # 실제로 지운다
"""
import argparse
import sys
from collections import defaultdict

from db import get_client


def _norm(title: str) -> str:
    """제목에서 묶음 키를 만든다.

    LLM 이 쓴 제목이라 표현이 조금씩 다르다("김건희 도이치모터스 주가조작 사건"
    vs "김건희 도이치모터스 주가조작 및 명품백 수수 사건"). 앞부분이 같으면
    같은 사안으로 본다. 공백을 지워 띄어쓰기 차이를 흡수한다.
    """
    return (title or "").replace(" ", "")[:12]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--key-len", type=int, default=12, help="제목 앞 몇 글자로 묶을지")
    args = ap.parse_args()

    client = get_client()
    rows = client.table("storylines").select(
        "id, slug, title, created_at").order("created_at").execute().data
    print(f"사안 {len(rows)}건")

    chapters = client.table("storyline_chapters").select("storyline_id").execute().data
    chapter_count: dict[str, int] = defaultdict(int)
    for c in chapters:
        chapter_count[c["storyline_id"]] += 1

    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[(r.get("title") or "").replace(" ", "")[: args.key_len]].append(r)

    dupes = {k: v for k, v in groups.items() if len(v) > 1}
    if not dupes:
        print("중복 없음")
        return 0

    total_removed = 0
    for key, members in sorted(dupes.items(), key=lambda kv: -len(kv[1])):
        # 장이 가장 많은 것 → 같으면 가장 먼저 만들어진 것
        members.sort(key=lambda r: (-chapter_count.get(r["id"], 0), r["created_at"]))
        keep, drop = members[0], members[1:]
        total_removed += len(drop)

        print(f"\n[{len(members)}벌] {keep['title'][:44]}")
        print(f"  남김  {keep['slug'][:38]:38s} 장 {chapter_count.get(keep['id'], 0)}개  {keep['created_at'][:10]}")
        for d in drop:
            print(f"  지움  {d['slug'][:38]:38s} 장 {chapter_count.get(d['id'], 0)}개  {d['created_at'][:10]}")

        if args.apply:
            ids = [d["id"] for d in drop]
            # 장을 먼저 지운다. 외래키가 없으면 고아 장이 남는다.
            client.table("storyline_chapters").delete().in_("storyline_id", ids).execute()
            client.table("storylines").delete().in_("id", ids).execute()

    print(f"\n{'='*60}")
    print(f"묶음 {len(dupes)}개 · 지울 사안 {total_removed}건 · 남는 사안 {len(rows) - total_removed}건")
    if not args.apply:
        print("\n--apply 를 붙이면 실제로 지운다. 지우기 전에 위 목록을 확인할 것.")
    else:
        print("\n지웠다. 다음 크롤러 실행에서 group_key 가 채워진다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
