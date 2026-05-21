"""민낯 크롤러 설정 v2"""
import os
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

# ── 카테고리 정의 (v2: policy_win 5단계 분해) ──
CATEGORIES = [
    # 감점
    "crime", "corruption", "hypocrisy", "slander", "division",
    "policy_fail", "promise_broke",
    # 가점 — 입법 5단계
    "bill_proposed", "bill_committee", "bill_plenary", "bill_promulgated", "bill_enforced",
    # 가점 — 기타
    "promise_kept", "charity",
    # 점수 미반영
    "controversial",
]

# 심각도 배수
SEVERITY_MULTIPLIER = {
    "mild": 1.0,
    "normal": 1.5,
    "severe": 2.0,
    "extreme": 3.0,
}

# 영향 범위 배수
IMPACT_MULTIPLIER = {
    "individual": 0.5,
    "regional": 0.8,
    "national": 1.0,
    "international": 1.2,
}

# 카테고리별 기본 가중치
CATEGORY_WEIGHT = {
    # 감점
    "crime": 10,
    "corruption": 9,
    "policy_fail": 8,
    "promise_broke": 7,
    "hypocrisy": 6,
    "division": 5,
    "slander": 4,
    # 가점 — 입법 5단계
    "bill_proposed": 1,
    "bill_committee": 3,
    "bill_plenary": 6,
    "bill_promulgated": 8,
    "bill_enforced": 10,
    # 가점 — 기타
    "promise_kept": 6,
    "charity": 5,
    # 미반영
    "controversial": 0,
}

# 가점 카테고리
POSITIVE_CATEGORIES = {
    "bill_proposed", "bill_committee", "bill_plenary",
    "bill_promulgated", "bill_enforced",
    "promise_kept", "charity",
}

# 점수 미반영 카테고리
UNSCORED_CATEGORIES = {"controversial"}

# ── 입법 단계 키워드 사전 (결정론적 판정) ──
LEGISLATIVE_STAGE_KEYWORDS = {
    "bill_enforced": ["시행", "발효", "효력 발생", "오늘부터 시행"],
    "bill_promulgated": ["공포", "관보 게재", "법률 제"],
    "bill_plenary": ["본회의 가결", "본회의 통과", "본회의 의결", "표결로 통과", "재석 의원"],
    "bill_committee": ["상임위 통과", "위원회 의결", "소위 가결", "법사위 통과"],
    "bill_proposed": ["발의", "제안", "제출했다", "법안 마련", "개정안"],
}

# ── 매체 성향 ──
MEDIA_LEAN = {
    "한겨레": "progressive",
    "경향신문": "progressive",
    "오마이뉴스": "progressive",
    "KBS": "center",
    "MBC": "center",
    "SBS": "center",
    "연합뉴스": "center",
    "JTBC": "center",
    "조선일보": "conservative",
    "중앙일보": "conservative",
    "동아일보": "conservative",
    "채널A": "conservative",
    "TV조선": "conservative",
}


def detect_legislative_stage(text: str) -> str | None:
    """텍스트에서 입법 단계를 결정론적으로 판정한다.
    가장 높은 단계를 우선 반환 (시행 > 공포 > 본회의 > 위원회 > 발의).
    """
    for stage, keywords in LEGISLATIVE_STAGE_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return stage
    return None
