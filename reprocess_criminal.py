"""criminal_conviction 오분류 재처리

confidence < 0.8인 criminal_conviction을 media_coverage로 재분류.
"""
from db import get_client


def reprocess() -> None:
    client = get_client()

    # confidence < 0.8인 criminal_conviction 조회
    result = (
        client.table("issues")
        .select("id, title, ai_analysis")
        .eq("category", "criminal_conviction")
        .execute()
    )

    downgraded = 0
    for issue in result.data:
        ai = issue.get("ai_analysis")
        if not ai:
            continue

        conf = ai.get("confidence", 1.0)
        if conf >= 0.8:
            continue

        # media_coverage로 재분류
        try:
            client.table("issues").update({
                "category": "media_coverage",
                "is_archive": True,
                "weighted_score": 0,
                "criminal_stage": None,
            }).eq("id", issue["id"]).execute()

            print(f"  [downgraded] {conf:.2f} | {issue['title'][:60]}")
            downgraded += 1
        except Exception as e:
            print(f"  [error] {e}")

    print(f"\n재분류: {downgraded}건 (criminal_conviction → media_coverage)")


if __name__ == "__main__":
    reprocess()
