"""Event 매칭 파이프라인 — 4단계 매칭으로 새 기사를 기존 사건(Event)에 연결

Stage 0: DB 필터 (7일 윈도우 active events)
Stage 1: actor_name + category 룰 매칭
Stage 2: Embedding cosine similarity
Stage 3: LLM 판정 (Haiku 배치, 고영향은 Sonnet)
"""
import json
import numpy as np
import openai

import anthropic

from config import (
    OPENAI_API_KEY,
    ANTHROPIC_API_KEY,
    EMBEDDING_MODEL,
    EVENT_MATCH_THRESHOLD,
    EVENT_REJECT_THRESHOLD,
    EVENT_HIGH_IMPACT_COVERAGE,
)

openai_client = openai.OpenAI(api_key=OPENAI_API_KEY)
anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


# ── Embedding ──

def get_embedding(text: str) -> list[float]:
    """OpenAI text-embedding-3-small로 임베딩 벡터 생성."""
    if not text.strip():
        return [0.0] * 1536
    try:
        resp = openai_client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=text[:2000],  # 토큰 절약: 2000자 제한
        )
        return resp.data[0].embedding
    except Exception as e:
        print(f"  [embedding] 실패: {e}")
        return [0.0] * 1536


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """두 벡터의 코사인 유사도 계산."""
    va = np.array(a, dtype=np.float32)
    vb = np.array(b, dtype=np.float32)
    dot = np.dot(va, vb)
    norm_a = np.linalg.norm(va)
    norm_b = np.linalg.norm(vb)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(dot / (norm_a * norm_b))


# ── Stage 1: 룰 기반 매칭 ──

def stage1_rule_match(
    issue: dict,
    events: list[dict],
) -> list[dict]:
    """actor_name + category가 일치하는 event 후보를 반환한다.

    actor_name이 없는 경우 category만으로 필터.
    반환: 최대 5개 후보 (최신순).
    """
    actor = issue.get("actor_name", "")
    category = issue.get("category", "")
    candidates = []

    for event in events:
        # category 일치 필수
        if event.get("category") != category:
            continue
        # actor 일치 (둘 다 있을 때만 비교)
        if actor and event.get("actor_name") and actor != event["actor_name"]:
            continue
        candidates.append(event)

    # 최신순 정렬, 최대 5개
    candidates.sort(key=lambda e: e.get("last_reported_at", ""), reverse=True)
    return candidates[:5]


# ── Stage 2: Embedding 매칭 ──

def stage2_embedding_match(
    issue_embedding: list[float],
    candidates: list[dict],
) -> tuple[dict | None, list[dict]]:
    """후보 events와 cosine similarity 비교.

    Returns:
        (confirmed_match, gray_zone_candidates)
        - confirmed_match: 유사도 >= MATCH_THRESHOLD인 최고 event (or None)
        - gray_zone_candidates: REJECT < sim < MATCH인 후보들
    """
    scored = []
    for event in candidates:
        event_emb = event.get("embedding")
        if not event_emb:
            continue
        sim = cosine_similarity(issue_embedding, event_emb)
        scored.append((sim, event))

    if not scored:
        return None, []

    # 유사도 내림차순
    scored.sort(key=lambda x: x[0], reverse=True)

    best_sim, best_event = scored[0]

    # 확정 매칭
    if best_sim >= EVENT_MATCH_THRESHOLD:
        return best_event, []

    # 확정 배제 + 회색지대 분리
    gray_zone = []
    for sim, event in scored:
        if sim > EVENT_REJECT_THRESHOLD:
            gray_zone.append({"event": event, "similarity": sim})

    return None, gray_zone


# ── Stage 3: LLM 판정 ──

