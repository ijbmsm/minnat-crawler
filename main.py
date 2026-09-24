"""민낯 크롤러 v2.0 — Event 기반 파이프라인

파이프라인:
0. 정치인 DB 동기화
1. 다중 소스 동시 수집
2. AI 분류 + Embedding 생성
3. Event 매칭 (4단계: 룰→임베딩→LLM)
4. 신뢰도 게이트
5. DB 저장 + Event 생성/머지
6. 미검증 Event 자동 승격
7. 비활성화 + 스냅샷 생성
"""
import sys
from collections import Counter
from datetime import datetime

from crawlers.assembly import fetch_recent_bills
from crawlers.factcheck import fetch_factchecks, ENABLED as FACTCHECK_ENABLED
from crawlers.news import fetch_all_news, balanced_sample
from crawlers.naver_news import fetch_all_political_news
from crawlers.court import fetch_recent_rulings
from analyzer import (analyze_article, SKIP_STATS, usage_report,
                      ANALYZER_VERSION, MODEL, prompt_hash, LAST_SKIP)
import raw_store
import writer
from trust_gate import evaluate_trust
from event_matcher import get_embedding, match_to_event, EMBEDDING_FAILURES
from storyline_builder import build as build_storylines
from event_manager import create_event, merge_into_event, deactivate_old_events
from expression_filter import filter_expression, needs_unconfirmed_label
from actor_resolver import mentions_known_politician
from validator import validate_issue
from scorer import calculate_score, generate_daily_snapshot
from auto_verify import run_auto_verify
from sync_politicians import sync_to_db as sync_politicians
from db import (
    insert_issue, get_client, get_active_events, get_recent_source_urls,
    load_politician_positions,
)
from config import SCORED_CATEGORIES, POSITION_WEIGHT

# 프롬프트 지문. 한 실행 안에서는 안 바뀌므로 한 번만 계산한다.
_PROMPT_HASH = prompt_hash()


def _last_skip_reason() -> str | None:
    """방금 기사를 왜 버렸는지. 원문 행에 붙여 나중에 되짚는다."""
    return LAST_SKIP.get("reason")


# ── 실행당 LLM 호출 예산 ──
# 기사 1건 = LLM 1회. 예산을 명시해 비용이 소스 개수에 끌려다니지 않게 한다.
# URL 사전 중복 제거 덕에, 두 번째 실행부터는 실제 호출이 이보다 훨씬 적다.
RSS_BUDGET = 60      # 매체 10곳 × 6건 균등
NAVER_BUDGET = 30
BILL_BUDGET = 20     # 법안은 전부 archive(점수 없음)라 비용 대비 가치가 낮다
FACTCHECK_BUDGET = 20


def load_politicians_map() -> dict[str, str]:
    client = get_client()
    result = (
        client.table("politicians")
        .select("name, party:parties(camp)")
        .eq("active", True)
        .execute()
    )
    mapping: dict[str, str] = {}
    for row in result.data:
        name = row.get("name", "")
        party = row.get("party")
        if name and party and isinstance(party, dict):
            mapping[name] = party.get("camp", "")
    return mapping


