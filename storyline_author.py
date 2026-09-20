"""사안 원고를 쓴다 — 뼈대 위에 문장만 얹는다.

역할 분담이 중요하다:
  storyline_discover  어떤 사건이 한 사안인가   (규칙)
  storyline_shape     장을 어디서 자르나, 등급  (규칙)
  storyline_author    제목·본문·요지            (LLM)

등급과 장 구분을 LLM 에 맡기지 않는 이유는, 그게 사실관계이고 같은 입력에 매번 다른
답이 나오면 안 되기 때문이다. LLM 은 이미 정해진 뼈대를 설명하는 문장만 쓴다.

인과 문장 규칙: 사건 요약이 명시적으로 두 국면을 잇지 않으면 인과를 쓰지 않는다.
"이 보도로 검찰이 움직였다" 는 없는 인과를 만드는 문장이고, 서사에서 가장 위험한 자리다.
대신 시간 연결어("그 다음 달, 이듬해")만 허용한다.

날짜 규칙: **본문에 구체적 날짜(N월 N일)를 쓰지 못하게 한다.**
첫 시험에서 모델이 "헌법재판소는 12월 14일 파면했다"라고 썼는데, 실제 파면은 이듬해
4월이었다. 우리가 넘긴 값이 사건 발생일이 아니라 **보도일**이었기 때문이다. 요약문에는
날짜가 없으니 모델이 보도일을 발생일로 바꿔 쓴 것이다. 이런 문장은 페이지 전체의
신뢰를 무너뜨린다. 기간은 장 머리에 이미 표시되고, 날짜는 기사 목록이 각자 달고 나간다.
금지로 끝내지 않고 정규식으로 한 번 더 검사한다(_has_specific_date).
"""
import json

import anthropic

from config import ANTHROPIC_API_KEY
from expression_filter import filter_expression

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

MODEL = "claude-haiku-4-5-20251001"
PREFILL = "{"

SYSTEM = """한국 정치 사건 기록 서비스의 편집자다. 주어진 사건 목록으로 '사안 원고'를 쓴다.

절대 규칙:
1. 주어진 사건 요약에 없는 사실을 만들지 않는다. 날짜·금액·인원·혐의명은 주어진 것만 쓴다.
2. 단정·평가 표현 금지. "부패한", "무능한", "충격적인", "~게이트", "~파문" 같은 말을 쓰지 않는다.
   판단이 필요한 대목은 attribution 으로 쓴다: "검찰은 ~라고 봤다", "~라고 주장했다".
3. 인과를 만들지 않는다. 사건 요약이 두 국면의 인과를 명시하지 않으면
   시간 연결어만 쓴다("그 다음 달", "이듬해"). 명시했을 때만 causal=true 로 표시한다.
4. 장 제목은 명사가 아니라 문장으로 쓴다. 제목만 훑어도 줄거리가 되어야 한다.
   "수사 착수"(X) → "검찰이 전담수사팀을 꾸렸다"(O)
5. 수사·기소 단계는 확정된 사실이 아니다. "기소됐다"는 쓰되 "유죄다"는 쓰지 않는다.
6. slug 는 **영문 소문자·숫자·하이픈만**. 한글·공백·특수문자를 넣지 않는다.
   사안을 특정하는 고유명사를 로마자로 옮긴다(대장동 → daejangdong).
7. **본문에 구체적 날짜를 쓰지 않는다.** "12월 14일", "2024년 12월 3일" 같은 표현 금지.
   주어진 날짜는 보도 시점이라 사건 발생일이 아니다. 기간은 장 머리에 이미 표시된다.
   연도만 필요하면 "2024년"까지는 허용한다. 월·일은 절대 쓰지 않는다.

JSON 만 출력한다. 다른 말을 덧붙이지 않는다."""

import re as _re

