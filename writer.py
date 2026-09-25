"""집필 — 사람이 읽는 문장만 만든다.

판정(analyzer)과 갈라놓은 이유는 둘이다.

**비용** — 예전에는 한 번의 호출이 분류·근거·헤드라인·요약·예정일정을 다 만들었다.
그런데 분석한 기사의 절반 가까이가 판정 단계에서 버려진다(진영 판정 불가·검증 실패).
버려질 기사의 헤드라인과 요약까지 생성 비용을 내고 있었다.

**품질** — 분류와 집필은 요구가 다르다. 분류는 정확해야 하고 출력이 짧다.
집필은 읽히는 글이어야 하고 출력이 길다. 하나의 `max_tokens=1024` 안에
둘을 밀어 넣으니 잘렸고, 잘리면 JSON 파싱이 깨져 기사가 통째로 버려졌다
(2026-09-25 실측: 130건 중 3건).

여기 오는 기사는 이미 판정을 통과한 것이다. 그래서 무엇을 쓸지 다투지 않고
**어떻게 쓸지**에만 집중한다.
"""
import hashlib
import json

import anthropic

from config import ANTHROPIC_API_KEY
from expression_filter import filter_expression
from model_caps import supports_prefill

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

WRITER_VERSION = "w1"
# 사람이 읽는 문장이다. 분류보다 여기가 체감 품질을 정한다.
MODEL = "claude-sonnet-5"

USAGE = {"calls": 0, "input": 0, "output": 0, "cache_write": 0, "cache_read": 0}
# Sonnet 5 단가 ($/MTok)
_PRICE_IN, _PRICE_OUT = 2.00, 10.00

# ⚠️ Sonnet 5 는 assistant prefill 을 받지 않는다(400 — 2026-09-25 실측).
#    그래서 서두가 붙을 수 있다고 보고 max_tokens 를 넉넉히 주고,
#    응답에서 JSON 덩어리만 떼어낸다. 모델별 차이는 model_caps.py 에 모아 뒀다.


def _system_prompt() -> str:
    return """당신은 한국 정치 뉴스 편집자입니다.
분류는 이미 끝났습니다. 당신은 **읽히는 문장**만 씁니다.

우리는 점수 매기지 않는다. 사회·제도의 반응을 측정만 한다.
그러므로 문장도 판단하지 않는다. 일어난 일을 적는다.

## 표현 규칙 (어기면 문장을 버린다)

- 단정 금지. "뇌물을 받았다" → "뇌물 수수 혐의로 기소됐다"
- 평가 금지. "부패한", "무능한", "비열한", "사악한" 등 사용 금지
- 추측 금지. "~로 보인다", "~할 전망" 은 기사에 그렇게 쓰여 있을 때만
- 기사에 없는 내용은 절대 채워 넣지 않는다. **지어내는 것보다 짧은 게 낫다**
- 진영을 편드는 어휘를 쓰지 않는다. 같은 사건을 진영만 바꿔 써도 문장이
  그대로여야 한다

## headline — 30자 이내

무슨 일이 있었는지 한 줄로 안다. 기사 제목을 베끼지 않는다.
- O "뇌물 수수 혐의 1심 유죄"
- O "공직선거법 위반 벌금 80만원"
- O "법무장관 후보 지명 20일 만에 사퇴"
- X "충격, 또다시 불거진 의혹" — 무슨 일인지 모른다
- X "OOO, 결국…" — 낚시 제목

## summary — 역피라미드로 2~5문장

뉴스 요약은 역피라미드로 쓴다. 기승전결(소설·에세이 구조)로 쓰지 마라.
결론을 아껴두고 배경부터 풀면 안 된다 — 가장 중요한 사실을 첫 문장에 박는다.

순서:
1. **가장 중요한 사실** — 누가 무엇을 했는가. 첫 문장에서 끝난다.
2. **배경·이유** — 왜 그렇게 했는가, 왜 지금 문제가 되는가.
3. **세부** — 금액·날짜·장소·쟁점 등 구체 수치. 기사에 있는 것만.
4. **영향** — 그래서 무엇이 달라지는가. 기사가 말하지 않으면 생략한다.

담아야 할 것: 무엇(What) · 누가(Who) · 왜(Why) · 영향(Impact).
언제·어디서(When/Where)는 그 자체가 쟁점일 때만 넣는다.

분량은 2~5문장. 길이를 채우려고 늘리지 마라 — 핵심이 2문장에 들어가면
2문장이 정답이다.

예시 — 원문 3문장("AI 수요로 신규 공장 설립 발표 / 2028년 완공, 5조 투자 /
글로벌 AI 칩 수요 대응")을 이렇게 압축한다:
  O "삼성전자가 AI 반도체 수요에 대응해 5조 원을 투자, 2028년 완공 목표로
     신규 공장을 짓는다."
  X "최근 반도체 업계는 AI 수요로 술렁이고 있다. 이런 가운데 삼성전자가 …"
     → 배경부터 시작해 첫 문장에 사건이 없다.

또 하나 — 재판 기사:
  O "법원이 정치자금법 위반 혐의로 기소된 OOO 에게 1심에서 징역 1년을
     선고했다. 검찰은 2021년 지방선거 과정에서 5천만 원을 받은 것으로 봤고,
     OOO 는 혐의를 부인해 왔다."
  X "OOO 의 운명이 갈렸다. 지난한 공방 끝에 법원이 입을 열었다."
     → 사건이 없고 문학적이다

## next_branch — 날짜가 확정된 예정 일정이 있을 때만

없으면 null. 억지로 만들지 마라.
- 넣는 것: 선고 기일, 청문회 날짜, 보고서 채택 시한, 표결 예정일, 영장실질심사
- 넣지 않는 것: "조만간", "내달 중", "이르면 다음 주" 같은 미확정 표현.
  "~할 전망", "~할 것으로 보인다" 같은 추측성 전망은 절대 넣지 않는다
- date 는 반드시 YYYY-MM-DD. 기사에 연도가 없으면 기사 발행 연도를 쓴다
- description 은 갈래별 결과를 서술한다.
  예: "시한을 넘기면 대통령이 임명을 강행할 수 있고, 채택되면 즉시 임명
      절차로 넘어간다."

## 출력 (설명 없이 JSON만)

{
  "headline": "30자 이내 한 줄",
  "summary": "역피라미드 2~5문장 한 덩어리",
  "next_branch": {"date": "YYYY-MM-DD", "title": "일정 이름", "description": "갈래별 결과"} 또는 null
}
"""


