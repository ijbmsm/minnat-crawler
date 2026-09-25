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

**group_key 가 있는 행을 남긴다.** 그게 다음 실행이 찾아내는 행이기 때문이다.
group_key 없는 행을 남기면 다음 실행이 못 찾고 또 새로 만든다.

같은 조건이면 장(chapter)이 많은 것 → 먼저 만들어진 것 순이다.

## 주소는 옮겨 붙인다

030 적용 전에 만들어진 행에는 읽을 수 있는 slug 가 있고(kim-keon-hee-…),
그 뒤에 만들어진 행에는 해시 slug 가 있다(story-41ac9ab4). 살아남는 쪽이
해시인데 묶음 안에 읽을 수 있는 것이 있으면 **그 주소를 가져온다.**
검색 유입이 붙어 있는 쪽이 옛 주소이기 때문이다.

    python merge_duplicate_storylines.py            # 무엇을 지울지만 보여준다
    python merge_duplicate_storylines.py --apply    # 실제로 지운다
"""
import argparse
import sys
from collections import defaultdict

from db import get_client


def _looks_hashed(slug: str) -> str:
    """make_slug 가 만든 해시 꼬리인가. `2000-5aee6065` 처럼 앞이 숫자인 것도 잡는다."""
    tail = slug.rsplit("-", 1)[-1]
    return len(tail) == 8 and all(c in "0123456789abcdef" for c in tail)


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
        "id, slug, title, created_at, group_key").order("created_at").execute().data
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
        # ① group_key 가 있는 행 우선 — 다음 실행이 찾아내는 건 그것뿐이다
        # ② 장이 많은 것 ③ 먼저 만들어진 것
        members.sort(key=lambda r: (
            0 if r.get("group_key") else 1,
            -chapter_count.get(r["id"], 0),
            r["created_at"],
        ))
        keep, drop = members[0], members[1:]
        total_removed += len(drop)

        # 주소는 읽을 수 있는 쪽을 쓴다. 살아남는 행이 해시 slug 면 옮겨 붙인다.
        readable = next((m["slug"] for m in members
                         if not m["slug"].startswith("story-")
                         and not _looks_hashed(m["slug"])), None)
        new_slug = None
        if readable and (keep["slug"].startswith("story-") or _looks_hashed(keep["slug"])):
            new_slug = readable

        print(f"\n[{len(members)}벌] {keep['title'][:44]}")
        mark = "gk" if keep.get("group_key") else "--"
        print(f"  남김 [{mark}] {keep['slug'][:36]:36s} 장 {chapter_count.get(keep['id'], 0)}개  {keep['created_at'][:10]}")
        if new_slug:
            print(f"       주소 이관: {keep['slug']} → {new_slug}")
        for d in drop:
            m2 = "gk" if d.get("group_key") else "--"
            print(f"  지움 [{m2}] {d['slug'][:36]:36s} 장 {chapter_count.get(d['id'], 0)}개  {d['created_at'][:10]}")

        if args.apply:
            ids = [d["id"] for d in drop]
            # 장을 먼저 지운다. 외래키가 없으면 고아 장이 남는다.
            client.table("storyline_chapters").delete().in_("storyline_id", ids).execute()
            client.table("storylines").delete().in_("id", ids).execute()
            # 주소 이관은 옛 행을 지운 **뒤에** 한다 (slug 충돌 방지)
            if new_slug:
                client.table("storylines").update({"slug": new_slug}).eq("id", keep["id"]).execute()

    print(f"\n{'='*60}")
    print(f"묶음 {len(dupes)}개 · 지울 사안 {total_removed}건 · 남는 사안 {len(rows) - total_removed}건")
    if not args.apply:
        print("\n--apply 를 붙이면 실제로 지운다. 지우기 전에 위 목록을 확인할 것.")
    else:
        print("\n지웠다. 다음 크롤러 실행에서 group_key 가 채워진다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
