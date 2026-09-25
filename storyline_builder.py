"""사안을 만들고 갱신한다 — 사람 손 없이.

  storyline_discover  어떤 사건이 한 사안인가   (규칙)
  storyline_shape     장을 어디서 자르나, 등급  (규칙)
  storyline_author    제목·본문·요지            (LLM)
  storyline_builder   ← 위를 엮어 DB 에 쓴다

사안은 매번 통째로 다시 만든다. 사건이 늘면 장 경계가 바뀌므로 증분 갱신이 오히려
어긋나기 쉽다. 사안 수가 수십 개 규모라 다시 쓰는 편이 싸고 정확하다.
원고를 다시 쓰는 건 사건 구성이 바뀌었을 때뿐이다(LLM 비용).

사용:
    python storyline_builder.py            # 드라이런
    python storyline_builder.py --apply
"""
import hashlib
import re
import sys
import unicodedata

from db import get_client, get_politicians
from storyline_author import author
from storyline_discover import group_events, group_keywords
from storyline_shape import (
    chapter_grade, event_grade, split_chapters, story_status, when_label,
)

# 사안 slug 후보를 만들 때 쓰는 로마자 표기 (한글 slug 는 URL 에서 깨진다)
SLUG_FALLBACK = "story"


def make_slug(label: str, seed: str) -> str:
    """URL 에 쓸 slug. 한글은 안전한 해시로 대체한다."""
    ascii_part = re.sub(r"[^a-z0-9]+", "-", unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode().lower()).strip("-")
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8]
    return f"{ascii_part or SLUG_FALLBACK}-{digest}"


def _camp_of(events: list[dict]) -> str:
    camps = {e.get("camp") for e in events if e.get("camp")}
    if camps == {"blue"}:
        return "blue"
    if camps == {"red"}:
        return "red"
    return "both"


def _status_label(events: list[dict], status: str) -> str:
    """상태 한 줄. 최신 형사 단계를 그대로 옮긴다 — 해석을 넣지 않는다."""
    from storyline_shape import event_date, parse_date
    labels = {
        "investigation": "수사 중", "indicted": "기소", "suspended_indictment": "기소유예",
        "guilty_1st": "1심 유죄", "guilty_2nd": "2심 유죄", "confirmed": "대법 확정",
        "not_guilty": "무죄", "no_charges": "혐의없음", "dismissed": "각하", "pardoned": "특별사면",
    }
    dated = sorted((e for e in events if parse_date(event_date(e))),
                   key=lambda e: parse_date(event_date(e)))
    for e in reversed(dated):
        if e.get("criminal_stage") in labels:
            return labels[e["criminal_stage"]]
    return "종결" if status == "closed" else "진행 중"


def _fingerprint(events: list[dict]) -> str:
    """사건 구성 지문. 바뀌지 않았으면 원고를 다시 쓰지 않는다 (LLM 비용)."""
    return hashlib.sha1(",".join(sorted(e["id"] for e in events)).encode()).hexdigest()


def group_key(label: str) -> str:
    """사안의 **신원**. label 에서 결정론적으로 나오고 바뀌지 않는다.

    slug 와 역할이 다르다. slug 는 사람이 보는 주소라 읽을 수 있어야 하고,
    group_key 는 "같은 사안인가" 판정용이라 안정적이기만 하면 된다.
    예전에는 slug 하나가 두 역할을 겸했는데, 그 값을 LLM 이 매번 새로 줘서
    판정이 실행마다 흔들렸다.
    """
    return hashlib.sha1(label.encode("utf-8")).hexdigest()[:16]


