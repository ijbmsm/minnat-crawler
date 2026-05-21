"""교차검증 자동 승격 — 미검증 이슈를 새 기사와 비교하여 verified로 승격

실행 타이밍: 매 크롤러 실행 마지막에 호출.

로직:
  1차: 제목+요약 유사도 >= 0.7 → 바로 같은 이슈 (AI 안 씀)
  2차: actor_name + category + 7일 이내 → 바로 같은 이슈 (AI 안 씀)
  3차: 유사도 0.4~0.7 → AI에게 "같은 사건인가?" 질문
  4차: 유사도 0.4 미만 → 다른 이슈
"""
import json
from datetime import datetime, timedelta

import anthropic

from config import ANTHROPIC_API_KEY, MEDIA_LEAN
from db import get_client
from dedup import text_similarity

ai_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def _get_lean(source: str) -> str:
    for media, lean in MEDIA_LEAN.items():
        if media in source:
            return lean
    return "unknown"


def _ai_same_event(title_a: str, summary_a: str, title_b: str, summary_b: str) -> bool:
    """AI에게 두 기사가 같은 사건인지 판단시킨다."""
    try:
        resp = ai_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=10,
            system="두 뉴스 기사가 같은 정치적 사건/이슈를 다루고 있는지 판단하세요. 'yes' 또는 'no'만 답하세요.",
            messages=[{
                "role": "user",
                "content": f"기사A 제목: {title_a}\n기사A 요약: {summary_a[:200]}\n\n기사B 제목: {title_b}\n기사B 요약: {summary_b[:200]}\n\n같은 사건인가?",
            }],
        )
        answer = resp.content[0].text.strip().lower()
        return answer.startswith("yes") or answer == "예" or answer == "네"
    except Exception as e:
        print(f"  [ai_verify] 판단 실패: {e}")
        return False


def run_auto_verify() -> int:
    """미검증 Tier 3 이슈를 교차검증하여 verified로 승격한다."""
    client = get_client()

    # 최근 7일 미검증 이슈
    since = (datetime.now() - timedelta(days=7)).isoformat()
    unverified_result = (
        client.table("issues")
        .select("id, title, summary, camp, category, actor_name, source_name, published_at")
        .eq("verified", False)
        .eq("source_tier", 3)
        .gte("published_at", since)
        .execute()
    )
    unverified = unverified_result.data
    if not unverified:
        print("[verify] 미검증 이슈 없음")
        return 0

    # 전체 이슈 (비교 대상 — 다른 매체의 이슈)
    all_result = (
        client.table("issues")
        .select("id, title, summary, camp, category, actor_name, source_name, published_at")
        .gte("published_at", since)
        .execute()
    )
    all_issues = all_result.data

    verified_count = 0

    for issue in unverified:
        matched_sources: list[str] = []
        issue_lean = _get_lean(issue["source_name"])

        for other in all_issues:
            # 자기 자신 스킵
            if other["id"] == issue["id"]:
                continue
            # 같은 매체 스킵
            if other["source_name"] == issue["source_name"]:
                continue

            # 날짜 차이 체크
            try:
                d1 = datetime.fromisoformat(issue["published_at"].replace("Z", "+00:00"))
                d2 = datetime.fromisoformat(other["published_at"].replace("Z", "+00:00"))
                if abs((d1 - d2).days) > 7:
                    continue
            except (ValueError, TypeError):
                continue

            # ── 1차: 유사도 >= 0.7 → 바로 매칭 ──
            title_sim = text_similarity(issue["title"], other["title"])
            summary_sim = text_similarity(issue["summary"], other["summary"])
            combined = title_sim * 0.5 + summary_sim * 0.5

            if combined >= 0.7:
                matched_sources.append(other["source_name"])
                continue

            # ── 2차: actor + category 일치 → 바로 매칭 ──
            if (
                issue.get("actor_name")
                and issue["actor_name"] == other.get("actor_name")
                and issue["category"] == other["category"]
            ):
                matched_sources.append(other["source_name"])
                continue

            # ── 3차: 유사도 0.4~0.7 → AI 판단 ──
            if 0.4 <= combined < 0.7:
                if _ai_same_event(issue["title"], issue["summary"], other["title"], other["summary"]):
                    matched_sources.append(other["source_name"])

        # ── 승격 판정 ──
        if not matched_sources:
            continue

        # 매체 다양성 체크: 다른 성향 매체가 있는지
        other_leans = {_get_lean(s) for s in matched_sources}
        all_leans = other_leans | {issue_lean}
        has_diversity = len(all_leans - {"unknown"}) >= 2

        # 2개 이상 다른 매체가 보도 + 성향 다양성
        unique_other_sources = len(set(matched_sources))
        should_verify = unique_other_sources >= 2 or (unique_other_sources >= 1 and has_diversity)

        if should_verify:
            # DB 업데이트
            source_list = list(set(matched_sources))[:5]
            try:
                client.table("issues").update({
                    "verified": True,
                    "cross_verified_sources": [{"name": s, "lean": _get_lean(s)} for s in source_list],
                    "verification_note": f"교차검증 완료: {', '.join(source_list)}",
                }).eq("id", issue["id"]).execute()

                print(f"  [verified] {issue['title'][:50]} ← {', '.join(source_list)}")
                verified_count += 1
            except Exception as e:
                print(f"  [error] 승격 실패: {e}")

    print(f"[verify] 승격 완료: {verified_count}건 / 미검증 {len(unverified)}건")
    return verified_count


if __name__ == "__main__":
    run_auto_verify()
