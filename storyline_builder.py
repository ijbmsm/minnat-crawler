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
    from storyline_shape import parse_date
    labels = {
        "investigation": "수사 중", "indicted": "기소", "suspended_indictment": "기소유예",
        "guilty_1st": "1심 유죄", "guilty_2nd": "2심 유죄", "confirmed": "대법 확정",
        "not_guilty": "무죄", "no_charges": "혐의없음", "dismissed": "각하", "pardoned": "특별사면",
    }
    dated = sorted((e for e in events if parse_date(e.get("first_reported_at"))),
                   key=lambda e: parse_date(e["first_reported_at"]))
    for e in reversed(dated):
        if e.get("criminal_stage") in labels:
            return labels[e["criminal_stage"]]
    return "종결" if status == "closed" else "진행 중"


def _fingerprint(events: list[dict]) -> str:
    """사건 구성 지문. 바뀌지 않았으면 원고를 다시 쓰지 않는다 (LLM 비용)."""
    return hashlib.sha1(",".join(sorted(e["id"] for e in events)).encode()).hexdigest()


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

    existing = {s["slug"]: s for s in client.table("storylines").select("id, slug, authored_at").execute().data} if apply else {}

    made = skipped = failed = 0
    used_slugs: set[str] = set()
    for label, members in groups:
        status, ended = story_status(members)
        chunks = split_chapters(members)
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

        draft = author(chapters, status)
        if not draft:
            failed += 1
            continue

        # slug 는 검색 유입의 주소다. LLM 이 준 로마자를 쓰되 규격 미달이면 해시로 내려간다
        slug = draft.get("slug") or make_slug(label, label)
        while slug in used_slugs:
            slug = make_slug(slug, slug + label)
        used_slugs.add(slug)

        payload = {
            "slug": slug,
            "title": draft["title"],
            "blurb": draft.get("blurb") or "",
            "lead": draft.get("lead") or [],
            "camp": _camp_of(members),
            "status": status,
            "status_label": _status_label(members, status),
            "started_at": _first_date(members),
            "ended_at": ended,
            "outcome": draft.get("outcome"),
            "figures": draft.get("figures") or [],
            "people": draft.get("people") or [],
            "match_keywords": group_keywords(members, events),
            "authored_at": "now()",
        }
        story_id = _upsert_story(client, existing.get(slug), payload)
        _replace_chapters(client, story_id, chapters, draft)
        made += 1
        print(f"  [ok] {slug:28s} {draft['title'][:36]}")

    verb = "만들 예정" if not apply else "생성"
    print(f"\n{verb} {made}개 · 건너뜀 {skipped} · 실패 {failed}")
    if not apply:
        print("실제로 만들려면: python storyline_builder.py --apply")
    return failed


def _first_date(events: list[dict]) -> str | None:
    from storyline_shape import parse_date
    dates = sorted(d for d in (parse_date(e.get("first_reported_at")) for e in events) if d)
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
            issue_id = e.get("representative_issue_id")
            if not issue_id:
                continue
            try:
                client.table("storyline_articles").insert({
                    "storyline_id": story_id,
                    "chapter_id": row["id"],
                    "issue_id": issue_id,
                    "event_id": e["id"],
                    "score": 1.0,
                    "stage": "rule",
                    "reason": f"{ch['when']} 국면",
                }).execute()
            except Exception as err:
                if "duplicate" not in str(err).lower() and "unique" not in str(err).lower():
                    raise


if __name__ == "__main__":
    sys.exit(build(apply="--apply" in sys.argv))
