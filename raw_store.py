"""수집 원문 보관 — 분석을 다시 돌릴 수 있게 만드는 층.

분석은 원문에서 파생되는 **계산**이다. 원문이 남아 있으면 프롬프트를 고치고,
모델을 바꾸고, 임계값을 조정한 뒤 과거 전체에 다시 돌려 전후를 비교할 수 있다.

원문이 없던 동안에는 그게 안 됐다. 2026-09-25 임베딩 기준을 바꿀지 판단하려고
라이브 수집을 다시 하면서 $1.32 를 썼다 — 원문이 있었으면 조회로 끝났다.

테이블(supabase/029-raw-articles.sql)이 아직 적용되지 않았을 수 있다.
그때는 **죽지 않되 조용하지도 않다**: 경고를 찍고 파이프라인은 계속 간다.
수집·분석이 원문 보관보다 급하기 때문이고, 보관이 멈춘 걸 모르는 게 더 나쁘기 때문이다.
"""
import hashlib

from db import get_client

# 테이블이 없을 때 매 기사마다 같은 경고를 찍지 않는다. 한 번만 알리고 센다.
_MISSING = {"warned": False, "count": 0}


def content_hash(title: str, content: str) -> str:
    """같은 URL 이 내용만 바뀌어 다시 들어오는 경우를 가른다(기사 수정·종합 기사)."""
    return hashlib.sha256(f"{title}\n{content}".encode("utf-8")).hexdigest()[:32]


def _is_missing_table(err: Exception) -> bool:
    """raw_articles 가 아직 없어서 난 오류인가.

    PostgREST 는 없는 테이블에 PGRST205 를 준다. 메시지 문자열도 같이 본다 —
    코드만 보면 클라이언트 버전이 바뀔 때 조용히 빗나간다.
    """
    s = str(err)
    return "PGRST205" in s or "PGRST106" in s or (
        "raw_articles" in s and ("does not exist" in s or "not find the table" in s)
    )


def save_many(articles: list[dict], source_tier: int) -> dict[str, str]:
    """원문을 저장하고 {source_url: raw_article_id} 를 돌려준다.

    분석 **전에** 부른다. 분석에서 버려질 기사도 원문은 남아야 한다 —
    지금은 "진영 판정 불가 74건" 이 숫자로만 남고 무엇이었는지 사라진다.

    실패해도 예외를 올리지 않는다. 호출부는 빈 dict 를 받고 그대로 진행한다.
    """
    if not articles:
        return {}

    rows = []
    for a in articles:
        url = a.get("source_url") or a.get("detail_link") or ""
        title = a.get("title") or ""
        if not url or not title:
            continue
        content = a.get("summary") or a.get("content") or ""
        rows.append({
            "source_url": url,
            "title": title,
            "content": content,
            "source_name": a.get("source") or "",
            "source_tier": source_tier,
            "published_at": a.get("published_at"),
            "content_hash": content_hash(title, content),
        })

    if not rows:
        return {}

    try:
        client = get_client()
        # (source_url, content_hash) 가 유일하다. 같은 기사가 두 번 와도 한 행이다.
        result = (
            client.table("raw_articles")
            .upsert(rows, on_conflict="source_url,content_hash")
            .execute()
        )
        return {r["source_url"]: r["id"] for r in (result.data or [])}
    except Exception as e:
        if _is_missing_table(e):
            _MISSING["count"] += len(rows)
            if not _MISSING["warned"]:
                _MISSING["warned"] = True
                print("  [raw] ⚠ raw_articles 테이블이 없다 — 원문 보관이 꺼진 채로 돈다")
                print("        supabase/029-raw-articles.sql 을 적용하면 켜진다")
        else:
            print(f"  [raw] 원문 저장 실패: {e}")
        return {}


def mark_analyzed(
    raw_id: str | None,
    analyzer_version: str,
    prompt_hash: str,
    model: str,
    skip_reason: str | None = None,
) -> None:
    """이 원문을 무엇으로 봤는지 기록한다.

    `skip_reason` 이 있으면 분석은 했는데 저장까지 못 간 것이다. 그 사유가 남아야
    "정치인 DB 에 없어서 버린 기사가 누구였나" 를 나중에 되짚을 수 있다.
    """
    if not raw_id:
        return
    try:
        get_client().table("raw_articles").update({
            "analyzed_at": "now()",
            "analyzer_version": analyzer_version,
            "prompt_hash": prompt_hash,
            "model": model,
            "skip_reason": skip_reason,
        }).eq("id", raw_id).execute()
    except Exception as e:
        if not _is_missing_table(e):
            print(f"  [raw] 분석 기록 실패: {e}")


def pending(analyzer_version: str, limit: int = 200) -> list[dict]:
    """현재 분석기 버전으로 아직 안 본 원문.

    프롬프트나 모델을 바꾸면 analyzer_version 이 달라지고, 그 순간 과거 기사가
    전부 여기로 다시 올라온다. 재분석 배치가 이걸 먹는다.
    """
    try:
        result = (
            get_client().table("raw_articles")
            .select("id, source_url, title, content, source_name, source_tier, published_at")
            .or_(f"analyzer_version.is.null,analyzer_version.neq.{analyzer_version}")
            .order("collected_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []
    except Exception as e:
        if _is_missing_table(e):
            return []
        print(f"  [raw] 재분석 대상 조회 실패: {e}")
        return []


def report() -> str | None:
    """실행 끝에 한 줄. 보관이 꺼져 있었으면 그 사실을 남긴다."""
    if _MISSING["count"]:
        return (f"[raw] ⚠ 원문 {_MISSING['count']}건을 보관하지 못했다 "
                f"— supabase/029-raw-articles.sql 미적용")
    return None
