"""정치인 DB 동기화 — 국회 OpenAPI에서 22대 의원 300명+ 자동 수집"""
import httpx
from db import get_client

ASSEMBLY_API = "https://open.assembly.go.kr/portal/openapi"

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

# 직책 가중치
POSITION_WEIGHT = {
    "대통령": 1.2,
    "총리": 1.0,
    "대표": 1.0,
    "원내대표": 1.0,
    "전 대통령": 0.9,
    "전 총리": 0.9,
    "전 대표": 0.9,
    "장관": 0.8,
    "시장": 0.8,
    "도지사": 0.8,
    "의원": 0.8,
}


def fetch_assembly_members() -> list[dict]:
    """국회 OpenAPI에서 22대 국회의원 목록을 가져온다."""
    try:
        resp = httpx.get(
            f"{ASSEMBLY_API}/nwvrqwxyaytdsfvhu",
            params={"KEY": "", "Type": "json", "pSize": 400, "AGE": "22"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        rows = data.get("nwvrqwxyaytdsfvhu", [{}])
        if len(rows) < 2:
            return []

        members: list[dict] = []
        for row in rows[1].get("row", []):
            name = row.get("HG_NM", "").strip()
            party = row.get("POLY_NM", "").strip()
            region = row.get("ORIG_NM", "").strip()
            elect_div = row.get("ELECT_GBN_NM", "")  # 지역구/비례

            if not name or not party:
                continue

            camp = PARTY_CAMP.get(party, "")
            if not camp:
                continue

            members.append({
                "name": name,
                "party_name": party,
                "camp": camp,
                "position": "의원",
                "region": region or None,
                "elect_type": elect_div,
            })

        return members

    except Exception as e:
        print(f"[sync] 국회 API 수집 실패: {e}")
        return []


def sync_to_db() -> None:
    """정치인 DB를 동기화한다."""
    client = get_client()

    # 정당 ID 조회
    parties = client.table("parties").select("id, camp").execute()
    camp_to_party_id: dict[str, str] = {}
    for p in parties.data:
        camp_to_party_id[p["camp"]] = p["id"]

    # 기존 정치인 이름 조회 (중복 방지)
    existing = client.table("politicians").select("name").execute()
    existing_names = {row["name"] for row in existing.data}

    # 1. 국회의원 수집
    print("[sync] 국회 OpenAPI에서 의원 목록 수집 중...")
    members = fetch_assembly_members()
    print(f"[sync] 수집: {len(members)}명")

    new_count = 0
    for m in members:
        if m["name"] in existing_names:
            continue

        party_id = camp_to_party_id.get(m["camp"])
        if not party_id:
            continue

        try:
            client.table("politicians").insert({
                "name": m["name"],
                "party_id": party_id,
                "position": m["position"],
                "region": m["region"],
                "active": True,
            }).execute()
            existing_names.add(m["name"])
            new_count += 1
        except Exception as e:
            print(f"  [skip] {m['name']}: {e}")

    print(f"[sync] 국회의원 신규 추가: {new_count}명")

    # 2. 정부 주요 직책자
    gov_count = 0
    for official in GOVERNMENT_OFFICIALS:
        if official["name"] in existing_names:
            continue

        camp = PARTY_CAMP.get(official["party"], "")
        party_id = camp_to_party_id.get(camp)
        if not party_id:
            continue

        try:
            client.table("politicians").insert({
                "name": official["name"],
                "party_id": party_id,
                "position": official["position"],
                "region": None,
                "active": True,
            }).execute()
            existing_names.add(official["name"])
            gov_count += 1
        except Exception as e:
            print(f"  [skip] {official['name']}: {e}")

    print(f"[sync] 정부 직책자 신규 추가: {gov_count}명")
    print(f"[sync] 총 정치인 DB: {len(existing_names)}명")


if __name__ == "__main__":
    sync_to_db()
