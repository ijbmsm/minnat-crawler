"""국회 의안정보시스템 크롤러

Open API: https://open.assembly.go.kr
- 법안 발의/표결 기록 수집
- 정치인별 표결 이력 추적
"""
import httpx
from datetime import datetime, timedelta

# 국회 Open API (키 없이도 기본 조회 가능)
BASE_URL = "https://open.assembly.go.kr/portal/openapi"


def fetch_recent_bills(days: int = 7) -> list[dict]:
    """최근 N일간 발의된 법안 목록을 가져온다."""
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    try:
        resp = httpx.get(
            f"{BASE_URL}/nzmimeepazxkubdpn",
            params={
                "KEY": "",  # 공개 API
                "Type": "json",
                "pSize": 100,
                "AGE": "22",  # 22대 국회
                "PROPOSE_DT": since,
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        # API 응답 구조: nzmimeepazxkubdpn > [1] > row
        rows = data.get("nzmimeepazxkubdpn", [{}])
        if len(rows) < 2:
            return []

        return [
            {
                "title": row.get("BILL_NAME", ""),
                "proposer": row.get("PROPOSER", ""),
                "propose_date": row.get("PROPOSE_DT", ""),
                "committee": row.get("COMMITTEE", ""),
                "bill_id": row.get("BILL_ID", ""),
                "detail_link": row.get("DETAIL_LINK", ""),
                "source": "국회 의안정보시스템",
                "source_tier": 1,
            }
            for row in rows[1].get("row", [])
        ]
    except Exception as e:
        print(f"[assembly] 법안 수집 실패: {e}")
        return []


def fetch_vote_results(bill_id: str) -> dict | None:
    """특정 법안의 표결 결과를 가져온다."""
    try:
        resp = httpx.get(
            f"{BASE_URL}/nojepdqqaweusdfbi",
            params={
                "KEY": "",
                "Type": "json",
                "BILL_ID": bill_id,
                "AGE": "22",
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        rows = data.get("nojepdqqaweusdfbi", [{}])
        if len(rows) < 2:
            return None

        result = rows[1].get("row", [{}])[0]
        return {
            "bill_id": bill_id,
            "yes": int(result.get("YES_TCNT", 0)),
            "no": int(result.get("NO_TCNT", 0)),
            "abstain": int(result.get("BLANK_TCNT", 0)),
        }
    except Exception as e:
        print(f"[assembly] 표결 결과 수집 실패 ({bill_id}): {e}")
        return None


if __name__ == "__main__":
    bills = fetch_recent_bills(30)
    print(f"수집된 법안: {len(bills)}건")
    for bill in bills[:5]:
        print(f"  - {bill['title']} ({bill['proposer']})")