def stage3_llm_judgment(
    issue: dict,
    gray_candidates: list[dict],
) -> dict | None:
    """회색지대 후보들을 LLM에게 배치로 판정시킨다.

    고영향 사건(coverage_count >= HIGH_IMPACT)은 Sonnet, 나머지는 Haiku.
    Returns: 매칭된 event 또는 None.
    """
    if not gray_candidates:
        return None

    new_title = issue.get("title", "")
    new_summary = issue.get("summary", "")[:200]

    # 배치 프롬프트 구성
    comparisons = []
    for i, cand in enumerate(gray_candidates[:5]):  # 최대 5개
        evt = cand["event"]
        comparisons.append(
            f"[Event {i+1}] "
            f"제목: {evt.get('summary', '')[:100]} | "
            f"행위자: {evt.get('actor_name', '?')} | "
            f"카테고리: {evt.get('category', '?')} | "
            f"유사도: {cand['similarity']:.2f}"
        )

    events_text = "\n".join(comparisons)

    # 고영향 여부 확인 (가장 높은 coverage 기준)
    max_coverage = max(
        (c["event"].get("coverage_count", 1) for c in gray_candidates),
        default=1,
    )
    use_sonnet = max_coverage >= EVENT_HIGH_IMPACT_COVERAGE
    model = "claude-sonnet-4-20250514" if use_sonnet else "claude-haiku-4-5-20251001"

    try:
        resp = anthropic_client.messages.create(
            model=model,
            max_tokens=50,
            system=(
                "한국 정치 뉴스 이벤트 매칭기입니다. "
                "새 기사가 기존 사건 중 하나와 같은 사건인지 판단하세요. "
                "JSON으로 답하세요: {\"match\": 1} (Event 번호) 또는 {\"match\": 0} (모두 다른 사건)"
            ),
            messages=[{
                "role": "user",
                "content": (
                    f"새 기사:\n"
                    f"제목: {new_title}\n"
                    f"요약: {new_summary}\n"
                    f"행위자: {issue.get('actor_name', '?')}\n"
                    f"카테고리: {issue.get('category', '?')}\n\n"
                    f"기존 사건 목록:\n{events_text}\n\n"
                    f"같은 사건이 있으면 Event 번호를, 없으면 0을 반환하세요."
                ),
            }],
        )

        text = resp.content[0].text.strip()
        # JSON 파싱
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        result = json.loads(text)
        match_idx = result.get("match", 0)

        if match_idx > 0 and match_idx <= len(gray_candidates):
            matched = gray_candidates[match_idx - 1]["event"]
            model_tag = "sonnet" if use_sonnet else "haiku"
            print(f"  [stage3:{model_tag}] 매칭: {matched.get('summary', '')[:40]}")
            return matched

        return None

    except Exception as e:
        print(f"  [stage3] LLM 판정 실패: {e}")
        return None


# ── 오케스트레이터 ──

def match_to_event(
    issue: dict,
    active_events: list[dict],
    issue_embedding: list[float],
) -> dict | None:
    """4단계 매칭을 실행하여 기존 event를 찾거나 None을 반환한다.

    Args:
        issue: 새 기사 정보 (title, summary, actor_name, category, camp, published_at)
        active_events: load_active_events()의 결과
        issue_embedding: get_embedding()의 결과

    Returns:
        매칭된 event dict 또는 None (새 event 생성 필요)
    """
    if not active_events:
        return None

    # Stage 1: 룰 기반 필터
    candidates = stage1_rule_match(issue, active_events)
    if not candidates:
        # Stage 1에서 후보 0개 → 전체 events 대상 embedding만 비교
        confirmed, gray = stage2_embedding_match(issue_embedding, active_events)
        if confirmed:
            return confirmed
        if gray:
            return stage3_llm_judgment(issue, gray)
        return None

    # Stage 2: Embedding 비교 (후보 대상)
    confirmed, gray = stage2_embedding_match(issue_embedding, candidates)
    if confirmed:
        return confirmed

    # Stage 3: LLM 판정 (회색지대)
    if gray:
        return stage3_llm_judgment(issue, gray)

    # 후보 있었지만 embedding이 없는 경우 → Stage 1 매칭만으로 판단
    # actor + category 일치 + 7일 이내면 같은 사건일 가능성 높음
    if len(candidates) == 1:
        only = candidates[0]
        actor = issue.get("actor_name", "")
        if actor and actor == only.get("actor_name"):
            print(f"  [stage1] actor+category 단독 매칭: {only.get('summary', '')[:40]}")
            return only

    return None