def process_article(
    article: dict,
    source_tier: int,
    politicians_map: dict[str, str],
    politicians_positions: dict[str, str],
    active_events: list[dict],
    all_articles: list[dict],
    raw_id: str | None = None,
) -> str:
    title = article.get("title", "")
    content = article.get("summary", article.get("content", ""))
    source = article.get("source", "")

    if not title:
        return "skip_empty"

    # ── 정치인 게이트 — LLM 호출 전 ──
    # 정치인 DB 에 없는 사람의 기사는 analyzer.resolve_camp 가 어차피 버린다.
    # 이 게이트는 결과를 바꾸지 않고 **버리는 시점만 앞으로 당긴다.**
    # 실측(2026-09-25): 이 사유로 50건 중 28건, 130건 중 58건을 분석 후에 버렸다.
    if not mentions_known_politician(f"{title} {content}", politicians_map, politicians_positions):
        raw_store.mark_analyzed(raw_id, ANALYZER_VERSION, _PROMPT_HASH, MODEL,
                                skip_reason="게이트: 등재 정치인 미언급")
        return "skip_gate"

    # ── AI 분석 ──
    analysis = analyze_article(
        title, content, source, politicians_map,
        article.get("published_at"), politicians_positions,
    )
    if not analysis:
        # 왜 버렸는지 원문에 붙여둔다. "진영 판정 불가 74건" 이 숫자로만 남으면
        # 누가 빠졌는지 알 길이 없어 정치인 DB 를 넓힐 근거도 못 만든다.
        raw_store.mark_analyzed(raw_id, ANALYZER_VERSION, _PROMPT_HASH, MODEL,
                                skip_reason=_last_skip_reason())
        return "skip_analysis"

    # ── 2차 검증 — 집필 **전에** ──
    # 순서가 중요하다. 집필은 Sonnet 이고 출력이 길어 이 파이프라인에서 제일 비싸다.
    # 검증을 뒤에 두면 떨어질 기사의 글까지 쓰게 된다(실측 10.1%).
    #
    # 떨어진 기사는 저장하지 않는다. 원문(raw_articles)에 사유가 남으므로
    # 프롬프트를 고친 뒤 reanalyze 로 언제든 살릴 수 있다.
    # 저장해두는 쪽도 고려했지만, 집필을 안 하면 원문 제목을 그대로 써야 하고
    # 그건 AI 헤드라인을 쓰는 이유(저작권)와 충돌한다.
    verdict = validate_issue(analysis, article, politicians_map)
    if verdict.action == "queue_review":
        print(f"  [validator] 저장 안 함 — {', '.join(verdict.errors)[:70]}")
        raw_store.mark_analyzed(raw_id, ANALYZER_VERSION, _PROMPT_HASH, MODEL,
                                skip_reason=f"검증 실패: {'; '.join(verdict.errors)[:180]}")
        return "skip_validation"

    # ── 집필 — 판정을 통과한 기사만 ──
    # 예전에는 한 번의 호출이 분류·근거·헤드라인·요약을 다 만들었다. 그런데
    # 분석한 기사의 절반 가까이가 판정 단계에서 버려진다. 버려질 기사의
    # 헤드라인과 요약까지 생성 비용을 내고 있었다.
    #
    # 집필에 실패해도 기사를 버리지 않는다. 판정은 이미 끝났고 그 결과가
    # 점수와 사건 매칭에 쓰인다. 문장만 원문으로 대체한다.
    draft = writer.write(title, content, analysis)
    if draft:
        summary = draft.get("summary") or content[:300]
        analysis["headline"] = draft.get("headline") or title[:60]
        analysis["next_branch"] = draft.get("next_branch")
        if draft.get("expression_changes"):
            analysis["expression_changes"] = draft["expression_changes"]
    else:
        # 문장을 못 썼다. 원문으로 대체하고 그 사실을 남긴다 —
        # 조용히 원문 제목이 헤드라인이 되면 품질 저하를 아무도 모른다.
        summary = content[:300]
        analysis["headline"] = title[:60]
        analysis["next_branch"] = None
        analysis["writer_failed"] = True

    # ── Embedding 생성 ──
    embedding_text = f"{title} {summary}"
    embedding = get_embedding(embedding_text)

    # ── Event 매칭 (4단계) ──
    preliminary = {
        "title": title,
        "summary": summary,
        "camp": analysis["camp"],
        "category": analysis["category"],
        "actor_name": analysis.get("actor_name", ""),
        "published_at": article.get("published_at", datetime.now().isoformat()),
    }
    matched_event = match_to_event(preliminary, active_events, embedding)

    # ── 신뢰도 게이트 ──
    trust_input = {
        **preliminary,
        "source_name": source,
        "source_tier": source_tier,
    }
    trust = evaluate_trust(trust_input, all_articles)

    # ── 직책 가중치 ──
    actor_name = analysis.get("actor_name", "")
    position = politicians_positions.get(actor_name, "의원")
    pos_weight = POSITION_WEIGHT.get(position, 0.8)

    # ── archive 여부 ──
    is_archive = analysis["category"] not in SCORED_CATEGORIES

    # ── DB 저장 ──
    # title은 AI 생성 headline 사용 (저작권 보호), 원본은 ai_analysis에 보관
    ai_title = analysis.get("headline", title[:60])
    issue = {
        "title": ai_title,
        "summary": summary,
        "category": analysis["category"],
        "camp": analysis["camp"],
        "source_tier": source_tier,
        "source_url": article.get("source_url", article.get("detail_link", "")),
        "source_name": source,
        "weighted_score": 0,  # event 레벨에서 재계산
        "ai_analysis": {
            "confidence": analysis.get("confidence", 0),
            "reasoning": analysis.get("reasoning", ""),
            "category_rationale": analysis.get("category_reasoning", ""),
            "camp_reasoning": analysis.get("camp_reasoning", ""),
            "actor_correction": analysis.get("actor_correction", ""),
            "evidence_sentence": analysis.get("evidence_sentence", ""),
            "criminal_stage_reasoning": analysis.get("criminal_stage", None),
            "source_title": title,
            "writer_failed": analysis.get("writer_failed", False),
        },
        "published_at": article.get("published_at", datetime.now().isoformat()),
        "verified": trust["verified"],
        "verification_note": trust["note"],
        "trust_level": trust["trust_level"],
        "criminal_stage": analysis.get("criminal_stage"),
        "institutional_stage": analysis.get("institutional_stage"),
        "coverage_count": len(trust["matched_sources"]) + 1,
        "headline_days": 1,
        "is_archive": is_archive,
        "position_weight": pos_weight,
        "actor_name": actor_name,
        "actor_party": analysis.get("actor_party", ""),
        "cross_verified_sources": [{"name": s["name"], "lean": s["lean"]} for s in trust["matched_sources"][:5]],
        # 이 행이 어느 원문에서, 무엇으로 만들어졌는지. 프롬프트를 바꾼 뒤
        # 전후를 비교하려면 이 셋이 있어야 한다
        "raw_article_id": raw_id,
        "analyzer_version": ANALYZER_VERSION,
        "prompt_hash": _PROMPT_HASH,
        "model": MODEL,
    }

    # 검증 결과를 행에 남긴다. 떨어진 건 위에서 이미 걸렀으므로
    # 여기 오는 것은 passed 또는 warned 다.
    issue["validation_status"] = {
        "insert": "passed", "insert_unverified": "warned",
    }.get(verdict.action, "pending")
    issue["validation_errors"] = verdict.errors + verdict.warnings

    # 개별 issue 점수 (참고용, event 점수가 실제 사용됨)
    issue["weighted_score"] = calculate_score(issue)

    result = insert_issue(issue)
    if not result:
        raw_store.mark_analyzed(raw_id, ANALYZER_VERSION, _PROMPT_HASH, MODEL,
                                skip_reason="DB 저장 실패")
        return "skip_db"

    raw_store.mark_analyzed(raw_id, ANALYZER_VERSION, _PROMPT_HASH, MODEL)

    issue_with_id = {
        **issue,
        "id": result["id"],
        "headline": analysis.get("headline", ""),
        "next_branch": analysis.get("next_branch"),
    }

    # ── Event 생성 또는 머지 ──
    if matched_event:
        merged = merge_into_event(matched_event, issue_with_id)
        if merged:
            tag = "MERGED"
        else:
            # 머지 실패 시 새 event 생성
            create_event(issue_with_id, embedding)
            tag = "NEW(merge_fail)"
    else:
        event = create_event(issue_with_id, embedding)
        if event:
            # active_events에 추가하여 이후 기사와 매칭 가능하게
            active_events.append(event)
        tag = "NEW"

    camp_label = "파랑" if analysis["camp"] == "blue" else "빨강"
    trust_mark = "H" if trust["trust_level"] == "high" else ("M" if trust["trust_level"] == "medium" else "?")
    score_tag = "SCORED" if not is_archive and issue["weighted_score"] > 0 else "ARCHIVE"
    print(f"  [{trust_mark}] [{camp_label}] [{score_tag}] [{tag}] {analysis['category']}: {title[:50]}")
    return "inserted"


