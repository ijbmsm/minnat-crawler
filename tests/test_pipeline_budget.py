"""수집 예산·중복 제거 로직 회귀 테스트.

여기 담긴 두 성질은 실제로 한 번씩 깨졌던 것들이다:
  1) 매체별 균등 할당 — 앞에서 N개를 자르면 기사 많은 매체가 슬롯을 독식
  2) 중복 제거가 할당보다 먼저 — 순서가 반대면 기처리 기사가 슬롯을 차지해
     그 매체의 신규 기사가 영영 분석되지 않는다
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crawlers.news import balanced_sample
from main import drop_seen


def _article(source: str, n: int) -> dict:
    return {"source": source, "source_url": f"https://{source}.example/{n}"}


def test_balanced_sample_spreads_across_outlets():
    # 한 매체가 100건, 나머지는 5건씩
    articles = (
        [_article("연합뉴스", i) for i in range(100)]
        + [_article("한겨레", i) for i in range(5)]
        + [_article("조선일보", i) for i in range(5)]
    )
    picked = balanced_sample(articles, 12)
    counts = {}
    for a in picked:
        counts[a["source"]] = counts.get(a["source"], 0) + 1

    assert len(picked) == 12
    # 어느 매체도 전체를 독식하지 못한다
    assert max(counts.values()) <= 5
    assert set(counts) == {"연합뉴스", "한겨레", "조선일보"}


def test_balanced_sample_handles_small_pool():
    articles = [_article("한겨레", i) for i in range(3)]
    assert len(balanced_sample(articles, 10)) == 3
    assert balanced_sample([], 10) == []
    assert balanced_sample(articles, 0) == []


def test_balanced_sample_keeps_within_outlet_order():
    articles = [_article("한겨레", i) for i in range(5)]
    picked = balanced_sample(articles, 3)
    assert [a["source_url"] for a in picked] == [
        "https://한겨레.example/0",
        "https://한겨레.example/1",
        "https://한겨레.example/2",
    ]


def test_drop_seen_filters_and_counts():
    seen = {"https://한겨레.example/0"}
    stats: dict[str, int] = {}
    articles = [_article("한겨레", i) for i in range(3)]

    fresh = drop_seen(articles, seen, stats)

    assert len(fresh) == 2
    assert stats["skip_seen"] == 1
    # 이번 실행에서 본 URL도 집합에 추가되어 같은 실행 내 재처리를 막는다
    assert "https://한겨레.example/1" in seen


def test_drop_seen_is_not_idempotent_so_call_once():
    """drop_seen은 seen_urls를 변경한다 — 같은 리스트에 두 번 쓰면 전부 사라진다.

    이 성질을 테스트로 못박아, 호출부에서 중복 적용하는 실수를 막는다.
    """
    seen: set[str] = set()
    stats: dict[str, int] = {}
    articles = [_article("한겨레", i) for i in range(3)]

    assert len(drop_seen(articles, seen, stats)) == 3
    assert drop_seen(articles, seen, stats) == []


def test_dedup_before_budget_preserves_fresh_articles():
    """중복 제거를 먼저 해야 신규 기사가 예산 슬롯을 받는다."""
    # 한겨레의 앞 2건은 이미 처리됨
    articles = [_article("한겨레", i) for i in range(4)] + [_article("조선일보", i) for i in range(4)]
    seen = {"https://한겨레.example/0", "https://한겨레.example/1"}

    # 올바른 순서: 중복 제거 → 할당
    fresh = drop_seen(articles, set(seen), {})
    good = balanced_sample(fresh, 4)
    assert len(good) == 4, "신규 기사만으로 예산을 채워야 한다"

    # 잘못된 순서: 할당 → 중복 제거 (기처리 기사가 슬롯을 낭비)
    budgeted = balanced_sample(articles, 4)
    bad = drop_seen(budgeted, set(seen), {})
    assert len(bad) < 4, "이 순서는 슬롯을 낭비한다 — 회귀 감시용"


# ── camp 판정: LLM 추론이 아니라 DB 조회여야 한다 ──

from analyzer import resolve_camp, normalize_actor_name  # noqa: E402

_ROSTER = {"홍길동": "blue", "김철수": "red"}


def test_resolve_camp_uses_roster_lookup():
    camp, reason = resolve_camp("홍길동", None, _ROSTER)
    assert camp == "blue"
    assert "정치인 DB 조회" in reason


def test_resolve_camp_strips_titles():
    """LLM이 지시를 어기고 직책을 붙여도 매칭돼야 한다."""
    assert resolve_camp("홍길동 의원", None, _ROSTER)[0] == "blue"
    assert resolve_camp("김철수 원내대표", None, _ROSTER)[0] == "red"
    # 접미사가 더 긴 것부터 검사되는지 (원내대표 > 대표)
    assert normalize_actor_name("김철수 원내대표") == "김철수"


def test_resolve_camp_rejects_unknown_actor():
    camp, reason = resolve_camp("이몽룡 장관", None, _ROSTER)
    assert camp is None
    assert "정치인 DB에 없음" in reason


def test_resolve_camp_rejects_empty_actor():
    camp, reason = resolve_camp("", None, _ROSTER)
    assert camp is None
    assert "특정하지 못함" in reason


def test_resolve_camp_notes_article_party_in_reason():
    _, reason = resolve_camp("홍길동", "더불어민주당", _ROSTER)
    assert "더불어민주당" in reason


def test_system_prompt_excludes_roster():
    """명단이 프롬프트에 다시 들어가면 비용이 명단 크기에 비례해 늘어난다."""
    import re

    from analyzer import build_system_prompt

    prompt = build_system_prompt()
    # 과거 형식: "  - 홍길동: blue" 같은 줄이 정치인 수만큼 반복됐다
    roster_lines = re.findall(r"^\s*-\s*\S+:\s*(?:blue|red)\s*$", prompt, re.MULTILINE)
    assert not roster_lines, f"명단이 프롬프트에 다시 들어갔다: {roster_lines[:3]}"

    # 길이는 **띠**로 본다. 위아래 둘 다 이유가 있다.
    #
    # 아래쪽 — 캐시 최소 길이. 2026-09-25 실측으로 Haiku 4.5 의 임계는
    #   **4,096 토큰**이다(2,048 이 아니다). 4,042 토큰에서는 cache_write·
    #   cache_read 가 둘 다 0 이고, 4,904 토큰에서는 걸린다. 임계 밑으로 내려가면
    #   cache_control 이 조용히 무시되고 매 호출이 전액 입력이 된다 —
    #   실제로 그 상태로 돌고 있었다(실행당 입력 39만 토큰, 캐시 0).
    #   측정된 밀도는 약 1.38자/토큰이라 4,096 토큰 ≈ 5,640자다. 여유를 두고 6,000.
    #
    # 위쪽 — 명단 주입 방지. 이 테스트가 실제로 막는 것은 위 regex 가 잡고,
    #   길이는 보조 지표다. 캐시 읽기가 0.1배라 길이 자체는 싸다.
    #   "작게" 가 목표가 아니라 "항목 수에 비례해 무한히 늘어나지 않게" 가 목표다.
    assert len(prompt) >= 6000, (
        "프롬프트가 캐시 최소 길이(4,096 토큰 ≈ 5,640자) 밑으로 내려갔다. "
        "cache_control 이 무시되어 입력 비용이 그대로 나간다")
    assert len(prompt) < 9000, "프롬프트가 커졌다면 명단·목록이 다시 들어갔는지 확인"


def test_system_prompt_takes_no_arguments():
    """build_system_prompt(politicians_map) 형태로 되돌아가지 않도록 못박는다."""
    import inspect

    from analyzer import build_system_prompt

    assert not inspect.signature(build_system_prompt).parameters
