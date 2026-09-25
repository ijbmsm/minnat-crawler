"""validation_status 는 DB 제약이 정한 어휘만 쓴다.

supabase/migration-001-validation.sql:
    CHECK (validation_status IN ('passed', 'flagged', 'rejected', 'pending'))

2026-09-25: 'warned' 를 새로 만들어 넣었다가 CHECK 제약에 걸려 매 실행 3건씩
삽입이 막혔다. 기사는 raw_articles 에 남아 복구 가능했지만, 파이프라인은
"DB스킵 3" 이라고만 찍고 넘어갔다. 어휘를 코드에서 못 박아 회귀를 막는다.
"""
import re
from pathlib import Path

ALLOWED = {"passed", "flagged", "rejected", "pending"}


def test_main_writes_only_allowed_validation_status():
    src = Path(__file__).resolve().parent.parent / "main.py"
    text = src.read_text(encoding="utf-8")

    block = re.search(r'issue\["validation_status"\]\s*=\s*\{(.*?)\}\.get\((.*?)\)',
                      text, re.S)
    assert block, "validation_status 매핑을 못 찾았다 — 테스트가 대상을 잃었다"

    written = set(re.findall(r':\s*"([a-z_]+)"', block.group(1)))
    fallback = re.findall(r',\s*"([a-z_]+)"\s*\)', block.group(2))
    written.update(fallback)

    assert written, "매핑에서 값을 못 읽었다"
    assert written <= ALLOWED, (
        f"DB 제약에 없는 값을 쓴다: {sorted(written - ALLOWED)} "
        f"— 허용: {sorted(ALLOWED)}")
