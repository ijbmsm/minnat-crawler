"""매체 식별 — 이름을 못 읽으면 진영도 못 붙는다.

2026-09-30 실측: 진영 미상 168건 중 120건이 네이버 경유였다. 원인 둘.
  ① 도메인(`www.ytn.co.kr`)을 한글 매체명(`YTN`)과 `in` 으로 대조 — 절대 안 맞는다
  ② naver_news.py 에 MEDIA_LEAN 12개짜리 옛 사본이 있어 config 를 가렸다

진영이 unknown 이면 다양도 집합에서 버려지고(media_diversity 가 discard),
신뢰도 게이트의 '2진영 이상' 이 영영 안 걸린다. 교차검증이 안 붙는 직접 원인이다.
"""
import config
from crawlers import naver_news


def test_domain_resolves_to_outlet_name():
    assert config.media_from_url("https://www.ytn.co.kr/_ln/x") == "YTN"
    assert config.media_from_url("https://www.khan.co.kr/article/1") == "경향신문"
    assert config.media_from_url("http://www.munhwa.com/news/x") == "문화일보"


def test_subdomain_matches():
    assert config.media_from_url("https://news.sbs.co.kr/news/x") == "SBS"


def test_unknown_domain_is_kept_not_dropped():
    """모르는 도메인을 버리지 않는다. 버리면 무엇이 빠졌는지 알 수 없다."""
    got = config.media_from_url("https://www.some-new-press.co.kr/a")
    assert got == "some-new-press.co.kr"


def test_empty_url_is_safe():
    assert config.media_from_url("") == ""


def test_naver_uses_config_not_a_local_copy():
    """naver_news 에 MEDIA_LEAN 사본을 다시 두면 여기서 걸린다.

    사본이 있으면 config 에만 등록된 매체가 unknown 으로 떨어진다.
    """
    name, lean = naver_news._detect_media("https://www.ytn.co.kr/x")
    assert name == "YTN"
    assert lean == config.MEDIA_LEAN["YTN"] == "center"


def test_rss_and_naver_agree_on_the_same_outlet():
    """같은 매체가 경로에 따라 다른 이름이 되면 한 매체를 둘로 센다.

    그러면 coverage_count 가 부풀고 교차검증이 왜곡된다.
    """
    from crawlers.news import NEWS_FEEDS
    rss_names = {f["name"] for f in NEWS_FEEDS}
    for domain, name in config.MEDIA_DOMAINS.items():
        if name in rss_names:
            # RSS 가 쓰는 이름과 도메인 표가 주는 이름이 같아야 한다
            assert config.media_from_url(f"https://www.{domain}/x") == name


def test_politics_filter_keeps_obvious_political_news():
    """옛 키워드 24개가 RSS 539건 중 251건(47%)을 버렸다. 실제로 버려진 제목들이다."""
    from crawlers.news import _is_political
    for title in [
        "'원내대표 멱살잡이' 국힘 권영진, 당원권 정지 1년 '징계 감경'",
        "국정원 \"포로송환 시작부터 비공개 합의\"",
        "합참 \"북 '국경선 요새화' 즉시 중단해야\"",
        "유엔사 \"DMZ 폭발, 정전협정 위반\"",
    ]:
        assert _is_political(title, ""), f"정치 기사인데 걸러진다: {title}"


def test_politics_filter_has_no_person_names():
    """명단을 넣으면 목록이 사람 수에 비례해 늘어난다. 직위·기관·절차만 넣는다."""
    from crawlers.news import POLITICS_KEYWORDS
    from db import get_client
    names = {p["name"] for p in get_client().table("politicians")
             .select("name").eq("active", True).limit(400).execute().data}
    leaked = [k for k in POLITICS_KEYWORDS if k in names]
    assert not leaked, f"정치인 이름이 키워드에 들어갔다: {leaked}"


# ── 출처 독립성 (2026-09-30) ──
#
# 점수에서 진영을 뺐다. 기사 765건 중 613건이 단독 보도라 진영 다양성 판정이
# 실제로는 거의 도달하지 못했고(다양성으로 verified 된 건 16건뿐), 무엇보다
# 매체의 정치색을 우리가 정하는 것이 "점수 매기지 않는다" 는 원칙과 충돌했다.
#
# 대신 계열(소유 관계)로 독립성을 잰다 — 정치 판단이 아니라 확인 가능한 사실이다.

from scorer import independent_sources, media_diversity


def _s(*names):
    return [{"name": n} for n in names]


def test_same_group_counts_as_one():
    """조선일보와 TV조선이 보도해도 두 곳이 확인한 게 아니다."""
    assert independent_sources(_s("조선일보", "TV조선")) == 1
    assert independent_sources(_s("중앙일보", "JTBC")) == 1
    assert independent_sources(_s("동아일보", "채널A")) == 1


def test_different_groups_count_separately():
    assert independent_sources(_s("조선일보", "한겨레")) == 2
    assert independent_sources(_s("조선일보", "한겨레", "연합뉴스")) == 3


def test_unknown_outlet_counts_as_independent():
    """목록에 없으면 독립으로 본다. 모르면 나누지 않는 쪽이 안전하다 —
    잘못 묶으면 실제 교차검증을 깎아내린다."""
    assert independent_sources(_s("한겨레", "처음보는신문")) == 2


def test_multiplier_steps():
    """계단값은 웹 independenceMultiplier 와 같아야 한다 (하네스 M-01)."""
    assert media_diversity(_s("한겨레")) == 0.7
    assert media_diversity(_s("조선일보", "TV조선")) == 0.7   # 계열 → 1곳
    assert media_diversity(_s("조선일보", "한겨레")) == 1.0
    assert media_diversity(_s("조선일보", "한겨레", "연합뉴스")) == 1.3


def test_empty_is_lowest():
    assert independent_sources([]) == 0
    assert media_diversity(None) == 0.7


def test_lean_is_no_longer_used_for_scoring():
    """진영이 붙어 있든 없든 배수가 같아야 한다. 점수에서 진영을 뺐다."""
    with_lean = [{"name": "조선일보", "lean": "conservative"}, {"name": "한겨레", "lean": "progressive"}]
    without = _s("조선일보", "한겨레")
    assert media_diversity(with_lean) == media_diversity(without)
