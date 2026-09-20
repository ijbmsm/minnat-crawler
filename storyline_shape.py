"""사안의 뼈대를 만든다 — LLM 없이 결정론으로.

장을 어디서 자를지, 각 장의 등급이 무엇인지는 규칙으로 정한다. LLM 에 맡기면
같은 데이터로 매번 다른 뼈대가 나오고, 등급은 사실관계라 추측이 섞이면 안 된다.
LLM 은 이 뼈대 위에 문장만 얹는다(storyline_author).
"""
from datetime import datetime

# 장을 자르는 기준
CHAPTER_GAP_DAYS = 120      # 보도 공백이 이만큼 벌어지면 국면이 바뀐 것으로 본다
MAX_CHAPTERS = 7            # 이보다 많으면 읽기 어렵다. 가까운 것부터 합친다
MIN_EVENTS_FOR_STORY = 3    # 사건 3건 미만은 사안이 아니라 그냥 사건이다

# criminal_stage → 단계 순서. 단계가 오르면 국면이 바뀐 것으로 본다
STAGE_RANK = {
    "investigation": 1,
    "indicted": 2,
    "suspended_indictment": 2,
    "guilty_1st": 3,
    "guilty_2nd": 4,
    "confirmed": 5,
    "not_guilty": 5,
    "no_charges": 5,
    "dismissed": 5,
    "pardoned": 6,
}

# 제도적 결정은 그 자체가 공식 기록이다. 국회가 가결했다·헌재가 인용했다는
# 일어난 사실이며, 형사 혐의의 유무죄와는 다른 축이다.
# (혐의가 사실이라는 뜻이 아니라 "이 결정이 있었다"가 확인됐다는 뜻이다)
INSTITUTIONAL_CONFIRMED = {
    "impeachment_proposed", "impeachment_passed", "impeachment_upheld",
    "impeachment_rejected", "censure_passed", "inquiry_launched",
}

# 확정으로 볼 수 있는 형사 단계 — 법원이 판단을 내린 것
SETTLED_STAGES = {"guilty_1st", "guilty_2nd", "confirmed", "not_guilty", "no_charges", "dismissed", "pardoned"}
# 수사기관의 판단일 뿐 유무죄가 정해지지 않은 단계
ALLEGED_STAGES = {"investigation", "indicted", "suspended_indictment"}

# 점수 카테고리(공식 처분)
SCORED = {
    "criminal_conviction", "civil_judgment", "ethics_violation",
    "factcheck_false", "self_admission", "official_misconduct",
}


def event_date(item: dict) -> str | None:
    """사건(클러스터)이든 기사(issue)든 날짜 필드를 찾아 준다.

    장을 기사 단위로 자르기 때문에 두 모양이 모두 들어온다.
    """
    return item.get("published_at") or item.get("first_reported_at") or item.get("created_at")


