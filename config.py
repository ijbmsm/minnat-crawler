"""민낯 크롤러 설정"""
import os
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

# 카테고리 정의
CATEGORIES = [
    "crime", "corruption", "hypocrisy", "slander", "division",
    "policy_fail", "charity", "policy_win", "promise_kept",
    "promise_broke", "controversial",
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
    "crime": 10,
    "corruption": 9,
    "policy_fail": 8,
    "promise_broke": 7,
    "hypocrisy": 6,
    "division": 5,
    "slander": 4,
    "policy_win": 7,
    "promise_kept": 6,
    "charity": 5,
    "controversial": 0,
}

# 가점 카테고리
POSITIVE_CATEGORIES = {"charity", "policy_win", "promise_kept"}

# 점수 미반영 카테고리
UNSCORED_CATEGORIES = {"controversial"}