# 일(日)까지 특정하는 표기 — 조작 위험이 가장 큰 형태
DAY_RE = _re.compile(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일")
# 월만 특정하는 표기
MONTH_RE = _re.compile(r"(\d{1,2})\s*월(?!\s*간)")
# 입력에 등장하는 숫자 (12·3 의 12, 3 도 잡는다)
NUM_RE = _re.compile(r"\d{1,4}")


def ungrounded_dates(text: str, source: str) -> list[str]:
    """입력에 근거가 없는 날짜 표기를 찾는다.

    날짜를 통째로 금지하면 사안 이름이 "12·3 비상계엄" 인 경우까지 걸린다.
    막아야 하는 건 **없는 날짜를 만드는 것**이지 날짜 자체가 아니다.

    - "N월 M일" : 그 조합이 입력에 나오지 않으면 조작으로 본다 (일 단위는 위험이 크다)
    - "N월"     : N 이 입력 숫자에 없으면 조작으로 본다
    """
    src_nums = set(NUM_RE.findall(source or ""))
    bad: list[str] = []

    for month, day in DAY_RE.findall(text or ""):
        if not _day_in_source(month, day, source):
            bad.append(f"{month}월 {day}일")

    for month in MONTH_RE.findall(text or ""):
        if month not in src_nums:
            bad.append(f"{month}월")
    return bad


def _day_in_source(month: str, day: str, source: str) -> bool:
    """입력이 그 월·일을 실제로 말하고 있는가 ("12월 3일", "12·3", "12-03" 모두 허용)."""
    m, d = int(month), int(day)
    patterns = [
        rf"{m}\s*월\s*{d}\s*일",
        rf"{m}\s*·\s*{d}",
        rf"-0?{m}-0?{d}\b",
        rf"{m}\.0?{d}\b",
    ]
    return any(_re.search(p, source or "") for p in patterns)

SCHEMA_HINT = """{
  "title": "사안 이름. 고유명사 중심, 평가어 금지",
  "slug": "URL 용 로마자 소문자 kebab. 고유명사를 로마자로 옮긴다. 예: daejangdong, gukjeongnongdan, bisang-gyeeom",
  "blurb": "한 문장 요지",
  "lead": ["30초 요약 문단 2~3개"],
  "figures": [{"value": "4,040억", "label": "민간 배당액"}],
  "people": [{"name": "이름", "role": "한 문장 역할", "chapter_positions": [1, 2]}],
  "chapters": [{"position": 1, "title": "문장형 제목", "body": "2~3문장",
                "link": "다음 장으로 잇는 한 줄. 마지막 장은 빈 문자열",
                "causal": false}],
  "outcome": {"label": "결말 한 줄", "description": "두세 문장"} 또는 null
}"""


def _chapter_block(position: int, when: str, grade: str, events: list[dict]) -> str:
    """LLM 에 넘기는 재료.

    **날짜를 넘기지 않는다.** 우리가 가진 건 보도일이고 사건 발생일이 아니다.
    넘기면 모델이 그걸 발생일로 바꿔 쓴다(실제로 그랬다). 순서만 알려 준다.
    """
    lines = [f"[{position}장] 기간 {when} · 근거등급 {grade}"]
    for i, e in enumerate(events, 1):
        stage = e.get("criminal_stage") or "-"
        lines.append(f"  {i}) 단계={stage} 행위자={e.get('actor_name') or '?'} : {e.get('summary') or ''}")
    return "\n".join(lines)


def build_prompt(chapters: list[dict], status: str) -> str:
    blocks = [_chapter_block(c["position"], c["when"], c["grade"], c["events"]) for c in chapters]
    tail = "이 사안은 아직 진행 중이다. outcome 은 null 로 둔다."
    if status == "closed":
        tail = "이 사안은 종결됐다. outcome 을 반드시 채운다. 무죄·혐의없음·사면도 결말이다."
    return (
        "사건 목록:\n" + "\n\n".join(blocks) +
        f"\n\n장은 {len(chapters)}개로 이미 정해졌다. 장을 합치거나 나누지 않는다.\n"
        f"{tail}\n\n이 형식으로 답한다:\n{SCHEMA_HINT}"
    )


SLUG_RE = _re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _safe_slug(value) -> str:
    """LLM 이 준 slug 를 검증한다. 규격을 벗어나면 빈 문자열 — 호출부가 해시로 대체한다."""
    if not isinstance(value, str):
        return ""
    candidate = value.strip().lower().replace("_", "-")
    candidate = _re.sub(r"[^a-z0-9-]", "", candidate).strip("-")
    candidate = _re.sub(r"-{2,}", "-", candidate)
    if 3 <= len(candidate) <= 40 and SLUG_RE.match(candidate):
        return candidate
    return ""


def _clean(text: str) -> str:
    """표현 검수를 통과시킨다. 금지어가 남으면 빈 문자열을 돌려 호출부가 알게 한다."""
    if not isinstance(text, str):
        return ""
    cleaned, changes = filter_expression(text)
    if any("금지 표현" in c or "banned" in c.lower() for c in changes):
        return ""
    return cleaned


def _source_text(chapters: list[dict]) -> str:
    out = []
    for ch in chapters:
        out.append(ch.get("when", ""))
        for e in ch["events"]:
            out.append(e.get("summary") or "")
            out.append((e.get("first_reported_at") or "")[:10])
    return " ".join(out)


def author(chapters: list[dict], status: str) -> dict | None:
    """원고를 쓴다. 실패하면 None — 호출부는 사안을 만들지 않는다."""
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=2000,
            system=SYSTEM,
            messages=[
                {"role": "user", "content": build_prompt(chapters, status)},
                {"role": "assistant", "content": PREFILL},
            ],
        )
        raw = PREFILL + (resp.content[0].text if resp.content else "")
        # 뒤에 설명이 붙어도 JSON 만 떼어낸다
        end = raw.rfind("}")
        data = json.loads(raw[: end + 1] if end > 0 else raw)
    except Exception as e:
        print(f"  [author] 원고 생성 실패: {e}")
        return None

    if not isinstance(data, dict) or not data.get("chapters"):
        print("  [author] 응답 형식 오류")
        return None

    # 표현 검수 — LLM 을 믿지 않고 한 번 더 거른다
    data["title"] = _clean(data.get("title", ""))
    data["slug"] = _safe_slug(data.get("slug"))
    data["blurb"] = _clean(data.get("blurb", ""))
    data["lead"] = [t for t in (_clean(x) for x in data.get("lead", [])) if t]
    for ch in data["chapters"]:
        ch["title"] = _clean(ch.get("title", ""))
        ch["body"] = _clean(ch.get("body", ""))
        ch["link"] = _clean(ch.get("link", ""))
        ch["causal"] = bool(ch.get("causal"))
    if isinstance(data.get("outcome"), dict):
        data["outcome"]["label"] = _clean(data["outcome"].get("label", ""))
        data["outcome"]["description"] = _clean(data["outcome"].get("description", ""))

    if not data["title"] or any(not c["title"] for c in data["chapters"]):
        print("  [author] 표현 검수에서 제목이 걸렸다 — 사안을 만들지 않는다")
        return None

    # 날짜 조작 검사 — 입력에 없는 날짜가 나오면 그 원고는 버린다.
    # 틀린 날짜가 실린 페이지는 없느니만 못하다
    source = _source_text(chapters)
    texts = [data["blurb"], *data["lead"]]
    for ch in data["chapters"]:
        texts += [ch["title"], ch["body"], ch["link"]]
    if isinstance(data.get("outcome"), dict):
        texts += [data["outcome"]["label"], data["outcome"]["description"]]

    bad = [b for t in texts for b in ungrounded_dates(t, source)]
    if bad:
        print(f"  [author] 입력에 없는 날짜 — 폐기: {sorted(set(bad))}")
        return None
    return data