def drop_seen(articles: list[dict], seen_urls: set[str], stats: dict[str, int]) -> list[dict]:
    """이미 처리한 기사를 LLM 호출 전에 걸러낸다.

    반드시 예산 배분(balanced_sample)보다 *먼저* 호출해야 한다. 순서가 반대면
    이미 본 기사가 매체별 할당 슬롯을 차지해, 그 매체의 신규 기사가 영영
    분석되지 않는다.
    """
    fresh: list[dict] = []
    for article in articles:
        url = article.get("source_url") or article.get("detail_link") or ""
        if url and url in seen_urls:
            stats["skip_seen"] = stats.get("skip_seen", 0) + 1
            continue
        if url:
            seen_urls.add(url)  # 같은 실행 안에서의 중복도 막는다
        fresh.append(article)
    return fresh


def _process_batch(
    articles: list[dict],
    tier: int,
    politicians_map: dict[str, str],
    politicians_positions: dict[str, str],
    active_events: list[dict],
    all_collected: list[dict],
    stats: dict[str, int],
    limit: int = 50,
) -> None:
    """이미 drop_seen을 통과한 기사를 처리한다.

    중복 제거는 호출자가 책임진다 — drop_seen이 seen_urls를 변경하므로
    같은 리스트에 두 번 적용하면 전부 걸러진다.
    """
    batch = articles[:limit]

    # 분석 **전에** 원문을 저장한다. 분석에서 버려질 기사도 원문은 남아야
    # 나중에 프롬프트를 고쳐 다시 볼 수 있다 — 지금 버리는 게 절반에 가깝다.
    raw_ids = raw_store.save_many(batch, tier)

    for article in batch:
        try:
            url = article.get("source_url") or article.get("detail_link") or ""
            result = process_article(article, tier, politicians_map, politicians_positions,
                                     all_articles=all_collected, active_events=active_events,
                                     raw_id=raw_ids.get(url))
            stats[result] = stats.get(result, 0) + 1
        except Exception as e:
            print(f"  [error] {e}")


