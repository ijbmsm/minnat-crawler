"""사안 발견 — 어떤 사건들이 하나의 사안인가.

임베딩으로 묶으려다 실패했다. 2026-09-19 실측:
    같은 사안  국정농단 ↔ 국정농단   0.342
               계엄 선포 ↔ 계엄 기소  0.335
    다른 사안  계엄 ↔ 국정농단        0.377   ← 같은 사안보다 높다
같은 사안 0.24~0.75 / 다른 사안 0.17~0.38 로 완전히 겹쳐 어떤 임계로도 갈리지 않는다.
사건 요약이 짧고 압축적이라 벡터가 표면 어휘에 지배되기 때문이다.

실제로 이 문서들을 가르는 신호는 **희소 고유명사**다("국정농단", "대장동", "화천대유",
"명태균"). 흔한 말(대통령·기소·혐의·의혹)은 거의 모든 사건에 나오므로 문서빈도로 걸러낸다.

묶는 방식은 두 번 갈아엎었다.
  1차: 고유명사를 하나라도 공유하면 union → 전이적으로 번져 95건 중 66건이 한 사안.
       윤석열↔박근혜↔이준석 처럼 **인물 이름이 서로 다른 사안을 이어버린다**.
  2차: 앵커 낱말 하나로 묶기 → 낱말 충돌이 남았다. "갈등"·"특혜"·"부재"·"2년" 같은
       흔한 말이 무관한 사건을 묶고("징역 2년" 때문에 드루킹과 조국이 한 사안),
       "박근혜" 앵커가 세월호·블랙리스트·개성공단·국정농단을 한 덩어리로 만들었다.
  3차(현재): **고리를 2개 이상 공유해야 잇는다.**
       진짜 사안은 여러 낱말을 함께 공유하고(12·3 + 비상계엄 + 내란수괴 + 헌재),
       우연한 충돌은 낱말 하나뿐이다. 인물 이름은 여전히 고리에서 뺀다.
"""
import re
from collections import defaultdict

# 고유명사 후보: 한글/숫자/·/가운뎃점이 섞인 2자 이상 덩어리
TOKEN_RE = re.compile(r"[가-힣A-Za-z0-9][가-힣A-Za-z0-9·]{1,15}")

# 조사를 떼어내지 않으면 "대장동"과 "대장동의"가 다른 낱말이 된다
JOSA = (
    "으로써", "으로서", "에서는", "에게서", "으로", "에서", "에게", "까지", "부터",
    "이라는", "라는", "이라고", "라고", "와의", "과의", "의", "은", "는", "이", "가",
    "을", "를", "에", "와", "과", "로", "도", "만", "및",
)

# 정치 기사에 늘 나오는 말. 사안을 특정하지 못한다
STOPWORDS = {
    "대통령", "의원", "장관", "후보", "후보자", "대표", "위원장", "검찰", "법원", "국회",
    "혐의", "기소", "구속", "수사", "재판", "선고", "유죄", "무죄", "논란", "의혹",
    "특검", "발언", "비판", "요구", "촉구", "결정", "발표", "제기", "관련", "진행",
    "사건", "사퇴", "임명", "지명", "탄핵", "탄핵소추", "특별사면", "사면", "확정",
    "징역", "벌금", "위반", "개입", "공모", "지시", "정부", "여당", "야당", "민주당",
    "국민의힘", "청와대", "대법원", "헌법재판소", "1심", "2심", "항소심",
}

# 문서빈도 상한 — 이 비율을 넘게 나오면 흔한 말로 본다.
#
# 0.15 로 뒀다가 문제가 났다: "김승원"이 95건 중 18건(19%)에 나와 상한을 넘어 버려졌는데,
# 정작 그 사안을 특정하는 **유일한** 낱말이었다. 한 뉴스 사이클이 말뭉치를 지배하면
# 정당한 고유명사가 흔한 말로 오인된다.
#
# 0.35 로 올려도 안전한 이유: 이제 고리를 **2개 이상** 공유해야 잇기 때문에 흔한 말
# 하나만으로는 사안이 만들어지지 않는다. 진짜 일반명사는 STOPWORDS 가 막는다.
MAX_DOC_RATIO = 0.35
# 최소 이만큼의 사건에 함께 나와야 사안을 잇는 고리로 인정한다
MIN_DOC_COUNT = 2
# 사건이 이보다 적으면 사안이 아니라 그냥 사건이다.
# 2 로 두는 이유: 현재 DB 는 한 주제당 사건이 1~3건뿐이라 3 으로 두면 대장동·국정농단·
# 다스 같은 명백한 사안이 전부 탈락한다. 사안은 2건에서 시작해 자라는 것이기도 하다.
MIN_EVENTS = 2