def parse_date(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def event_grade(event: dict) -> str:
    """사건 하나의 근거 등급. **LLM 이 아니라 기록에서 도출한다.**

    확정 / 혐의 / 주장·보도 는 사실관계라 추측이 섞이면 안 된다.
    인터넷 검색이 이 셋을 섞어 주는 게 이 제품이 메우려는 빈틈인데,
    우리가 다시 섞으면 존재 이유가 없어진다.
    """
    # 제도적 결정이 먼저다. 한 기사가 둘 다 가질 수 있는데(헌재 파면 기사에
    # 형사 기소 단계가 함께 붙는다), 그 기사가 기록하는 것은 제도적 결정이다
    if event.get("institutional_stage") in INSTITUTIONAL_CONFIRMED:
        return "confirmed"

    stage = event.get("criminal_stage")
    if stage in SETTLED_STAGES:
        return "confirmed"
    if stage in ALLEGED_STAGES:
        return "alleged"

    category = event.get("category") or ""
    tier = event.get("source_tier") or 3

    # 본인 시인, 공식 기관 처분은 단계가 없어도 확정 기록이다
    if category in ("self_admission", "official_misconduct") and event.get("verified"):
        return "confirmed"
    # 국회·법원·선관위 같은 1차 출처의 기록
    if tier == 1 and event.get("verified"):
        return "confirmed"
    if category in SCORED:
        return "alleged"
    return "claim"


def chapter_grade(events: list[dict]) -> str:
    """장의 등급 — 그 장에서 가장 단단한 근거를 따른다.

    한 장에 확정과 주장이 섞이면 "이 국면에서 무엇이 확인됐나"가 기준이므로
    가장 높은 등급을 쓴다. 개별 기사는 각자 등급을 그대로 달고 나간다.
    """
    order = {"confirmed": 3, "alleged": 2, "claim": 1}
    best = max((event_grade(e) for e in events), key=lambda g: order[g], default="claim")
    return best


def split_chapters(events: list[dict]) -> list[list[dict]]:
    """사건 목록을 국면으로 자른다.

    자르는 지점:
      1. 형사 단계가 올라갔을 때 (수사 → 기소 → 1심 …)
      2. 보도가 CHAPTER_GAP_DAYS 이상 끊겼을 때
    그다음 MAX_CHAPTERS 를 넘으면 기간이 가장 짧은 이웃끼리 합친다.
    """
    dated = [e for e in events if parse_date(event_date(e))]
    if not dated:
        return []
    dated.sort(key=lambda e: parse_date(event_date(e)))

    chapters: list[list[dict]] = [[dated[0]]]
    for prev, cur in zip(dated, dated[1:]):
        gap = (parse_date(event_date(cur)) - parse_date(event_date(prev))).days
        rank_prev = STAGE_RANK.get(prev.get("criminal_stage") or "", 0)
        rank_cur = STAGE_RANK.get(cur.get("criminal_stage") or "", 0)

        if (rank_cur > rank_prev > 0) or gap >= CHAPTER_GAP_DAYS:
            chapters.append([cur])
        else:
            chapters[-1].append(cur)

    while len(chapters) > MAX_CHAPTERS:
        # 가장 짧은 장을 앞 장에 흡수시킨다 (첫 장이면 뒤로)
        idx = min(range(len(chapters)), key=lambda i: _span_days(chapters[i]))
        target = idx - 1 if idx > 0 else 1
        chapters[target] = sorted(
            chapters[target] + chapters[idx],
            key=lambda e: parse_date(event_date(e)),
        )
        chapters.pop(idx)

    return chapters


def _span_days(chapter: list[dict]) -> int:
    dates = [parse_date(event_date(e)) for e in chapter]
    dates = [d for d in dates if d]
    if not dates:
        return 0
    return (max(dates) - min(dates)).days


def when_label(chapter: list[dict]) -> str:
    """"2021.10" 또는 "2022 – 2023" 같은 기간 표기."""
    dates = sorted(d for d in (parse_date(event_date(e)) for e in chapter) if d)
    if not dates:
        return ""
    first, last = dates[0], dates[-1]
    if first.year != last.year:
        return f"{first.year} – {last.year}"
    if first.month != last.month:
        return f"{first.year}.{first.month} – {last.month}"
    # 같은 달 안에서 국면이 나뉘면(형사 단계가 오르면) 라벨이 겹친다.
    # 실제로 비상계엄의 1·2장이 둘 다 "2024.12" 였다. 일까지 보여 구분한다.
    if first.day == last.day:
        return f"{first.year}.{first.month}.{first.day}"
    return f"{first.year}.{first.month}.{first.day} – {last.day}"


def story_status(events: list[dict]) -> tuple[str, str | None]:
    """(status, ended_at) — 결말이 났는가.

    가장 마지막 사건의 형사 단계가 확정·사면이면 종결로 본다.
    """
    dated = [e for e in events if parse_date(event_date(e))]
    if not dated:
        return "ongoing", None
    dated.sort(key=lambda e: parse_date(event_date(e)))
    last = dated[-1]
    if (last.get("criminal_stage") or "") in {"confirmed", "pardoned", "not_guilty", "no_charges", "dismissed"}:
        return "closed", parse_date(event_date(last)).date().isoformat()
    return "ongoing", None