def run_pipeline() -> None:
    print(f"\n{'='*60}")
    print(f"민낯 크롤러 v2.0 (Event 기반): {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    # 0. 정치인 DB 동기화
    print("\n[0] 정치인 DB 동기화...")
    try:
        sync_politicians()
    except Exception as e:
        print(f"  [warn] {e}")

    politicians_map = load_politicians_map()
    politicians_positions = load_politician_positions()
    print(f"  정치인 DB: {len(politicians_map)}명")

    # Active events 로드 (dedup 대체)
    active_events = get_active_events()
    print(f"  활성 사건: {len(active_events)}건")


    # 이미 처리한 기사 URL — LLM 호출 전에 거르는 용도
    seen_urls = get_recent_source_urls(days=14)
    print(f"  기처리 URL: {len(seen_urls)}건 (LLM 호출 전 제외)")

    stats: dict[str, int] = {
        "inserted": 0, "skip_analysis": 0,
        "skip_db": 0, "skip_empty": 0, "skip_gate": 0, "skip_validation": 0,
    }
    all_collected: list[dict] = []
    collected_per_source: dict[str, int] = {}

    # 1. Tier 1 — 국회 의안정보
    print("\n[1] 국회 법안 수집...")
    bills = fetch_recent_bills(days=7)
    for b in bills:
        b["published_at"] = b.get("propose_date", datetime.now().isoformat())
    all_collected.extend(bills)
    collected_per_source["국회 법안"] = len(bills)
    print(f"  수집: {len(bills)}건")
    _process_batch(drop_seen(bills, seen_urls, stats), 1, politicians_map, politicians_positions, active_events, all_collected, stats, limit=BILL_BUDGET)

    # 2. Tier 2 — 팩트체크
    print("\n[2] 팩트체크 수집...")
    factchecks = fetch_factchecks()
    for fc in factchecks:
        fc["published_at"] = fc.get("date", datetime.now().isoformat())
        if not fc.get("summary"):
            fc["summary"] = fc.get("title", "")
    all_collected.extend(factchecks)
    if FACTCHECK_ENABLED:  # 의도적 비활성 소스는 "죽은 소스" 경고 대상이 아니다
        collected_per_source["팩트체크"] = len(factchecks)
    print(f"  수집: {len(factchecks)}건")
    _process_batch(drop_seen(factchecks, seen_urls, stats), 2, politicians_map, politicians_positions, active_events, all_collected, stats, limit=FACTCHECK_BUDGET)

    # 3. Tier 3 — RSS
    print("\n[3] 뉴스 RSS 수집...")
    rss_news = fetch_all_news()
    all_collected.extend(rss_news)
    collected_per_source["뉴스 RSS"] = len(rss_news)
    # ① 기처리 제외 → ② 매체별 라운드로빈. 순서를 바꾸면 안 된다(drop_seen 주석 참조).
    rss_fresh = drop_seen(rss_news, seen_urls, stats)
    rss_balanced = balanced_sample(rss_fresh, RSS_BUDGET)
    print(f"  RSS 총 {len(rss_news)}건 → 신규 {len(rss_fresh)}건 → 매체 균등 {len(rss_balanced)}건")
    _process_batch(rss_balanced, 3, politicians_map, politicians_positions, active_events, all_collected, stats, limit=RSS_BUDGET)

    # 4. Tier 3 — 네이버
    print("\n[4] 네이버 뉴스 수집...")
    naver_news = fetch_all_political_news()
    all_collected.extend(naver_news)
    collected_per_source["네이버 뉴스"] = len(naver_news)
    _process_batch(drop_seen(naver_news, seen_urls, stats), 3, politicians_map, politicians_positions, active_events, all_collected, stats, limit=NAVER_BUDGET)

    # 5. Tier 1 — 법원
    print("\n[5] 법원 판결 수집...")
    rulings = fetch_recent_rulings()
    for r in rulings:
        r["published_at"] = r.get("date", datetime.now().isoformat())
    all_collected.extend(rulings)
    collected_per_source["법원 판결"] = len(rulings)
    print(f"  수집: {len(rulings)}건")
    _process_batch(drop_seen(rulings, seen_urls, stats), 1, politicians_map, politicians_positions, active_events, all_collected, stats)

    # 7. 미검증 Event 자동 승격
    print("\n[6] 교차검증 자동 승격...")
    run_auto_verify()

    # 7. 사안 재구성 — 사건이 바뀌었으니 사안도 다시 만든다.
    #    실패해도 파이프라인을 멈추지 않는다. 수집·저장이 더 중요하다
    print("\n[7] 사안 재구성...")
    try:
        build_storylines(apply=True)
    except Exception as e:
        print(f"  [warn] 사안 재구성 실패: {e}")

    # 8. 비활성화 + 스냅샷
    print("\n[8] 비활성화 + 스냅샷...")
    deactivate_old_events()
    generate_daily_snapshot()

    print(f"\n{'='*60}")
    print(f"결과: 저장 {stats['inserted']} | 게이트 차단 {stats.get('skip_gate', 0)} "
          f"| 분석스킵 {stats['skip_analysis']} | 검증탈락 {stats.get('skip_validation', 0)} "
          f"| DB스킵 {stats['skip_db']} | 기처리 제외 {stats.get('skip_seen', 0)}")

    if SKIP_STATS:
        print("버려진 이유:")
        for reason, cnt in sorted(SKIP_STATS.items(), key=lambda kv: -kv[1]):
            print(f"  - {reason}: {cnt}건")

    print(usage_report())
    print(writer.usage_report())
    raw_note = raw_store.report()
    if raw_note:
        print(raw_note)

    # 조용한 실패 방지 — 수집 0건인 소스를 드러낸다
    dead = [name for name, cnt in collected_per_source.items() if cnt == 0]
    if dead:
        print(f"[경고] 수집 0건 소스: {', '.join(dead)} — URL·키 점검 필요")

    # 임베딩 실패도 조용히 지나가면 안 된다.
    # 실패하면 embedding 이 NULL 로 저장되고, 유사 사례와 Stage 2 사건 매칭이 통째로
    # 죽는다. 2026-09 에 OpenAI 크레딧이 떨어진 채로 몇 달을 돌아 클러스터 67건 중
    # 정상 임베딩이 0건이었는데 파이프라인은 계속 초록색이었다.
    if EMBEDDING_FAILURES:
        kinds = Counter(f.split(":")[0] for f in EMBEDDING_FAILURES)
        print(f"[경고] 임베딩 실패 {len(EMBEDDING_FAILURES)}건 — "
              + ", ".join(f"{k} {v}건" for k, v in kinds.most_common()))
        print(f"        예시: {EMBEDDING_FAILURES[0][:160]}")

    print(f"{'='*60}\n")

    # 아무것도 수집하지 못했거나 전량 분석 실패면 워크플로를 실패로 끝낸다.
    # exit 0으로 끝나면 대시보드가 초록색이라 몇 달간 아무도 모른다.
    if not all_collected:
        print("[치명] 모든 소스에서 기사를 한 건도 수집하지 못했습니다")
        sys.exit(1)
    if stats["inserted"] == 0 and stats.get("skip_seen", 0) == 0:
        print("[치명] 신규 기사를 처리했으나 저장 0건입니다 — 위 '버려진 이유'를 확인하세요")
        sys.exit(1)
    if stats["inserted"] > 0 and len(EMBEDDING_FAILURES) >= stats["inserted"]:
        print("[치명] 저장된 기사 전부가 임베딩 없이 들어갔습니다 — "
              "OPENAI_API_KEY 잔액·키를 확인하세요. 유사 사례와 사건 매칭이 동작하지 않습니다")
        sys.exit(1)


if __name__ == "__main__":
    run_pipeline()
