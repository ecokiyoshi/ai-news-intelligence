import json
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

from app.database import create_db_engine, init_db
from app.pipeline import MetadataTextProvider
from app.production_pipeline import (
    NoFreshYouTubeIdeaError,
    NoPriorityNewsError,
    ProductionProviders,
    run_production_pipeline,
)
from app.scoring import LocalScorer
from app.summarization import LocalSummarizer
from app.youtube_dialogue import LocalYouTubeDialogueConverter
from app.youtube_ideas import LocalYouTubeIdeaGenerator
from app.youtube_image_generation import LocalSceneImageGenerator
from app.youtube_packaging import (
    LocalYouTubePackagingEvaluator,
    LocalYouTubePackagingGenerator,
)
from app.youtube_potential import LocalYouTubePotentialScorer
from app.youtube_script import LocalYouTubeOutlineGenerator, LocalYouTubeScriptGenerator
from app.youtube_visuals import LocalYouTubeVisualPlanner


def local_providers(*, idea_generator=None) -> ProductionProviders:
    return ProductionProviders(
        summarizer=LocalSummarizer(),
        news_scorer=LocalScorer(),
        text_provider=MetadataTextProvider(),
        idea_generator=idea_generator or LocalYouTubeIdeaGenerator(),
        potential_scorer=LocalYouTubePotentialScorer(),
        packaging_generator=LocalYouTubePackagingGenerator(),
        packaging_evaluator=LocalYouTubePackagingEvaluator(),
        outline_generator=LocalYouTubeOutlineGenerator(),
        script_generator=LocalYouTubeScriptGenerator(),
        dialogue_converter=LocalYouTubeDialogueConverter(),
        visual_planner=LocalYouTubeVisualPlanner(),
        image_generator=LocalSceneImageGenerator(),
    )


def feed(title: str = "AI model release", link: str = "https://example.com/article") -> dict:
    return {
        "feed": {"title": "Example News"},
        "entries": [{"title": title, "link": link}],
    }


@pytest.fixture
def production_db(tmp_path: Path):
    engine = create_db_engine(f"sqlite:///{tmp_path / 'production.db'}")
    init_db(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def execute(session, output_root: Path, parser, providers=None):
    return run_production_pipeline(
        ["https://example.com/feed.xml"],
        "AI industry",
        "Practical AI news",
        providers or local_providers(),
        session,
        output_root=output_root,
        news_limit=10,
        idea_count=2,
        packaging_count=2,
        target_minutes=15,
        scene_limit=20,
        image_size="160x90",
        feed_parser=parser,
    )


def test_local_end_to_end_pipeline_persists_complete_unique_runs(
    production_db, tmp_path: Path
) -> None:
    output_root = tmp_path / "outputs"
    with production_db() as session:
        first = execute(session, output_root, lambda _url: feed())
        second = execute(
            session,
            output_root,
            lambda _url: feed("Robotics safety release", "https://example.com/robotics"),
        )

    assert first.run_id != second.run_id
    assert Path(first.output_directory).name == first.run_id
    assert Path(second.output_directory).name == second.run_id
    assert first.news.articles_stored == 1
    assert first.news.priority_articles
    assert first.selected_idea.source_article_ids
    assert first.potential.youtube_potential_score >= 0
    assert first.selected_packaging.title
    assert first.script.narration_sections
    assert first.dialogue.chapters
    assert first.visual_plan.scenes
    assert first.images.assets

    run_dir = Path(first.output_directory)
    assert (run_dir / "manifest.json").is_file()
    assert (run_dir / "run.json").is_file()
    assert all(
        Path(asset.file_path).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        for asset in first.images.assets
    )

    metadata = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert metadata["run_id"] == first.run_id
    assert metadata["source_article_ids"] == first.selected_idea.source_article_ids
    assert metadata["selected_youtube_idea"]["title"] == first.selected_idea.title
    assert metadata["youtube_potential"]["youtube_potential_score"] >= 0
    assert metadata["selected_packaging"]["title"] == first.selected_packaging.title
    assert metadata["script"]["title"] == first.script.title
    assert metadata["dialogue"]["title"] == first.dialogue.title
    assert metadata["visual_plan"]["aspect_ratio"] == "16:9"
    assert metadata["generated_images"]
    assert "manifest.json" in metadata["output_files"]
    assert "run.json" in metadata["output_files"]
    assert "OPENAI_API_KEY" not in json.dumps(metadata)


def test_empty_feed_fails_before_downstream_youtube_provider(
    production_db, tmp_path: Path
) -> None:
    class ForbiddenIdeaGenerator:
        def generate(self, *args, **kwargs):
            raise AssertionError("downstream provider must not be called")

    with production_db() as session, pytest.raises(NoPriorityNewsError):
        execute(
            session,
            tmp_path / "outputs",
            lambda _url: {"feed": {"title": "Empty"}, "entries": []},
            local_providers(idea_generator=ForbiddenIdeaGenerator()),
        )
    assert not (tmp_path / "outputs").exists()


def test_scene_limit_rejects_before_image_provider_call(
    production_db, tmp_path: Path
) -> None:
    class ForbiddenImageGenerator:
        def generate(self, *args, **kwargs):
            raise AssertionError("image provider must not be called")

    class OversizedVisualPlanner:
        def __init__(self):
            self.received_limit = None

        def plan(self, source, *, channel_focus, scene_limit):
            self.received_limit = scene_limit
            return LocalYouTubeVisualPlanner().plan(
                source, channel_focus=channel_focus, scene_limit=20
            )

    providers = local_providers()
    visual_planner = OversizedVisualPlanner()
    providers = ProductionProviders(
        **{
            **providers.__dict__,
            "visual_planner": visual_planner,
            "image_generator": ForbiddenImageGenerator(),
        }
    )
    with production_db() as session, pytest.raises(
        ValueError, match=r"scene count \d+ exceeds configured scene_limit 1"
    ):
        run_production_pipeline(
            ["https://example.com/feed.xml"],
            "AI industry",
            "Practical AI news",
            providers,
            session,
            output_root=tmp_path / "outputs",
            idea_count=1,
            packaging_count=1,
            target_minutes=15,
            scene_limit=1,
            image_size="160x90",
            feed_parser=lambda _url: feed(),
        )
    assert visual_planner.received_limit == 1


def test_repeated_topic_stops_before_potential_and_image_work(
    production_db, tmp_path: Path
) -> None:
    output_root = tmp_path / "outputs"
    with production_db() as session:
        execute(session, output_root, lambda _url: feed())
        before = {path.name for path in output_root.iterdir()}
        with pytest.raises(NoFreshYouTubeIdeaError, match="10 most recent"):
            execute(session, output_root, lambda _url: feed())
    assert {path.name for path in output_root.iterdir()} == before

