"""Claude Haiku API 이슈 분석기 v1.1

v1.1 카테고리: 점수 6 + archive 5 + 입법 5
형사 단계 판정 포함
"""
import json
from datetime import datetime

import anthropic
from actor_resolver import normalize_actor_name, resolve_actor
from config import ANTHROPIC_API_KEY, ALL_CATEGORIES, detect_legislative_stage
from expression_filter import filter_expression

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# 왜 버려졌는지 집계한다. 전부 조용히 None을 반환하면 "저장 0건"의 원인을 알 수 없다.
SKIP_STATS: dict[str, int] = {}

# 토큰 사용량 누적. 비용이 보이지 않으면 아무도 초과를 눈치채지 못한다.
USAGE = {"calls": 0, "input": 0, "output": 0, "cache_write": 0, "cache_read": 0}

# Haiku 4.5 단가 ($/MTok). 캐시 쓰기 1.25x, 캐시 읽기 0.1x
_PRICE_IN, _PRICE_OUT = 1.00, 5.00


def _skip(reason: str) -> None:
    SKIP_STATS[reason] = SKIP_STATS.get(reason, 0) + 1


def _record_usage(message) -> None:
    u = getattr(message, "usage", None)
    if u is None:
        return
    USAGE["calls"] += 1
    USAGE["input"] += getattr(u, "input_tokens", 0) or 0
    USAGE["output"] += getattr(u, "output_tokens", 0) or 0
    USAGE["cache_write"] += getattr(u, "cache_creation_input_tokens", 0) or 0
    USAGE["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0


def usage_report() -> str:
    """이번 실행의 토큰 사용량과 추정 비용을 한 줄로 요약한다."""
    if not USAGE["calls"]:
        return "[비용] LLM 호출 없음"
    cost = (
        USAGE["input"] * _PRICE_IN
        + USAGE["cache_write"] * _PRICE_IN * 1.25
        + USAGE["cache_read"] * _PRICE_IN * 0.10
        + USAGE["output"] * _PRICE_OUT
    ) / 1_000_000
    warn = ""
    if USAGE["calls"] > 1 and USAGE["cache_read"] == 0:
        warn = "  ⚠ 캐시 미적중 — 시스템 프롬프트가 최소 캐시 길이 미만일 수 있음"
    return (
        f"[비용] 호출 {USAGE['calls']}회 | 입력 {USAGE['input']:,} "
        f"| 캐시쓰기 {USAGE['cache_write']:,} | 캐시읽기 {USAGE['cache_read']:,} "
        f"| 출력 {USAGE['output']:,} | 추정 ${cost:.4f}{warn}"
    )


def build_system_prompt() -> str:
    """시스템 프롬프트를 만든다.

    정치인 명단은 일부러 넣지 않는다. 진영(camp)은 추론이 아니라 조회이며,
    DB 조회로 결정론적으로 정한다(resolve_camp). 명단을 프롬프트에 넣으면
    ① 명단이 커질수록 매 호출 비용이 선형으로 늘고
    ② LLM이 진영을 "판단"하면서 환각 오분류가 생긴다.
    프롬프트가 고정되므로 프롬프트 캐시 적중률도 올라간다.
    """
    return """당신은 한국 정치 뉴스 분류기입니다.
우리는 점수 매기지 않는다. 사회·제도의 반응을 측정만 한다.

## 행위자 추출
- "행위를 직접 수행한 사람"을 찾아라. "비판 대상"이 아니다.
- 헷갈리면 lead 문장의 동사 주어를 추출.
- actor_name은 기사에 나온 그대로의 인명만. 직책·수식어를 붙이지 말 것 (O: "홍길동", X: "홍길동 의원").
- actor_party는 기사 본문에 소속 정당이 명시된 경우에만 적고, 없으면 null. 추측 금지.

## 카테고리 v1.1

점수 카테고리 (공식 처분만):
- criminal_conviction: 형사 유죄·기소·기소유예 (criminal_stage 필수)
- civil_judgment: 민사 소송 패소
- ethics_violation: 윤리위·선관위 처분
- factcheck_false: IFCN 인증 매체의 false 판정
- self_admission: 본인 공식 시인·사과
- official_misconduct: 감사원·국정감사 적발

Archive 카테고리 (점수 X, 기록만):
- controversial_statement: 막말·논란 발언 (원문 보존, 우리가 판단 X)
- policy_record: 정책·발의·표결 이력
- attendance_record: 출석률
- media_coverage: 보도 모음
- politician_sns: 본인 SNS
- social_controversy: 사회 이슈가 정치권으로 확산된 경우 (예: 기업 논란→불매운동→고발, 재난→정부 책임론, 사회 갈등→정치 쟁점화). 정치인이 직접 행위자가 아니라 "관련된" 사건일 때 사용.

입법 기록 (점수 X):
- bill_proposed/bill_committee/bill_plenary/bill_promulgated/bill_enforced

## criminal_stage (criminal_conviction일 때 필수)
investigation(수사), indicted(기소), suspended_indictment(기소유예),
guilty_1st(1심유죄), guilty_2nd(2심유죄), confirmed(대법확정),
pardoned(사면), not_guilty(무죄), no_charges(혐의없음), dismissed(각하)

## institutional_stage (국회·헌재 절차가 있을 때만. 없으면 null)
형사 절차와 **다른 축**이다. 한 기사가 둘 다 가질 수 있다.
impeachment_proposed(탄핵소추안 발의), impeachment_passed(국회 가결→직무정지),
impeachment_upheld(헌재 인용→파면), impeachment_rejected(헌재 기각·각하),
censure_passed(해임건의안 가결), inquiry_launched(국정조사·특검 발동)

⚠ 탄핵·해임·국정조사를 criminal_stage 로 적지 마라. 그건 형사 절차가 아니다.
  "헌재 탄핵 인용 파면" → criminal_stage=null, institutional_stage=impeachment_upheld
  "국회 탄핵소추안 가결" → criminal_stage=null, institutional_stage=impeachment_passed

## 절대 규칙
- 막말·위선·정책 호불호 → controversial_statement (점수 X)
- 법안 통과 → bill_plenary (점수 X)
- 표결 찬반 → policy_record (점수 X)
- 발언·약속·계획 → controversial_statement 또는 policy_record
- 공식 처분(검찰·법원·윤리위·감사원·팩트체크)만 점수 카테고리
- 기업 논란·사회 이슈가 정치권으로 번진 경우 → social_controversy (점수 X)
- 정치인이 직접 행위자가 아닌 사회 이슈 → social_controversy (media_coverage 아님)

## criminal_conviction 판정 엄격 규칙 (매우 중요)
criminal_conviction은 다음 조건을 모두 만족해야 한다:
1. 기사가 "실제로 판결/기소/처분이 내려졌다"는 내용이어야 한다
2. "만약 유죄라면", "유죄 땐", "유죄 가능성" 등 가정문은 criminal_conviction이 아니다 → media_coverage
3. "구형"은 기소(indicted)가 맞지만, "선고"와 다르다. 구형은 검찰 요청이고, 판결은 법원 결정이다
4. 기사 주제가 형사 사건이 아니라 다른 주제(인물 소개, 정책, 선거)인데 과거 전과를 언급만 한 경우 → media_coverage
5. 확정 동사가 있어야 한다: "선고했다", "판결했다", "확정됐다", "기소했다", "기소됐다"
6. 없으면 confidence를 0.5 이하로 낮추고 media_coverage로 분류하라

## 자기검증
Q1: actor 진영을 반대로 바꾸면 같은 카테고리가 나오는가?
Q2: 이것이 정말 공식 처분인가, 아니면 보도/발언일 뿐인가?
하나라도 "아니오"면 confidence를 0.6 이하로.

## 표현 규칙
단정 표현 금지. "뇌물을 받았다" → "뇌물 수수 혐의로 기소됐다"
평가 표현 금지. "부패한", "무능한" 등 사용 금지.

## 분류 예시
- "이재명 대장동 배임 1심 유죄" → criminal_conviction, guilty_1st, blue
- "스타벅스 탱크데이 불매 → 대통령 고발" → social_controversy, blue
- "GTX 철근 누락 국감 질의" → official_misconduct, red
- "정청래 '개XX' 발언 논란" → controversial_statement, blue
- "국민연금법 본회의 통과" → bill_plenary, camp은 발의자 기준
- "감사원, OO부 특정감사 결과 발표" → official_misconduct, 해당 부처 장관 camp

## JSON 출력 (설명 없이 JSON만)
아래 키 순서 그대로 쓸 것. 근거를 먼저 쓰고 그 근거에 따라 결론을 적는다.
{
  "reasoning": "기사에서 누가 무엇을 했는지 정리한 판단 근거",
  "actor_name": "행위자 인명만",
  "actor_party": "기사에 명시된 소속 정당 (없으면 null)",
  "category_reasoning": "카테고리 판단 근거",
  "category": "카테고리",
  "criminal_stage": "형사단계|null",
  "institutional_stage": "제도단계|null",
  "confidence": 0.0~1.0,
  "evidence_sentence": "근거 기사 원문 1문장",
  "headline": "핵심 한 줄 (30자 이내, 무슨 사건인지 바로 알 수 있게. 예: '뇌물 수수 혐의 1심 유죄', '공직선거법 위반 벌금형')",
  "summary": "아래 '요약 작성 규칙'(역피라미드)을 따른 2~5문장 한 덩어리",
  "next_branch": {"date": "YYYY-MM-DD", "title": "예정 일정 이름", "description": "갈래별로 어떻게 되는지"} 또는 null
}

## 요약 작성 규칙 (summary)
뉴스 요약은 **역피라미드**로 쓴다. 기승전결(소설·에세이 구조)로 쓰지 마라.
결론을 아껴두고 배경부터 풀면 안 된다 — 가장 중요한 사실을 첫 문장에 박는다.

순서:
1. **가장 중요한 사실** — 누가 무엇을 했는가. 첫 문장에서 끝난다.
2. **배경·이유** — 왜 그렇게 했는가, 왜 지금 문제가 되는가.
3. **세부** — 금액·날짜·장소·쟁점 등 구체 수치. 기사에 있는 것만.
4. **영향** — 그래서 무엇이 달라지는가. 기사가 말하지 않으면 생략한다.

담아야 할 것: 무엇(What) · 누가(Who) · 왜(Why) · 영향(Impact).
언제·어디서(When/Where)는 그 자체가 쟁점일 때만 넣는다.

분량은 2~5문장. 길이를 채우려고 늘리지 마라 — 핵심이 2문장에 들어가면 2문장이 정답이다.
기사에 없는 내용은 절대 채워 넣지 않는다. 지어내는 것보다 짧은 게 낫다.
단정·평가 표현 금지는 그대로 적용된다.

예시 — 원문 3문장("AI 수요로 신규 공장 설립 발표 / 2028년 완공, 5조 투자 / 글로벌 AI
칩 수요 대응")을 이렇게 압축한다:
  O "삼성전자가 AI 반도체 수요에 대응해 5조 원을 투자, 2028년 완공 목표로 신규 공장을 짓는다."
  X "최근 반도체 업계는 AI 수요로 술렁이고 있다. 이런 가운데 삼성전자가 …"
    → 배경부터 시작해 첫 문장에 사건이 없다.

## next_branch (예정 일정)
기사에 **날짜가 확정된 예정 일정**이 있을 때만 채운다. 없으면 null.
- 넣는 것: 선고 기일, 청문회 날짜, 보고서 채택 시한, 표결 예정일, 영장실질심사 날짜
- 넣지 않는 것: "조만간", "내달 중", "이르면 다음 주" 같은 미확정 표현.
  그리고 "~할 전망", "~할 것으로 보인다" 같은 추측성 전망은 절대 넣지 않는다.
- date 는 반드시 YYYY-MM-DD. 기사에 연도가 없으면 기사 발행 연도를 쓴다.
- description 은 갈래별 결과를 서술한다. 예: "시한을 넘기면 대통령이 임명을 강행할 수 있고,
  채택되면 즉시 임명 절차로 넘어간다."
"""


# 추측성 전망은 예정 일정이 아니다 — 날짜가 있어도 버린다
_SPECULATIVE = (
    "전망", "예상", "관측", "가능성", "보인다", "보이며", "할 듯", "할듯",
    "조만간", "이르면", "늦어도", "검토 중", "추진 중",
)


def sanitize_next_branch(raw, published_at: str | None = None) -> dict | None:
    """LLM 이 준 next_branch 를 검증한다. 확정 일정만 통과시킨다.

    화면에 "다음 분기점" 으로 나가는 값이라, 추측이 섞이면 서비스 원칙
    ("추측·평가·단정 표현 금지")을 정면으로 어긴다. 의심스러우면 버린다.
    """
    if not isinstance(raw, dict):
        return None

    date = str(raw.get("date") or "").strip()
    title = str(raw.get("title") or "").strip()
    desc = str(raw.get("description") or "").strip()

    if not date or not title:
        return None

    try:
        when = datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return None

    # 과거 일정은 "다음 분기점" 이 아니다
    if published_at:
        try:
            base = datetime.fromisoformat(published_at.replace("Z", "+00:00")).replace(tzinfo=None)
            if when.date() < base.date():
                return None
        except (ValueError, AttributeError):
            pass

    # 너무 먼 미래는 확정 일정으로 보기 어렵다
    if (when - datetime.now()).days > 400:
        return None

    blob = f"{title} {desc}"
    if any(word in blob for word in _SPECULATIVE):
        return None

    out = {"date": date, "title": title[:80]}
    if desc:
        out["description"] = desc[:300]
    return out


def resolve_camp(
    actor_name: str,
    actor_party: str | None,
    politicians_map: dict[str, str],
) -> tuple[str | None, str]:
    """진영을 DB 조회로 결정한다. LLM의 추론에 맡기지 않는다.

    Returns:
        (camp, 판단 근거 문장). 판정 불가면 (None, 사유).
    """
    raw = (actor_name or "").strip()
    if not raw:
        return None, "행위자를 특정하지 못함"

    name = raw if raw in politicians_map else normalize_actor_name(raw)
    camp = politicians_map.get(name)
    if not camp:
        return None, f"'{raw}'이(가) 정치인 DB에 없음 (무소속·제3정당·비정치인 포함)"

    party = (actor_party or "").strip()
    if party and party not in ("null", "None"):
        return camp, f"정치인 DB 조회: {name} → {camp} (기사 명시 소속: {party})"
    return camp, f"정치인 DB 조회: {name} → {camp}"


def analyze_article(
    title: str,
    content: str,
    source: str,
    politicians_map: dict[str, str] | None = None,
    published_at: str | None = None,
    politicians_positions: dict[str, str] | None = None,
) -> dict | None:
    if politicians_map is None:
        politicians_map = {}
    if politicians_positions is None:
        politicians_positions = {}

    # 입법 기사 → 키워드 결정론적 판정 (LLM은 actor/camp만)
    text = f"{title} {content}"
    leg_stage = detect_legislative_stage(text)

    system_prompt = build_system_prompt()

    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=[{
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{
                "role": "user",
                "content": f"제목: {title}\n내용: {content}\n출처: {source}",
            }],
        )

        _record_usage(message)

        text_resp = message.content[0].text.strip()
        if text_resp.startswith("```"):
            text_resp = text_resp.split("\n", 1)[1]
            text_resp = text_resp.rsplit("```", 1)[0]

        result = json.loads(text_resp)

        # 필수 필드
        required = {"category", "confidence"}
        if not required.issubset(result.keys()):
            print(f"[analyzer] 필수 필드 누락: {required - result.keys()}")
            _skip("필수 필드 누락")
            return None

        # 행위자를 먼저 확정한다 — camp·직책 가중치·사건 매칭이 전부 이 이름에 매달려 있다.
        # 본문에 없는 이름은 LLM 이 약칭을 잘못 푼 것이므로 원문에서 다시 찾는다.
        actor, actor_note = resolve_actor(
            result.get("actor_name", ""), f"{title}\n{content}", politicians_positions
        )
        result["actor_name"] = actor
        if actor_note:
            result["actor_correction"] = actor_note
            print(f"  [analyzer] {actor_note}")

        # camp는 LLM이 아니라 정치인 DB 조회로 결정한다
        camp, camp_reason = resolve_camp(
            actor, result.get("actor_party"), politicians_map
        )
        result["camp"] = camp
        result["camp_reasoning"] = camp_reason
        if camp is None:
            _skip("진영 판정 불가(정치인 DB 미등재)")
            return None

        # 카테고리 검증
        if result["category"] not in ALL_CATEGORIES:
            # 레거시 카테고리 매핑
            legacy_map = {
                "crime": "criminal_conviction",
                "corruption": "criminal_conviction",
                "slander": "controversial_statement",
                "hypocrisy": "controversial_statement",
                "division": "controversial_statement",
                "policy_fail": "policy_record",
                "policy_win": "policy_record",
                "promise_kept": "policy_record",
                "promise_broke": "policy_record",
                "charity": "policy_record",
                "controversial": "controversial_statement",
            }
            mapped = legacy_map.get(result["category"])
            if mapped:
                result["category"] = mapped
            else:
                print(f"[analyzer] 잘못된 카테고리: {result['category']}")
                _skip("카테고리 불일치")
                return None

        # 입법 키워드 오버라이드
        if leg_stage and result["category"] in ("policy_record", "bill_proposed"):
            result["category"] = leg_stage

        # 표현 필터 적용
        if result.get("summary"):
            filtered_summary, changes = filter_expression(result["summary"])
            result["summary"] = filtered_summary
            if changes:
                result["expression_changes"] = changes

        # 예정 일정 — 확정된 것만 남긴다
        result["next_branch"] = sanitize_next_branch(result.get("next_branch"), published_at)

        return result

    except json.JSONDecodeError as e:
        print(f"[analyzer] JSON 파싱 실패: {e}")
        _skip("JSON 파싱 실패")
        return None
    except Exception as e:
        print(f"[analyzer] 분석 실패: {e}")
        _skip(f"API 오류({type(e).__name__})")
        return None
