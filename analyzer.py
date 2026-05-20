"""Claude Haiku API를 사용한 이슈 분석기

- 카테고리 자동 분류
- 심각도 판정
- 진영(camp) 판별
- confidence score 산출
"""
import json
import anthropic
from config import ANTHROPIC_API_KEY, CATEGORIES

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

SYSTEM_PROMPT = """당신은 대한민국 정치 이슈를 분석하는 팩트 기반 분석가입니다.
주어진 뉴스/팩트체크/판결 정보를 분석하여 아래 JSON 형식으로 응답하세요.

카테고리 목록:
- crime: 범죄/사법 (기소, 유죄 판결, 구속, 수사 중)
- corruption: 부정부패/비리 (뇌물, 횡령, 배임, 이권 개입)
- hypocrisy: 위선/이중잣대 (내로남불, 과거 발언 모순)
- slander: 막말/폭언 (비속어, 혐오 발언, 인신공격)
- division: 논란/갈등 조장 (지역, 세대, 성별 갈등)
- policy_fail: 정책 실패 (측정 가능한 국민 피해)
- charity: 선행/봉사 (기부, 봉사, 사비 출연)
- policy_win: 정책 성공 (측정 가능한 국민 이익)
- promise_kept: 공약 이행
- promise_broke: 공약 불이행
- controversial: 논란 정책 (가치 판단이 갈리는 정책)

심각도:
- mild: 경미
- normal: 보통
- severe: 중대
- extreme: 극심

영향 범위:
- individual: 개인
- regional: 지역
- national: 전국
- international: 국제

진영:
- blue: 더불어민주당 계열 (민주당, 열린민주당 등 진보)
- red: 국민의힘 계열 (국민의힘, 개혁신당 등 보수)

반드시 JSON만 응답하세요. 설명 없이 JSON만."""

ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": CATEGORIES},
        "camp": {"type": "string", "enum": ["blue", "red"]},
        "severity": {"type": "string", "enum": ["mild", "normal", "severe", "extreme"]},
        "impact_scope": {"type": "string", "enum": ["individual", "regional", "national", "international"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reasoning": {"type": "string"},
        "category_rationale": {"type": "string"},
        "severity_rationale": {"type": "string"},
        "summary": {"type": "string"},
    },
    "required": [
        "category", "camp", "severity", "impact_scope",
        "confidence", "reasoning", "category_rationale",
        "severity_rationale", "summary",
    ],
}


def analyze_article(title: str, content: str, source: str) -> dict | None:
    """기사/이슈를 분석하여 카테고리, 심각도, 진영 등을 판별한다."""
    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": f"""아래 정치 이슈를 분석하세요.

제목: {title}
내용: {content}
출처: {source}

JSON 형식으로 응답:
{{
  "category": "카테고리",
  "camp": "blue 또는 red",
  "severity": "심각도",
  "impact_scope": "영향 범위",
  "confidence": 0.0~1.0,
  "reasoning": "분석 근거",
  "category_rationale": "카테고리 판단 이유",
  "severity_rationale": "심각도 판단 이유",
  "summary": "2-3문장 요약"
}}""",
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

        return result

    except json.JSONDecodeError as e:
        print(f"[analyzer] JSON 파싱 실패: {e}")
        return None
    except Exception as e:
        print(f"[analyzer] 분석 실패: {e}")
        return None


if __name__ == "__main__":
    # 테스트
    result = analyze_article(
        title="A 의원, 뇌물 수수 혐의로 기소",
        content="검찰이 국민의힘 소속 A 의원을 건설업자로부터 5천만원을 수수한 혐의로 불구속 기소했다.",
        source="대한민국 법원",
    )
    if result:
        print(json.dumps(result, ensure_ascii=False, indent=2))