# 사안 안에서 이만큼 시간이 비면 다른 사안으로 본다.
# 세월호(2014)와 이태원(2022)이 "대응·부재·참사"를 공유해 한 사안이 됐다 —
# 재난 대응 기사는 어휘가 비슷해서 낱말만으로는 갈리지 않는다. 진짜 사안은 중간을
# 잇는 사건이 있다. 8년이 통째로 비어 있으면 서로 다른 일이다.
MAX_SAGA_GAP_DAYS = 3 * 365


def normalize(token: str) -> str:
    """조사를 떼어낸다. 가장 긴 것부터 떼야 '에서는'이 '는'으로 잘리지 않는다."""
    for josa in JOSA:
        if len(token) > len(josa) + 1 and token.endswith(josa):
            return token[: -len(josa)]
    return token


def extract_terms(text: str) -> set[str]:
    out = set()
    for raw in TOKEN_RE.findall(text or ""):
        term = normalize(raw)
        if len(term) < 2 or term in STOPWORDS:
            continue
        if term.isdigit():
            continue
        out.add(term)
    return out


def distinctive_terms(events: list[dict]) -> dict[str, set[str]]:
    """사건 id → 그 사건의 희소 고유명사 집합."""
    per_event = {e["id"]: extract_terms(f"{e.get('summary') or ''} {e.get('actor_name') or ''}")
                 for e in events}

    df: dict[str, int] = defaultdict(int)
    for terms in per_event.values():
        for t in terms:
            df[t] += 1

    cap = max(MIN_DOC_COUNT, int(len(events) * MAX_DOC_RATIO))
    keep = {t for t, n in df.items() if MIN_DOC_COUNT <= n <= cap}
    return {eid: terms & keep for eid, terms in per_event.items()}


# 두 사건을 잇는 데 필요한 공유 고리 수
MIN_SHARED_TERMS = 2


def group_events(events: list[dict], person_names: set[str] | None = None) -> list[tuple[str, list[dict]]]:
    """사안 후보 목록. (대표 낱말, 사건들).

    두 사건이 희소 고유명사를 MIN_SHARED_TERMS 개 이상 공유하면 같은 사안으로 잇는다.
    하나만 공유하는 연결은 대개 낱말 충돌이다("징역 2년", "갈등", "특혜").
    """
    people = {p.strip() for p in (person_names or set()) if p and p.strip()}
    raw = distinctive_terms(events)
    # 인물 이름은 고리에서 뺀다 — 같은 정치인이 여러 사안에 등장하므로 잇는 근거가 못 된다
    terms_by_event = {eid: {t for t in terms if t not in people} for eid, terms in raw.items()}

    by_id = {e["id"]: e for e in events}
    parent: dict[str, str] = {e["id"]: e["id"] for e in events}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    # 낱말을 공유하는 쌍만 후보로 본다 (전수 비교를 피한다)
    by_term: dict[str, list[str]] = defaultdict(list)
    for eid, terms in terms_by_event.items():
        for t in terms:
            by_term[t].append(eid)

    pairs: set[tuple[str, str]] = set()
    for ids in by_term.values():
        if len(ids) > 40:   # 그래도 흔한 말이면 쌍을 만들지 않는다
            continue
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                pairs.add((a, b) if a < b else (b, a))

    for a, b in pairs:
        if len(terms_by_event[a] & terms_by_event[b]) >= MIN_SHARED_TERMS:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

    groups: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        groups[find(e["id"])].append(e)

    out: list[tuple[str, list[dict]]] = []
    for members in groups.values():
        for members in _split_on_time_gap(members):
            if len(members) < MIN_EVENTS:
                continue
            # 대표 낱말 = 그룹 안에서 가장 많이 공유되는 것
            counts: dict[str, int] = defaultdict(int)
            for m in members:
                for t in terms_by_event[m["id"]]:
                    counts[t] += 1
            label = max(counts.items(), key=lambda kv: (kv[1], len(kv[0])))[0] if counts else "?"
            out.append((label, members))

    out = _merge_overlapping(out, raw, people)
    out.sort(key=lambda kv: -len(kv[1]))
    return out


