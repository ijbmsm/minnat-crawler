"""정치인 DB 동기화 — 국회 OpenAPI에서 22대 의원 자동 수집

ALLNAMEMBER(국회의원 정보 통합 API)는 역대 의원 3,296명 전체를 담고 있다.
GTELT_ERACO(당선대수)로 22대만 걸러 쓴다 — 역대 전체를 넣으면
① 시스템 프롬프트/DB가 불필요하게 커지고
② 역대 정당(민주정의당·유신정우회 등)을 blue/red로 가르는 정치적 판단이 필요해진다.
22대로 한정하면 이름→진영 충돌이 0건임을 실측 확인(2026-09-16).
"""
import httpx
from config import ASSEMBLY_API_KEY
from db import get_client

ASSEMBLY_API = "https://open.assembly.go.kr/portal/openapi"

# 대상 대수 (GTELT_ERACO는 "제21대, 제22대"처럼 쉼표로 여러 대수가 들어온다)
TARGET_ERA = "제22대"

# ALLNAMEMBER 1회 최대 1,000건 (초과 시 ERROR-336)
PAGE_SIZE = 1000
MAX_PAGES = 10

# 정당 → camp 매핑
PARTY_CAMP = {
    "더불어민주당": "blue",
    "민주당": "blue",
    "열린민주당": "blue",
    "조국혁신당": "blue",
    "더불어민주연합": "blue",
    "국민의힘": "red",
    "국민의미래": "red",
    "개혁신당": "red",
    "새로운미래": "red",
}

# 정부 주요 직책자 (수동)
GOVERNMENT_OFFICIALS = [
    {"name": "이재명", "party": "더불어민주당", "position": "대통령"},
    {"name": "한덕수", "party": "국민의힘", "position": "전 총리"},
    {"name": "오세훈", "party": "국민의힘", "position": "서울시장"},
    {"name": "홍준표", "party": "국민의힘", "position": "대구시장"},
    {"name": "윤석열", "party": "국민의힘", "position": "전 대통령"},
    {"name": "조국", "party": "조국혁신당", "position": "대표"},
    {"name": "이준석", "party": "개혁신당", "position": "대표"},
]

def _latest_party(raw: str | None) -> str:
    """PLPT_NM은 "미래통합당/국민의힘"처럼 정당 이력이 슬래시로 누적된다. 마지막이 현재 정당."""
    return (raw or "").split("/")[-1].strip()


def _eras(raw: str | None) -> list[str]:
    """GTELT_ERACO를 대수 리스트로 분리한다.

    부분 문자열 매칭("제22대" in "제2대")으로 판정하면 오탐이 나므로 정확히 분리해 비교한다.
    """
    return [e.strip() for e in (raw or "").split(",") if e.strip()]


def fetch_assembly_members() -> list[dict]:
    """국회 OpenAPI(ALLNAMEMBER)에서 22대 국회의원 목록을 가져온다."""
    if not ASSEMBLY_API_KEY:
        print("  [sync][경고] ASSEMBLY_API_KEY가 없습니다 — sample key는 5건만 반환합니다")

    rows: list[dict] = []
    try:
        for page in range(1, MAX_PAGES + 1):
            resp = httpx.get(
                f"{ASSEMBLY_API}/ALLNAMEMBER",
                params={
                    "KEY": ASSEMBLY_API_KEY,
                    "Type": "json",
                    "pIndex": page,
                    "pSize": PAGE_SIZE,
                },
                timeout=30,
            )
            resp.raise_for_status()
            body = resp.json().get("ALLNAMEMBER", [])

            if len(body) < 2 or "row" not in body[1]:
                result = body[0].get("head", [{}, {}])[1].get("RESULT", {}) if body else {}
                if result.get("CODE") not in (None, "INFO-000", "INFO-200"):
                    print(f"  [sync][경고] API 응답: {result.get('CODE')} {result.get('MESSAGE')}")
                break

            page_rows = body[1]["row"]
            rows.extend(page_rows)
            if len(page_rows) < PAGE_SIZE:
                break

        print(f"  [sync] ALLNAMEMBER 전체 {len(rows)}명 수신")

        members: list[dict] = []
        unmapped: dict[str, int] = {}

        for row in rows:
            if TARGET_ERA not in _eras(row.get("GTELT_ERACO")):
                continue

            name = (row.get("NAAS_NM") or "").strip()
            party = _latest_party(row.get("PLPT_NM"))
            if not name or not party:
                continue

            camp = PARTY_CAMP.get(party, "")
            if not camp:
                unmapped[party] = unmapped.get(party, 0) + 1
                continue

            members.append({
                "name": name,
                "assembly_code": (row.get("NAAS_CD") or "").strip(),
                "party_name": party,
                "camp": camp,
                "position": "의원",
                "region": (row.get("ELECD_NM") or "").split("/")[-1].strip() or None,
                "elect_type": (row.get("ELECD_DIV_NM") or "").split("/")[-1].strip(),
            })

        if unmapped:
            # 무소속은 의도적 제외(camp=null). 그 외는 PARTY_CAMP 보강 검토 대상.
            detail = ", ".join(f"{p} {c}명" for p, c in sorted(unmapped.items()))
            print(f"  [sync] 진영 미매핑 제외: {detail}")

        return members

    except Exception as e:
        print(f"  [sync][경고] 국회 API 수집 실패: {e}")
        return []


