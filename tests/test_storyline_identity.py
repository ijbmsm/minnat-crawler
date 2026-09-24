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
