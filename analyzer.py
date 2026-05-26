"""Claude Haiku API 이슈 분석기 v1.1

v1.1 카테고리: 점수 6 + archive 5 + 입법 5
형사 단계 판정 포함
"""
import json
import anthropic
from config import ANTHROPIC_API_KEY, ALL_CATEGORIES, detect_legislative_stage
from expression_filter import filter_expression

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def build_system_prompt(politicians_map: dict[str, str]) -> str:
    pol_lines = "\n".join(f"  - {name}: {camp}" for name, camp in sorted(politicians_map.items()))

    return f"""당신은 한국 정치 뉴스 분류기입니다.
우리는 점수 매기지 않는다. 사회·제도의 반응을 측정만 한다.

## 행위자 추출
- "행위를 직접 수행한 사람"을 찾아라. "비판 대상"이 아니다.
- 헷갈리면 lead 문장의 동사 주어를 추출.

## camp 결정 (절대 규칙)
오직 행위자의 소속 정당으로만 결정. 아래 DB 참조:
{pol_lines}
- 무소속/제3정당/판단 불가 → camp=null

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
{{
  "actor_name": "행위자",
  "actor_party": "소속 정당",
  "camp": "blue|red|null",
  "category": "카테고리",
  "criminal_stage": "형사단계|null",
  "confidence": 0.0~1.0,
  "reasoning": "판단 근거",
  "camp_reasoning": "camp 판단 근거",
  "category_reasoning": "카테고리 판단 근거",
  "evidence_sentence": "근거 기사 원문 1문장",
  "headline": "핵심 한 줄 (30자 이내, 무슨 사건인지 바로 알 수 있게. 예: '뇌물 수수 혐의 1심 유죄', '공직선거법 위반 벌금형')",
  "summary": "이슈 요약 2-3문장 (단정·평가 표현 없이)"
}}"""


def analyze_article(
    title: str,
    content: str,
    source: str,
    politicians_map: dict[str, str] | None = None,
) -> dict | None:
    if politicians_map is None:
        politicians_map = {}

    # 입법 기사 → 키워드 결정론적 판정 (LLM은 actor/camp만)
    text = f"{title} {content}"
    leg_stage = detect_legislative_stage(text)

    system_prompt = build_system_prompt(politicians_map)

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

        text_resp = message.content[0].text.strip()
        if text_resp.startswith("```"):
            text_resp = text_resp.split("\n", 1)[1]
            text_resp = text_resp.rsplit("```", 1)[0]

        result = json.loads(text_resp)

        # 필수 필드
        required = {"category", "camp", "confidence"}
        if not required.issubset(result.keys()):
            print(f"[analyzer] 필수 필드 누락: {required - result.keys()}")
            return None

        # camp 검증
        if result["camp"] not in ("blue", "red"):
            print(f"[analyzer] 진영 판별 불가 ({result['camp']}): {title[:40]}")
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

        return result

    except json.JSONDecodeError as e:
        print(f"[analyzer] JSON 파싱 실패: {e}")
        return None
    except Exception as e:
        print(f"[analyzer] 분석 실패: {e}")
        return None
