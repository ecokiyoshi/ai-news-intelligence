from app.topic_deduplication import titles_are_similar


def test_openai_infrastructure_title_variants_are_similar() -> None:
    titles = [
        "What OpenAI's NVIDIA, AMD, Broadcom, Stargate, and AWS Moves Signal for AI Infrastructure",
        "OpenAI's 26-Gigawatt AI Compute Push: NVIDIA, AMD, and Broadcom Explained",
        "OpenAI's 26-Gigawatt AI Infrastructure Buildout Explained",
        "Why OpenAI Is Splitting 26 Gigawatts Across NVIDIA, AMD, and Broadcom",
    ]
    assert all(titles_are_similar(titles[0], title) for title in titles[1:])


def test_unrelated_ai_topics_are_not_similar() -> None:
    assert not titles_are_similar(
        "OpenAI's 26-Gigawatt AI Infrastructure Buildout Explained",
        "How warehouse drones are changing last-mile inventory audits",
    )