def sync_to_db() -> None:
    """정치인 DB를 동기화한다.

    신규 추가뿐 아니라 당적 변경(진영 이동)도 반영한다. camp 판정이 이 테이블에
    전적으로 의존하므로, 낡은 당적이 남아 있으면 기사 전체가 잘못 분류된다.
    """
    client = get_client()

    # 정당 ID 조회
    parties = client.table("parties").select("id, camp").execute()
    camp_to_party_id: dict[str, str] = {p["camp"]: p["id"] for p in parties.data}

    # 기존 정치인 조회 (이름 기준 — 동명이인은 한 행으로 합쳐진다.
    # 22대 범위에서 이름→진영 충돌이 0건임을 확인했으므로 안전하다.)
    existing = client.table("politicians").select("id, name, party_id, position").execute()
    by_name = {row["name"]: row for row in existing.data}

    # 1. 국회의원 수집
    print("[sync] 국회 OpenAPI에서 의원 목록 수집 중...")
    members = fetch_assembly_members()
    print(f"[sync] 22대 수집: {len(members)}명")

    # 정부 주요 직책자를 의원 목록 위에 덮어쓴다.
    #
    # 예전에는 `members + officials` 를 앞에서부터 돌며 이미 본 이름을 건너뛰었다.
    # 주석은 "뒤에 둬서 우선 적용" 이라고 적혀 있었지만 실제로는 정반대로,
    # 앞선 의원 항목이 이기고 직책이 통째로 무시됐다. 그래서 현직 대통령이
    # DB 에 '의원' 으로 남아 직책 가중치가 1.2 가 아니라 0.8 로 계산됐다
    # (2026-09-21 실측: 이재명·이준석·조국 전원 '의원').
    merged: dict[str, dict] = {m["name"]: m for m in members}
    for o in GOVERNMENT_OFFICIALS:
        base = merged.get(o["name"], {})
        merged[o["name"]] = {
            **base,
            "name": o["name"],
            "camp": PARTY_CAMP.get(o["party"], ""),
            "position": o["position"],
        }

    to_insert: list[dict] = []
    to_update: list[tuple[str, dict]] = []

    for m in merged.values():
        name, camp = m["name"], m.get("camp", "")
        party_id = camp_to_party_id.get(camp)
        if not name or not party_id:
            continue

        row = by_name.get(name)
        if row is None:
            to_insert.append({
                "name": name,
                "party_id": party_id,
                "position": m.get("position", "의원"),
                "region": m.get("region"),
                "active": True,
            })
            continue

        # 당적이 바뀌었거나 직책이 갱신된 경우만 UPDATE
        changes: dict = {}
        if row.get("party_id") != party_id:
            changes["party_id"] = party_id
        if m.get("position") and row.get("position") != m["position"]:
            changes["position"] = m["position"]
        if changes:
            to_update.append((row["id"], changes))

    # 배치 INSERT — 건별 왕복을 없앤다
    if to_insert:
        try:
            client.table("politicians").insert(to_insert).execute()
        except Exception as e:
            print(f"  [sync][경고] 일괄 삽입 실패, 건별 재시도: {e}")
            for r in to_insert:
                try:
                    client.table("politicians").insert(r).execute()
                except Exception as inner:
                    print(f"    [skip] {r['name']}: {inner}")

    for pid, changes in to_update:
        try:
            client.table("politicians").update(changes).eq("id", pid).execute()
        except Exception as e:
            print(f"  [skip] 갱신 실패 {pid}: {e}")

    print(f"[sync] 신규 추가: {len(to_insert)}명 | 당적·직책 갱신: {len(to_update)}명")
    print(f"[sync] 총 정치인 DB: {len(by_name) + len(to_insert)}명")

    if not members:
        print("[sync][경고] 국회 API에서 의원을 한 명도 받지 못했습니다 — camp 판정이 붕괴합니다")


if __name__ == "__main__":
    sync_to_db()
