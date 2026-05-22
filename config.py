"""민낯 크롤러 설정 v1.1"""
import os
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

# 공공데이터포털 API 키
DATA_GO_KR_API_KEY = os.environ.get("DATA_GO_KR_API_KEY", "")

# 네이버 검색 API
NAVER_CLIENT_ID = os.environ.get("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "")

# OpenAI Embedding
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSIONS = 1536

# Event 매칭 임계값
EVENT_MATCH_THRESHOLD = 0.85    # 이상이면 같은 사건 확정
EVENT_REJECT_THRESHOLD = 0.35   # 이하이면 다른 사건 확정
EVENT_ACTIVE_DAYS = 7           # active event 윈도우
EVENT_HIGH_IMPACT_COVERAGE = 10  # 이상이면 Sonnet 사용

# ── 카테고리 v1.1 ──

# 점수 카테고리 (공식 처분만)
SCORED_CATEGORIES = [
    "criminal_conviction",  # 형사 유죄 (단계별)
    "civil_judgment",       # 민사 패소
    "ethics_violation",     # 윤리위·선관위 처분
    "factcheck_false",      # IFCN false 판정
    "self_admission",       # 본인 시인·사과
    "official_misconduct",  # 감사원·국정감사 적발
]

# Archive 카테고리 (점수 X)
ARCHIVE_CATEGORIES = [
    "controversial_statement",  # 막말·논란 발언
    "policy_record",            # 정책 기록
    "attendance_record",        # 출석 기록
    "media_coverage",           # 보도 모음
    "politician_sns",           # 본인 SNS
]

# 입법 기록 (점수 X)
BILL_CATEGORIES = [
    "bill_proposed", "bill_committee", "bill_plenary",
    "bill_promulgated", "bill_enforced",
]

ALL_CATEGORIES = SCORED_CATEGORIES + ARCHIVE_CATEGORIES + BILL_CATEGORIES

# ── 형사 단계 가중치 ──
CRIMINAL_STAGE_WEIGHT = {
    "investigation": 0,
    "indicted": 2,
    "suspended_indictment": 1.5,
    "guilty_1st": 4,
    "guilty_2nd": 6,
    "confirmed": 10,
    "pardoned": 10,
    "not_guilty": 0,
    "no_charges": 0,
    "dismissed": 0,
}

# ── 직책 가중치 ──
POSITION_WEIGHT = {
    "대통령": 1.2,
    "총리": 1.0,
    "대표": 1.0,
    "원내대표": 1.0,
    "장관": 0.8,
    "의원": 0.8,
    "시장": 0.8,
    "도지사": 0.8,
    "후보": 0.5,
    "당직자": 0.5,
}

# ── 매체 진영 매핑 ──
MEDIA_LEAN = {
    "한겨레": "progressive",
    "경향신문": "progressive",
    "오마이뉴스": "progressive",
    "프레시안": "progressive",
    "민중의소리": "progressive",
    "KBS": "center",
    "MBC": "center",
    "SBS": "center",
    "연합뉴스": "center",
    "JTBC": "center",
    "YTN": "center",
    "뉴시스": "center",
    "한국일보": "center",
    "조선일보": "conservative",
    "중앙일보": "conservative",
    "동아일보": "conservative",
    "채널A": "conservative",
    "TV조선": "conservative",
    "문화일보": "conservative",
    "세계일보": "conservative",
}

# ── 입법 단계 키워드 ──
LEGISLATIVE_STAGE_KEYWORDS = {
    "bill_enforced": ["시행", "발효", "효력 발생"],
    "bill_promulgated": ["공포", "관보 게재"],
    "bill_plenary": ["본회의 가결", "본회의 통과", "본회의 의결"],
    "bill_committee": ["상임위 통과", "위원회 의결", "소위 가결"],
    "bill_proposed": ["발의", "제안", "제출", "법안 마련", "개정안"],
}

# ── 금지 표현 (expression filter) ──
BANNED_EXPRESSIONS = [
    "부패한", "무능한", "악의적", "비열한", "사악한", "매국",
    "뇌물꾼", "거짓말쟁이", "범죄자", "악당",
]

# ── 신뢰도 게이트 HIGH 신호 키워드 ──
EVIDENCE_KEYWORDS = [
    "영상이 공개", "녹취록", "CCTV", "동영상", "영상 확인",
    "녹음 파일", "문자 메시지", "카톡", "SNS 캡처",
]


def detect_legislative_stage(text: str) -> str | None:
    """텍스트에서 입법 단계를 결정론적으로 판정한다."""
    for stage, keywords in LEGISLATIVE_STAGE_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return stage
    return None