def _load_existing(client) -> tuple[dict, bool]:
    """기존 사안을 group_key 로 인덱싱한다.

    030 미적용 환경에서도 돌아야 한다. 그때는 slug 를 신원으로 쓰되
    **결정론적 slug**(label 기반)로만 찾는다 — 중복 방지는 작동하고
    지문 비교와 읽을 수 있는 slug 만 못 쓴다.
    """
    try:
        rows = client.table("storylines").select(
            "id, slug, authored_at, fingerprint, group_key").execute().data
        # group_key 가 아직 안 채워진 기존 행은 slug 로 남겨둔다 (한 번 돌면 채워진다)
        idx = {}
        for r in rows:
            idx[r.get("group_key") or f"slug:{r['slug']}"] = r
        idx["__slugs__"] = {r["slug"] for r in rows}
        return idx, True
    except Exception:
        rows = client.table("storylines").select("id, slug, authored_at").execute().data
        print("  [storyline] group_key·fingerprint 컬럼 없음 — 원고를 매번 다시 쓴다 "
              "(supabase/030-storyline-fingerprint.sql 적용 시 꺼진다)")
        idx = {f"slug:{r['slug']}": r for r in rows}
        idx["__slugs__"] = {r["slug"] for r in rows}
        return idx, False


def _tables_ready(client) -> bool:
    """027 미적용 환경을 먼저 걸러낸다.

    확인 없이 진행하면 LLM 을 부른 **뒤에** 저장에서 터져 호출 비용만 버린다.
    """
    try:
        client.table("storylines").select("id").limit(1).execute()
        return True
    except Exception as e:
        print(f"  [storyline] 저장소가 없습니다 — supabase/027-storylines-auto.sql 을 먼저 적용하세요")
        print(f"              ({e})")
        return False