def prompt_hash() -> str:
    return hashlib.sha256(_system_prompt().encode("utf-8")).hexdigest()[:16]


_PREFILL = '{"headline":'


def _text_of(message) -> str:
    """응답에서 text 블록만 꺼낸다.

    ⚠️ content[0] 을 쓰면 안 된다. Sonnet 5 는 첫 블록으로 ThinkingBlock 을 주고,
       거기엔 .text 가 없다. 2026-09-25 프로덕션 실행에서 집필 28건 중 7건이
       'ThinkingBlock' object has no attribute 'text' 로 실패했고, 그 기사들은
       **원문 기사 제목이 그대로 저장됐다** — AI 헤드라인을 쓰는 이유(저작권)를
       정면으로 뚫었다.
    """
    for block in getattr(message, "content", None) or []:
        if getattr(block, "type", None) == "text":
            return getattr(block, "text", "") or ""
    return ""


def _json_blob(raw: str) -> str:
    """응답에서 JSON 덩어리만 떼어낸다.

    prefill 을 못 쓰는 모델은 ```json 펜스나 한 줄 서두를 붙이기도 한다.
    첫 `{` 부터 마지막 `}` 까지가 본문이다.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    i, j = text.find("{"), text.rfind("}")
    return text[i:j + 1] if i >= 0 and j > i else text


def _record(message) -> None:
    u = getattr(message, "usage", None)
    if u is None:
        return
    USAGE["calls"] += 1
    USAGE["input"] += getattr(u, "input_tokens", 0) or 0
    USAGE["output"] += getattr(u, "output_tokens", 0) or 0
    USAGE["cache_write"] += getattr(u, "cache_creation_input_tokens", 0) or 0
    USAGE["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0


def usage_report() -> str:
    if not USAGE["calls"]:
        return "[집필] 호출 없음"
    cost = (
        USAGE["input"] * _PRICE_IN
        + USAGE["cache_write"] * _PRICE_IN * 1.25
        + USAGE["cache_read"] * _PRICE_IN * 0.10
        + USAGE["output"] * _PRICE_OUT
    ) / 1_000_000
    return (f"[집필] 호출 {USAGE['calls']}회 | 입력 {USAGE['input']:,} "
            f"| 캐시읽기 {USAGE['cache_read']:,} | 출력 {USAGE['output']:,} "
            f"| 추정 ${cost:.4f}")


def write(title: str, content: str, judgment: dict, _retry: bool = True) -> dict | None:
    """헤드라인·요약·예정일정을 쓴다. 실패하면 None.

    호출부는 None 을 받으면 **원문 제목과 본문 앞부분으로 대체**한다.
    글을 못 썼다고 기사를 버리지는 않는다 — 판정은 이미 끝났고, 분류 결과가
    점수와 사건 매칭에 쓰이기 때문이다.
    """
    user = (
        f"제목: {title}\n"
        f"본문: {content}\n\n"
        f"[이미 끝난 판정 — 문장에 반영하되 다시 판단하지 마라]\n"
        f"행위자: {judgment.get('actor_name', '')}\n"
        f"카테고리: {judgment.get('category', '')}\n"
        f"형사 단계: {judgment.get('criminal_stage') or '해당 없음'}\n"
        f"제도 단계: {judgment.get('institutional_stage') or '해당 없음'}\n"
        f"근거 문장: {judgment.get('evidence_sentence', '')}\n"
    )

    prefill = supports_prefill(MODEL)
    messages: list[dict] = [{"role": "user", "content": user}]
    if prefill:
        messages.append({"role": "assistant", "content": _PREFILL})

    try:
        message = client.messages.create(
            model=MODEL,
            max_tokens=1200,
            system=[{
                "type": "text",
                "text": _system_prompt(),
                "cache_control": {"type": "ephemeral"},
            }],
            messages=messages,
        )
        _record(message)

        raw = (_PREFILL if prefill else "") + _text_of(message)
        result = json.loads(_json_blob(raw))
    except json.JSONDecodeError as e:
        print(f"  [writer] JSON 파싱 실패: {e}")
        if _retry:
            return write(title, content, judgment, _retry=False)
        return None
    except Exception as e:
        print(f"  [writer] 집필 실패: {e}")
        if _retry:
            return write(title, content, judgment, _retry=False)
        return None

    if not isinstance(result, dict) or not result.get("headline"):
        print("  [writer] 응답 형식 오류")
        return None

    # 표현 검수 — LLM 을 믿지 않고 한 번 더 거른다
    if result.get("summary"):
        filtered, changes = filter_expression(result["summary"])
        result["summary"] = filtered
        if changes:
            result["expression_changes"] = changes
    if result.get("headline"):
        result["headline"], _ = filter_expression(result["headline"])

    return result
