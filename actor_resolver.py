"""행위자 이름 교정 — LLM 이 고른 이름을 기사 본문과 정치인 DB 로 대조한다.

한국 기사는 인물을 약칭으로 쓴다: "李대통령", "이 대통령", "韓대표", "金후보".
LLM 은 이 약칭을 실명으로 풀어 쓰면서 성이 같은 다른 사람을 고른다.
2026-09-19 실측에서 같은 "李대통령 청년정책" 기사가 이재명·이준석·윤석열로 갈렸다.

행위자가 갈리면 사건이 쪼개진다. Stage 1 매칭 키가 actor_name + category 이기 때문이다.
쪼개지면 coverage_count 가 1 에 머물고, tier3 기사가 점수를 받는 유일한 조건인
교차검증(verified, 2개 이상 매체)이 영영 붙지 않는다. 9/18~19 클러스터 29건 중
19건이 단일 매체였고, 김승원 자진사퇴 한 사건이 11개로 나뉘어 있었다.

규칙은 하나다: **행위자 실명은 기사 본문에 있어야 한다.** 본문에 없으면 LLM 이
지어낸 것이므로, 본문의 약칭을 정치인 DB 와 맞춰 다시 찾는다.
"""
import re
from collections import Counter

# 기사에서 성(姓)을 한자로 쓰는 관행에 대응한다. 정치면에 실제로 등장하는 성만 담았다.
HANJA_SURNAME = {
    "李": "이", "金": "김", "朴": "박", "尹": "윤", "韓": "한", "文": "문",
    "安": "안", "崔": "최", "鄭": "정", "趙": "조", "曺": "조", "張": "장",
    "姜": "강", "洪": "홍", "吳": "오", "劉": "유", "柳": "유", "申": "신",
    "沈": "심", "元": "원", "秋": "추", "羅": "나", "千": "천", "孫": "손",
    "裵": "배", "白": "백", "許": "허", "嚴": "엄", "宋": "송", "禹": "우",
    "周": "주", "任": "임", "林": "임", "全": "전", "高": "고", "梁": "양",
    "玄": "현", "陳": "진", "呂": "여", "權": "권", "黃": "황", "車": "차",
    "具": "구", "閔": "민", "盧": "노", "河": "하", "郭": "곽", "成": "성",
    "蔡": "채", "康": "강", "石": "석", "宣": "선", "邊": "변", "卞": "변",
    "池": "지", "都": "도", "南": "남",
}

# 긴 직책을 먼저 써야 "원내대표"가 "대표"로 잘리지 않는다
_OFFICES = (
    "대통령", "국무총리", "부총리", "총리", "원내대표", "최고위원", "대표",
    "장관", "도지사", "지사", "시장", "위원장", "후보", "의원",
)
_OFFICE_ALT = "|".join(_OFFICES)

# "이 대통령", "李대통령", "李 대통령" — 성 한 글자 + 직책
_ABBREV_RE = re.compile(rf"([가-힣一-鿿])\s?({_OFFICE_ALT})")

# LLM이 "홍길동 의원"처럼 직책을 붙여 반환하는 경우를 대비한 접미사 목록
_TITLE_SUFFIXES = (
    "대통령", "국무총리", "총리", "부총리", "장관", "차관", "청장", "처장",
    "원내대표", "대표", "최고위원", "사무총장", "의장", "부의장", "위원장",
    "의원", "시장", "도지사", "지사", "군수", "구청장", "교육감", "후보", "당선인", "씨",
)

# DB 직책이 "서울시장"처럼 지역을 달고 있어도 "시장"으로 불린다.
# 반대로 "원내대표"는 "대표"가 아니다 — 서열이 다른 자리라 접미 매칭을 허용하지 않는다.
_SUFFIX_MATCHABLE_OFFICES = ("시장", "도지사", "지사")


def normalize_actor_name(raw: str) -> str:
    """행위자 이름에서 직책·수식어를 떼어 DB 조회용 인명만 남긴다."""
    name = (raw or "").strip()
    if not name:
        return ""
    # "홍길동 의원" → "홍길동"
    for suffix in _TITLE_SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix):
            name = name[: -len(suffix)].strip()
            break
    # "오세훈 서울시장" 은 위에서 "시장"만 떨어져 "오세훈 서울"이 남는다.
    # 한국 인명은 띄어 쓰지 않으므로 공백 앞까지가 이름이다.
    if " " in name:
        name = name.split()[0]
    return name


def _office_matches(position: str, office: str) -> bool:
    """DB 직책이 기사에서 부른 직책과 같은 자리인가."""
    position = (position or "").strip()
    if not position:
        return False
    # "전 대통령"은 지금 "대통령"으로 불리지 않는다
    if position.startswith("전 "):
        return False
    if position == office:
        return True
    return office in _SUFFIX_MATCHABLE_OFFICES and position.endswith(office)


def _from_abbreviation(text: str, positions: dict[str, str]) -> str:
    """본문의 '성 + 직책' 약칭을 정치인 DB 와 맞춰 실명으로 푼다.

    같은 성·같은 직책이 둘 이상이면 포기한다. 찍어서 맞히는 것보다 비우는 게 낫다.
    """
    counts: Counter[tuple[str, str]] = Counter()
    for m in _ABBREV_RE.finditer(text):
        surname = HANJA_SURNAME.get(m.group(1), m.group(1))
        counts[(surname, m.group(2))] += 1

    for (surname, office), _ in counts.most_common():
        matched = [
            name
            for name, position in positions.items()
            if name.startswith(surname) and _office_matches(position, office)
        ]
        if len(matched) == 1:
            return matched[0]
    return ""


def _sole_name_in_text(text: str, known_names) -> str:
    """본문에 실명이 딱 한 명만 등장하면 그 사람이 행위자다."""
    found = [name for name in known_names if name in text]
    return found[0] if len(found) == 1 else ""


def resolve_actor(
    raw_name: str,
    text: str,
    positions: dict[str, str],
) -> tuple[str, str]:
    """행위자 이름을 확정한다.

    Args:
        raw_name: LLM 이 준 actor_name
        text: 제목 + 본문 (약칭을 찾을 원문)
        positions: 이름 → 직책 (정치인 DB)

    Returns:
        (확정된 이름, 교정 사유). 교정하지 않았으면 사유는 빈 문자열.
    """
    name = normalize_actor_name(raw_name)
    if not name:
        return "", ""

    # 본문에 실명이 있으면 LLM 을 믿는다
    if name in text:
        return name, ""

    fixed = _from_abbreviation(text, positions)
    if fixed and fixed != name:
        return fixed, f"본문에 '{name}' 이 없어 약칭으로 교정: {fixed}"

    sole = _sole_name_in_text(text, positions.keys())
    if sole and sole != name:
        return sole, f"본문에 '{name}' 이 없어 유일한 실명으로 교정: {sole}"

    # 교정할 근거가 없으면 LLM 값을 그대로 둔다. 비정치인·비의원(장관·후보자 등)은
    # 정치인 DB 에 없어 여기서 걸러지는데, 그건 뒤의 resolve_camp 가 판단할 몫이다.
    return name, ""
