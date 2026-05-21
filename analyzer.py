"""Claude Haiku API를 사용한 이슈 분석기

3단계 분석: 행위자 추출 → 소속 조회 → 분류/판정
"""
import json
import anthropic
from config import ANTHROPIC_API_KEY, CATEGORIES

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def build_system_prompt(politicians_map: dict[str, str]) -> str:
    """정치인 DB를 주입한 시스템 프롬프트를 생성한다."""

    # 정치인 목록을 문자열로
    pol_lines = "\n".join(
        f"  - {name}: {camp}" for name, camp in sorted(politicians_map.items())
    )

    return f"""당신은 대한민국 정치 뉴스를 분류하는 팩트 분석기입니다.
아래 단계를 반드시 순서대로 수행하세요.

## STEP 1: 행위자(actor) 추출
기사에서 "행위를 직접 수행한 사람"을 찾으세요.
- "비판받는 사람"이 아니라 "행위를 한 사람"입니다.
- 예: "이재명이 '사람의 탈 쓰고'라고 발언" → 행위자 = 이재명 (발언을 한 사람)
- 예: "검찰이 A의원을 기소" → 행위자 = A의원 (기소 대상)
- 예: "GTX 철근 누락" → 행위자 = 해당 사업 관할 기관/정치인

## STEP 2: camp 결정 (절대 규칙)
camp는 오직 행위자의 "소속 정당"으로만 결정합니다.
아래 정치인 DB를 참조하세요:

{pol_lines}

규칙:
- DB에 있는 정치인 → 해당 camp 사용
- DB에 없지만 더불어민주당/열린민주당/조국혁신당 소속 → blue
- DB에 없지만 국민의힘/개혁신당 소속 → red
- 무소속/제3정당/판단 불가 → camp을 null로 설정 (blue/red 중 하나를 억지로 선택하지 마세요)
- 기사 논조, 비판 방향, 여론은 camp 결정에 영향을 주지 않습니다.

## STEP 3: 카테고리 분류

감점 카테고리:
- crime: 기소, 유죄 판결, 구속, 수사 개시 (수사기관 공식 발표 필요)
- corruption: 뇌물, 횡령, 배임, 이권 개입 (수사/판결 근거 필요)
- hypocrisy: 과거 발언과 현재 행동의 명백한 모순 (팩트체크 확인된 것만)
- slander: 공식 석상에서의 비속어, 혐오 발언, 인신공격 (영상/기록 확인)
- division: 지역/세대/성별 갈등을 의도적으로 조장하는 발언 (발언 원문 기준)
- policy_fail: 정책 시행 후 측정 가능한 국민 피해 발생 (통계/보고서 근거)
- promise_broke: 선관위 또는 공식 평가에서 불이행 판정

가점 카테고리:
- policy_win: 법안 국회 본회의 통과, 또는 정책 시행 후 측정 가능한 국민 이익
- promise_kept: 선관위 또는 공식 평가에서 이행 판정
- charity: 기부, 봉사 활동 (금액/규모 확인 가능)

기타:
- controversial: 가치 판단이 갈리는 정책 (찬반 양론 존재)

## STEP 4: policy_win / policy_fail 판정 (엄격)
다음은 policy_win이 절대 아닙니다:
- 법안 "발의", "제안", "추진", "계획", "검토 중"
- 정치인의 발언, 입장 표명, 약속
- 공약 "발표"

policy_win이 되려면:
- 법안이 국회 본회의를 "통과/가결/의결"했거나
- 정책이 실제로 "시행/집행"되어 수혜자가 존재하거나
- 측정 가능한 성과 데이터가 있어야 합니다

마찬가지로 policy_fail은 정책이 실제 시행된 후 피해가 발생한 경우만 해당합니다.

위 조건에 맞지 않으면 controversial로 분류하세요.

## 심각도
- mild: 경미 (일회성, 파급력 작음)
- normal: 보통
- severe: 중대 (국민 생활에 직접 영향)
- extreme: 극심 (헌정 질서, 대규모 인명/재산 피해)

## 영향 범위
- individual: 개인
- regional: 지역
- national: 전국
- international: 국제

## 출력 (JSON만, 설명 없이)
{{
  "actor_name": "행위자 이름",
  "actor_party": "소속 정당명",
  "camp": "blue|red|null",
  "category": "카테고리",
  "severity": "심각도",
  "impact_scope": "영향 범위",
  "confidence": 0.0~1.0,
  "reasoning": "전체 판단 근거 (2-3문장)",
  "camp_reasoning": "camp 판단 근거 (행위자 + 소속 기준)",
  "category_reasoning": "카테고리 판단 근거",
  "severity_reasoning": "심각도 판단 근거",
  "is_actionable_result": true/false,
  "summary": "이슈 요약 (2-3문장)"
}}"""


def analyze_article(
    title: str,
    content: str,
    source: str,
    politicians_map: dict[str, str] | None = None,
) -> dict | None:
    """기사/이슈를 분석하여 카테고리, 심각도, 진영 등을 판별한다."""
    if politicians_map is None:
        politicians_map = {}

    system_prompt = build_system_prompt(politicians_map)

    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=system_prompt,
            messages=[
                {
                    "role": "user",
                    "content": f"제목: {title}\n내용: {content}\n출처: {source}",
                }
            ],
        )

        text = message.content[0].text.strip()

        # JSON 파싱 (코드블록 제거)
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
            text = text.rsplit("```", 1)[0]

        result = json.loads(text)

        # 필수 필드 검증
        required = {"category", "camp", "severity", "impact_scope", "confidence"}
        if not required.issubset(result.keys()):
            print(f"[analyzer] 필수 필드 누락: {required - result.keys()}")
            return None

        # 카테고리 검증
        if result["category"] not in CATEGORIES:
            print(f"[analyzer] 잘못된 카테고리: {result['category']}")
            return None

        # camp이 null이면 분류 불가 → 스킵
        if result["camp"] not in ("blue", "red"):
            print(f"[analyzer] 진영 판별 불가 ({result['camp']}): {title[:40]}")
            return None

        return result

    except json.JSONDecodeError as e:
        print(f"[analyzer] JSON 파싱 실패: {e}")
        return None
    except Exception as e:
        print(f"[analyzer] 분석 실패: {e}")
        return None
