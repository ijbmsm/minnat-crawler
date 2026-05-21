"""Claude Haiku API를 사용한 이슈 분석기 v3

3단계 분석 + Chain-of-Verification 자기검증 + 입법 5단계 결정론적 판정
"""
import json
import anthropic
from config import ANTHROPIC_API_KEY, CATEGORIES, detect_legislative_stage

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def build_system_prompt(politicians_map: dict[str, str]) -> str:
    """정치인 DB를 주입한 시스템 프롬프트를 생성한다."""

    pol_lines = "\n".join(
        f"  - {name}: {camp}" for name, camp in sorted(politicians_map.items())
    )

    return f"""당신은 한국 정치 기사 분류 분석가입니다.

## 규칙 1: 행위자(actor) 추출
- "행위를 직접 수행한 사람"을 찾아라. "비판받는 사람"이 아니다.
- "이재명이 막말 논란" → actor=이재명 (이재명이 막말을 했음)
- "이재명, 막말 비판받아" → actor=이재명 (행위 주체)
- "이재명을 비판한 여당 대변인" → actor=여당 대변인
- 헷갈리면 lead 문장의 동사를 찾고 그 동사의 주어를 추출.

## 규칙 2: camp 결정 (절대 규칙)
camp는 오직 행위자의 "소속 정당"으로만 결정. 아래 DB 참조:

{pol_lines}

- DB에 있으면 해당 camp 사용
- DB에 없지만 더불어민주당/열린민주당/조국혁신당 → blue
- DB에 없지만 국민의힘/개혁신당 → red
- 무소속/제3정당/판단 불가 → camp을 null로
- 기사 논조, 비판 방향, 여론은 camp과 무관

## 규칙 3: 카테고리 분류

감점:
- crime: 기소, 유죄 판결, 구속, 수사 개시 (수사기관 발표 필요)
- corruption: 뇌물, 횡령, 배임 (수사/판결 근거)
- hypocrisy: 과거 발언과 현재 행동의 명백한 모순 (팩트체크 확인)
- slander: 공식 석상 비속어/혐오/인신공격 (영상/기록)
- division: 지역/세대/성별 갈등 의도적 조장 (발언 원문)
- policy_fail: 정책 시행 후 측정 가능한 국민 피해
- promise_broke: 공식 평가에서 불이행 판정

가점 — 입법 5단계:
- bill_proposed: 법안 발의/제안/제출 (가중치 1)
- bill_committee: 위원회/소위 통과 (가중치 3)
- bill_plenary: 국회 본회의 가결/통과 (가중치 6)
- bill_promulgated: 법률 공포 (가중치 8)
- bill_enforced: 법률 시행/발효 (가중치 10)

가점 — 기타:
- promise_kept: 공식 이행 판정
- charity: 기부/봉사 (금액 확인)

기타:
- controversial: 찬반 양론 정책

## 규칙 4: 발언 ≠ 성과
다음은 입법 성과가 아니다:
- 정치인의 발언, 입장 표명, 약속, 비판, 논평
- "~하겠다", "추진하겠다", "검토 중"
→ 이런 경우 controversial로 분류

## 규칙 5: 자기검증 (Chain-of-Verification)
출력 전에 스스로 답하라:
Q1: actor의 진영을 반대로 바꾸면 같은 카테고리·심각도가 나오는가?
Q2: actor 자리에 다른 사람(비판한 사람 등)을 넣으면 같은 결과가 나오는가?
하나라도 "아니오"면 confidence를 0.6 이하로.

## 심각도
mild(경미) / normal(보통) / severe(중대) / extreme(극심)

## 영향 범위
individual(개인) / regional(지역) / national(전국) / international(국제)

## 출력 (JSON만)
{{
  "actor_name": "행위자 이름",
  "actor_party": "소속 정당명",
  "camp": "blue|red|null",
  "category": "카테고리",
  "severity": "심각도",
  "impact_scope": "영향 범위",
  "confidence": 0.0~1.0,
  "reasoning": "전체 판단 근거",
  "camp_reasoning": "camp 판단 근거",
  "category_reasoning": "카테고리 판단 근거",
  "severity_reasoning": "심각도 판단 근거",
  "is_actionable_result": true/false,
  "evidence_sentence": "근거가 되는 기사 원문 1문장",
  "summary": "이슈 요약 (2-3문장)"
}}"""


def analyze_article(
    title: str,
    content: str,
    source: str,
    politicians_map: dict[str, str] | None = None,
) -> dict | None:
    """기사를 분석하여 분류한다.

    입법 관련 기사는 키워드 사전으로 결정론적 판정을 우선 시도한다.
    """
    if politicians_map is None:
        politicians_map = {}

    # ── 결정론적 입법 단계 판정 (LLM 불필요) ──
    text = f"{title} {content}"
    leg_stage = detect_legislative_stage(text)

    # ── LLM 분석 ──
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

        text_resp = message.content[0].text.strip()

        # JSON 파싱
        if text_resp.startswith("```"):
            text_resp = text_resp.split("\n", 1)[1]
            text_resp = text_resp.rsplit("```", 1)[0]

        result = json.loads(text_resp)

        # 필수 필드 검증
        required = {"category", "camp", "severity", "impact_scope", "confidence"}
        if not required.issubset(result.keys()):
            print(f"[analyzer] 필수 필드 누락: {required - result.keys()}")
            return None

        # camp 검증
        if result["camp"] not in ("blue", "red"):
            print(f"[analyzer] 진영 판별 불가 ({result['camp']}): {title[:40]}")
            return None

        # 카테고리 검증 — 기존 policy_win은 입법 단계로 오버라이드
        if result["category"] == "policy_win":
            if leg_stage:
                result["category"] = leg_stage
            else:
                # LLM이 policy_win이라 했는데 키워드에 안 걸림 → controversial
                result["category"] = "controversial"
                result["confidence"] = min(result.get("confidence", 0.5), 0.5)

        if result["category"] not in CATEGORIES:
            print(f"[analyzer] 잘못된 카테고리: {result['category']}")
            return None

        return result

    except json.JSONDecodeError as e:
        print(f"[analyzer] JSON 파싱 실패: {e}")
        return None
    except Exception as e:
        print(f"[analyzer] 분석 실패: {e}")
        return None
