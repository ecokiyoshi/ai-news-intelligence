import json
from pathlib import Path

import pytest

from app.audio.dialogue_parser import lines_from_run, parse_dialogue
from app.audio.normalization import normalize_japanese_tts
from app.audio.speech_generator import generate_project_audio


class FakeClient:
    calls: list[tuple[str, str]] = []

    def __init__(self, **_kwargs):
        pass

    def get_voice_settings(self, _voice_id: str) -> dict:
        return {"stability": 0.5, "similarity_boost": 0.75}

    def synthesize(self, voice_id: str, text: str, **_kwargs) -> bytes:
        self.calls.append((voice_id, text))
        return b"ID3" + text.encode()


def project(tmp_path: Path) -> Path:
    run = tmp_path / "project_test"
    run.mkdir()
    (run / "run.json").write_text(json.dumps({"dialogue": {
        "opening_lines": [{"speaker": "さび助", "text": "AIとGPU、26GW"}],
        "chapters": [{"lines": [{"speaker": "ハル", "text": "APIって何？"}]}],
        "closing_lines": [],
    }}, ensure_ascii=False), encoding="utf-8")
    return run


ENV = {
    "ELEVENLABS_API_KEY": "secret-test-key",
    "ELEVENLABS_SABISUKE_VOICE_ID": "voice-s",
    "ELEVENLABS_HARU_VOICE_ID": "voice-h",
}


def test_parser_supports_brackets_colons_continuations_and_blank_lines() -> None:
    lines = parse_dialogue("# title\n【さび助】\n一行目\n二行目\n\nハル: 返事")
    assert [(line.speaker, line.text) for line in lines] == [
        ("sabisuke", "一行目\n二行目"), ("haru", "返事")
    ]


def test_parser_rejects_unknown_speaker_in_structured_dialogue() -> None:
    with pytest.raises(ValueError, match="unsupported dialogue speaker"):
        lines_from_run({"opening_lines": [{"speaker": "謎", "text": "話す"}]})


def test_japanese_normalization_is_conservative() -> None:
    assert normalize_japanese_tts("OpenAIのGPUは26GW。本文は維持") == "オープンエーアイのジーピーユーは26ギガワット。本文は維持"


def test_generation_manifest_voice_mapping_cache_and_force(tmp_path: Path) -> None:
    project(tmp_path)
    FakeClient.calls = []
    first = generate_project_audio("project_test", output_root=tmp_path, environ=ENV, client_factory=FakeClient)
    assert first.generated_segments == 2
    assert [voice for voice, _ in FakeClient.calls] == ["voice-s", "voice-h"]
    manifest = json.loads((tmp_path / "project_test/audio/manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "completed"
    assert "secret-test-key" not in json.dumps(manifest)
    assert [item["file"] for item in manifest["segments"]] == ["001_sabisuke.mp3", "002_haru.mp3"]

    FakeClient.calls = []
    second = generate_project_audio("project_test", output_root=tmp_path, environ=ENV, client_factory=FakeClient)
    assert second.reused_segments == 2
    assert FakeClient.calls == []

    forced = generate_project_audio("project_test", output_root=tmp_path, environ=ENV, force=True, client_factory=FakeClient)
    assert forced.generated_segments == 2


def test_missing_credentials_fail_before_client_call(tmp_path: Path) -> None:
    project(tmp_path)
    with pytest.raises(ValueError, match="ELEVENLABS_API_KEY"):
        generate_project_audio("project_test", output_root=tmp_path, environ={}, client_factory=FakeClient)