def build(apply: bool) -> int:
    client = get_client()
    if apply and not _tables_ready(client):
        return 0

    events = [
        e for e in client.table("issue_clusters")
        .select("id, actor_name, category, camp, summary, criminal_stage, source_tier, "
                "verified, first_reported_at, representative_issue_id, is_active")
        .execute().data
        if e.get("summary")
    ]
    people_names = {p["name"] for p in get_politicians()}
    groups = group_events(events, people_names)
    print(f"사건 {len(events)}건 → 사안 후보 {len(groups)}개")

    # slug 로 찾는다. 단 그 slug 는 **LLM 이 준 것이 아니라** label 에서 결정론적으로
    # 만든 값이다 (아래 참조). 예전에는 LLM 이 매번 새 로마자를 줘서 조회가 빗나갔고,
    # 빗나가면 UPDATE 가 아니라 INSERT 가 돼 같은 사안이 여러 벌 생겼다.
    # 실측(2026-09-25): 사안 106건 중 김건희 8벌·조국 6벌·박근혜 6벌.
    existing, _has_fp = _load_existing(client) if apply else ({}, False)

    db_slugs: set[str] = existing.pop("__slugs__", set()) if apply else set()

    made = skipped = failed = 0
    used_slugs: set[str] = set()
    for label, members in groups:
        # 장은 **기사 단위**로 자른다.
        # 클러스터 단위로 자르면 first_reported_at(최초 보도일) 하나로 눌려
        # 17개월짜리 사안이 한 국면이 된다 — 실제로 비상계엄이 그랬다.
        articles = _articles_of(client, members)
        basis = articles or members
        status, ended = story_status(basis)
        chunks = split_chapters(basis)
        if not chunks:
            continue
        chapters = [
            {"position": i, "when": when_label(ch), "grade": chapter_grade(ch), "events": ch}
            for i, ch in enumerate(chunks, 1)
        ]

        if not apply:
            print(f"  [dry] [{label}] 사건 {len(members):2d}건 · 장 {len(chapters)}개 · {status}")
            made += 1
            continue

        # 신원은 group_key 다. slug 로 찾으면 안 된다 — LLM 이 준 로마자가
        # 실행마다 흔들려(lee-jae-myung- 과 leejaemyung- 이 공존했다)
        # 기존 사안을 못 찾고 새로 만든다. 사안 106건 중 김건희 8벌·조국 6벌.
        gkey = group_key(label)
        prior = existing.get(gkey) or existing.get(f"slug:{make_slug(label, label)}")
        fp = _fingerprint(members)

        # 구성이 그대로면 원고를 다시 쓰지 않는다.
        # 예전에는 이 비교가 아예 없어서 3시간마다 사안 전부를 다시 썼다
        # (실측: 사안 후보 25개 × 하루 8회 = 200회). 비용도 비용이지만 제목과
        # 요약이 매번 바뀌어 같은 사안이 읽을 때마다 다른 글이었다.
        if prior and _has_fp and prior.get("fingerprint") == fp:
            skipped += 1
            continue

        draft = author(chapters, status)
        if not draft:
            failed += 1
            continue

        # slug 는 검색 유입의 주소다. **한 번 정하면 안 바꾼다.**
        # 기존 사안이면 그 주소를 그대로 쓴다 — 바꾸면 들어오던 링크가 죽는다.
        if prior:
            slug = prior["slug"]
        elif _has_fp:
            # group_key 로 신원을 잡으므로 slug 는 읽을 수 있는 쪽을 쓴다
            slug = draft.get("slug") or make_slug(label, label)
        else:
            # 030 미적용 — 신원이 slug 뿐이다. LLM 이 준 로마자를 쓰면 다음 실행에서
            # 못 찾아 또 INSERT 가 된다(그게 중복 106건의 원인이었다).
            # 주소 가독성을 포기하고 결정론을 택한다. 030 을 적용하면 읽을 수 있는
            # slug 로 돌아온다.
            slug = make_slug(label, label)

        # 충돌은 두 곳에서 난다 — 이번 실행 안(used_slugs)과 **DB 에 이미 있는 것**.
        # 뒤엣것을 안 보면 storylines_slug_key 유일 인덱스에 걸려 INSERT 가 터진다
        # (2026-09-25 실측: yoon-martial-law 가 이미 있어 사안 재구성이 중단됐다).
        taken = used_slugs | {x for x in db_slugs if not prior or x != prior["slug"]}
        if slug in taken:
            for n in range(1, 20):
                candidate = make_slug(label, f"{label}#{n}")
                if candidate not in taken:
                    print(f"  [storyline] slug 충돌 회피: {slug} → {candidate}")
                    slug = candidate
                    break
        used_slugs.add(slug)

        payload = {
            "slug": slug,
            "title": draft["title"],
            "blurb": draft.get("blurb") or "",
            "lead": draft.get("lead") or [],
            "camp": _camp_of(members),
            "status": status,
            "status_label": _status_label(basis, status),
            "started_at": _first_date(basis),
            "ended_at": ended,
            "outcome": draft.get("outcome"),
            "figures": draft.get("figures") or [],
            "people": draft.get("people") or [],
            "match_keywords": group_keywords(members, events),
            "authored_at": "now()",
        }
        if _has_fp:
            payload["fingerprint"] = fp
            payload["group_key"] = gkey
        # 한 사안이 터져도 나머지는 계속 만든다. 예전에는 예외가 build() 밖으로
        # 나가 **그 뒤 사안이 전부 생성되지 않았다** (2026-09-25: slug 충돌 1건에
        # 30개 중 3개만 만들어지고 중단).
        try:
            story_id = _upsert_story(client, prior, payload)
            _replace_chapters(client, story_id, chapters, draft)
        except Exception as e:
            failed += 1
            print(f"  [storyline] 저장 실패 ({slug}): {str(e)[:110]}")
            continue
        db_slugs.add(slug)
        made += 1
        print(f"  [ok] {slug:28s} {draft['title'][:36]}")

    verb = "만들 예정" if not apply else "생성"
    print(f"\n{verb} {made}개 · 건너뜀 {skipped} · 실패 {failed}")
    if not apply:
        print("실제로 만들려면: python storyline_builder.py --apply")
    return failed