# 이미 만들어진 사안끼리 합칠 때 필요한 공유 낱말 수 (인물 이름 포함)
MERGE_SHARED_TERMS = 2
# 두 사안의 기간이 이보다 벌어져 있으면 합치지 않는다.
# 낱말만 보면 "윤석열"을 공유한다는 이유로 이태원 참사(2022)와 비상계엄(2024)이
# 한 사안이 된다. 쪼개진 같은 사안은 늘 시간이 붙어 있다(김승원 조각들은 같은 달).
MERGE_MAX_GAP_DAYS = 180


def _merge_overlapping(
    groups: list[tuple[str, list[dict]]],
    terms_by_event: dict[str, set[str]],
    people: set[str],
) -> list[tuple[str, list[dict]]]:
    """같은 사안이 쪼개진 것을 합친다.

    묶을 때는 인물 이름을 뺐지만(같은 정치인이 여러 사안에 나오므로), **이미 만들어진
    사안끼리 비교할 때는 넣는다.** 예: "김승원 법무부 자진" 과 "김승원 법무장관 자진" 은
    비인물 낱말을 하나만 공유해 따로 만들어졌는데, 김승원까지 보면 명백히 한 사안이다.
    이 단계는 전체 말뭉치로 번지지 않으므로 전이 폭발 위험이 없다.

    다만 조건이 둘 더 있다:
      - **인물 이름만으로는 합치지 않는다.** "이재명·이준석"을 공유한다는 이유로
        청년정책 전담조직과 중앙아시아 외교가 한 사안이 됐다.
      - **기간이 붙어 있어야 한다.** "윤석열"을 공유한다는 이유로 이태원 참사(2022)와
        비상계엄(2024)이 한 사안이 됐다. 쪼개진 같은 사안은 늘 시간이 붙어 있다.
    """
    def signature(members: list[dict]) -> set[str]:
        # 절반 이상의 사건에 나오는 낱말이 그 사안의 지문이다
        counts: dict[str, int] = defaultdict(int)
        for m in members:
            for t in terms_by_event.get(m["id"], ()):
                counts[t] += 1
        need = max(1, len(members) // 2)
        return {t for t, n in counts.items() if n >= need}

    merged: list[tuple[str, list[dict], set[str]]] = []
    for label, members in groups:
        sig = signature(members)
        for i, (_, other_members, other_sig) in enumerate(merged):
            shared = sig & other_sig
            if (
                len(shared) >= MERGE_SHARED_TERMS
                and (shared - people)
                and _time_gap_days(members, other_members) <= MERGE_MAX_GAP_DAYS
            ):
                combined = other_members + members
                merged[i] = (merged[i][0], combined, signature(combined))
                break
        else:
            merged.append((label, members, sig))
    return [(label, members) for label, members, _ in merged]


def _time_gap_days(a: list[dict], b: list[dict]) -> float:
    """두 사안의 기간 사이 거리. 겹치면 0."""
    da = sorted(d for d in (_when(m) for m in a) if d)
    db = sorted(d for d in (_when(m) for m in b) if d)
    if not da or not db:
        return 0.0
    if da[-1] >= db[0] and db[-1] >= da[0]:
        return 0.0
    return abs((db[0] - da[-1]).days if db[0] > da[-1] else (da[0] - db[-1]).days)


def _split_on_time_gap(members: list[dict]) -> list[list[dict]]:
    """시간이 크게 빈 지점에서 쪼갠다. 이어 주는 사건이 없으면 같은 사안이 아니다."""
    dated = [(_when(m), m) for m in members]
    dated = [(d, m) for d, m in dated if d]
    if len(dated) < 2:
        return [members]
    dated.sort(key=lambda dm: dm[0])

    chunks: list[list[dict]] = [[dated[0][1]]]
    for (prev_d, _), (cur_d, cur) in zip(dated, dated[1:]):
        if (cur_d - prev_d).days > MAX_SAGA_GAP_DAYS:
            chunks.append([cur])
        else:
            chunks[-1].append(cur)
    return chunks


def _when(event: dict):
    from datetime import datetime
    value = event.get("first_reported_at")
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def group_keywords(group: list[dict], events: list[dict], limit: int = 8) -> list[str]:
    """사안을 특정하는 키워드. 그룹 안에서 많이 겹치는 순."""
    terms_by_event = distinctive_terms(events)
    counts: dict[str, int] = defaultdict(int)
    for e in group:
        for t in terms_by_event.get(e["id"], ()):
            counts[t] += 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], -len(kv[0])))
    return [t for t, n in ranked if n >= 2][:limit]
