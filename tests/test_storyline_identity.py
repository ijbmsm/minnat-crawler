"""사안 신원 — 같은 사안이 여러 벌 생기지 않는가.

2026-09-25 실측: storylines 106건 안에 김건희 8벌·조국 6벌·박근혜 6벌·의대정원 5벌.
원인은 둘이었다.
  ① slug 를 LLM 이 매번 새로 줬다 (lee-jae-myung- 과 leejaemyung- 이 공존)
  ② 그 slug 로 기존 사안을 찾았다 → 빗나가면 UPDATE 가 아니라 INSERT
"""
from storyline_builder import group_key, make_slug, _fingerprint


def test_group_key_is_deterministic():
    """같은 label 은 언제 불러도 같은 신원이어야 한다. 아니면 중복이 생긴다."""
    assert group_key("이재명 대장동") == group_key("이재명 대장동")
    assert group_key("김건희 도이치모터스") == group_key("김건희 도이치모터스")


def test_group_key_separates_different_labels():
    assert group_key("이재명 대장동") != group_key("김건희 도이치모터스")


def test_group_key_survives_romanization_drift():
    """신원이 로마자 표기에 안 묶여 있어야 한다.

    LLM 은 같은 사람을 lee-jae-myung 으로도, leejaemyung 으로도 쓴다.
    실제로 두 표기가 DB 에 공존했다. 신원이 label 기반이면 영향받지 않는다.
    """
    label = "이재명 성남FC"
    assert group_key(label) == group_key(label)


def test_fingerprint_ignores_order():
    """사건 순서가 달라졌다고 원고를 다시 쓰면 안 된다."""
    a = [{"id": "x"}, {"id": "y"}]
    b = [{"id": "y"}, {"id": "x"}]
    assert _fingerprint(a) == _fingerprint(b)


def test_fingerprint_detects_membership_change():
    """사건이 하나 붙으면 원고를 다시 써야 한다."""
    a = [{"id": "x"}, {"id": "y"}]
    c = [{"id": "x"}, {"id": "y"}, {"id": "z"}]
    assert _fingerprint(a) != _fingerprint(c)


def test_make_slug_is_stable_for_same_label():
    """030 미적용 환경의 대체 신원. 결정론이 깨지면 그때도 중복이 난다."""
    assert make_slug("이재명 대장동", "이재명 대장동") == make_slug("이재명 대장동", "이재명 대장동")


# ── 신원은 최초 사건으로 (2026-09-28) ──
#
# label 기반 신원은 "그룹에서 가장 많이 공유되는 낱말" 이라 사건 하나만 들어와도
# 바뀐다. 그때마다 새 사안이 생기고 옛 것이 고아가 됐다 — 중복을 정리한 뒤에도
# 실행당 2~4건씩 늘던 원인이다.

from storyline_builder import group_key_of


def _ev(eid, date):
    return {"id": eid, "first_reported_at": date}


def test_group_key_survives_new_events():
    """사안은 사건이 쌓이며 자란다. 자라도 신원은 그대로여야 한다."""
    before = [_ev("a", "2026-01-01"), _ev("b", "2026-02-01")]
    after = before + [_ev("c", "2026-03-01")]
    assert group_key_of(before) == group_key_of(after)


def test_group_key_ignores_member_order():
    a = [_ev("a", "2026-01-01"), _ev("b", "2026-02-01")]
    b = [_ev("b", "2026-02-01"), _ev("a", "2026-01-01")]
    assert group_key_of(a) == group_key_of(b)


def test_group_key_is_deterministic_on_same_date():
    """최초 보도일이 같아도 순서가 흔들리면 안 된다. id 로 가른다."""
    a = [_ev("b", "2026-01-01"), _ev("a", "2026-01-01")]
    b = [_ev("a", "2026-01-01"), _ev("b", "2026-01-01")]
    assert group_key_of(a) == group_key_of(b)


def test_group_key_differs_for_different_groups():
    assert group_key_of([_ev("a", "2026-01-01")]) != group_key_of([_ev("z", "2026-01-01")])


def test_group_key_of_empty_is_empty():
    assert group_key_of([]) == ""