def _articles_of(client, clusters: list[dict]) -> list[dict]:
    """사안을 이루는 클러스터에 묶인 기사 전부.

    사안의 시간축은 여기서 나온다. 클러스터는 "같은 사건"의 묶음일 뿐이고,
    국면이 바뀌는 지점(기소 → 탄핵소추 → 파면 → 재판)은 기사 날짜에 있다.
    """
    ids = [c["id"] for c in clusters]
    if not ids:
        return []
    issue_ids: list[str] = []
    for i in range(0, len(ids), 50):
        rows = client.table("cluster_issues").select("issue_id").in_("cluster_id", ids[i:i + 50]).execute().data
        issue_ids += [r["issue_id"] for r in rows]
    # event_id 로만 연결된 기사도 있다
    for i in range(0, len(ids), 50):
        rows = client.table("issues").select("id").in_("event_id", ids[i:i + 50]).execute().data
        issue_ids += [r["id"] for r in rows]

    issue_ids = list(dict.fromkeys(issue_ids))
    out: list[dict] = []
    for i in range(0, len(issue_ids), 50):
        out += client.table("issues").select(
            "id, title, summary, published_at, category, criminal_stage, "
            # 등급 판정에 institutional_stage 가 쓰인다. 빠뜨리면 헌재 파면이
            # '혐의'로 표시된다 — 실제로 그랬다
            "institutional_stage, source_tier, verified, actor_name, camp, event_id"
        ).in_("id", issue_ids[i:i + 50]).execute().data
    return out


def _first_date(events: list[dict]) -> str | None:
    from storyline_shape import event_date, parse_date
    dates = sorted(d for d in (parse_date(event_date(e)) for e in events) if d)
    return dates[0].date().isoformat() if dates else None


def _upsert_story(client, existing: dict | None, payload: dict) -> str:
    payload = {k: v for k, v in payload.items() if k != "authored_at"}
    if existing:
        client.table("storylines").update(payload).eq("id", existing["id"]).execute()
        return existing["id"]
    row = client.table("storylines").insert(payload).execute().data[0]
    return row["id"]


def _replace_chapters(client, story_id: str, chapters: list[dict], draft: dict) -> None:
    """장과 기사를 통째로 다시 깐다. 장 경계가 바뀌면 부분 갱신이 더 위험하다."""
    client.table("storyline_chapters").delete().eq("storyline_id", story_id).execute()
    by_pos = {c.get("position"): c for c in draft.get("chapters", [])}

    for ch in chapters:
        written = by_pos.get(ch["position"], {})
        is_last = ch["position"] == len(chapters)
        row = client.table("storyline_chapters").insert({
            "storyline_id": story_id,
            "position": ch["position"],
            "when_label": ch["when"],
            "title": written.get("title") or f"{ch['when']} 국면",
            "body": written.get("body") or "",
            # 마지막 장에는 다음으로 잇는 줄을 두지 않는다
            "link": "" if is_last else (written.get("link") or ""),
            "link_is_causal": bool(written.get("causal")) and not is_last,
            "grade": ch["grade"],
        }).execute().data[0]

        for e in ch["events"]:
            # 기사 단위로 잘랐으므로 e 는 issue 다. 클러스터로 떨어진 경우만 대표 기사를 쓴다
            issue_id = e.get("id") if "published_at" in e else e.get("representative_issue_id")
            if not issue_id:
                continue
            try:
                client.table("storyline_articles").insert({
                    "storyline_id": story_id,
                    "chapter_id": row["id"],
                    "issue_id": issue_id,
                    "event_id": e.get("event_id") or e.get("id"),
                    "score": 1.0,
                    "stage": "rule",
                    "reason": f"{ch['when']} 국면",
                }).execute()
            except Exception as err:
                if "duplicate" not in str(err).lower() and "unique" not in str(err).lower():
                    raise


if __name__ == "__main__":
    sys.exit(build(apply="--apply" in sys.argv))
