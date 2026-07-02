from pathlib import Path

from orpheus.settings import (DEFAULT_CLEANUP_PROMPT, Settings, load_settings,
                              save_settings)


def test_default_prompt_covers_corrections_and_lists():
    prompt = DEFAULT_CLEANUP_PROMPT.lower()
    assert "scratch that" in prompt          # self-correction handling
    assert "never translate" in prompt       # language preservation
    assert "own line" in prompt              # list formatting
    assert Settings().cleanup_prompt == DEFAULT_CLEANUP_PROMPT


def test_missing_file_returns_defaults(tmp_path: Path):
    settings = load_settings(tmp_path / "nope.toml")
    assert settings == Settings()
    assert settings.hotkey == "<ctrl>+<alt>+<space>"
    assert settings.model_size == "large-v3-turbo"
    assert settings.language == "auto"
    assert settings.delivery == "type"


def test_round_trip(tmp_path: Path):
    path = tmp_path / "config.toml"
    original = Settings(
        hotkey="<ctrl>+<shift>+d",
        input_device="Yeti Stereo Microphone",
        model_size="small",
        language="he",
        cleanup_enabled=False,
        vocabulary=["Orpheus", "Zborovsky"],
        delivery="paste",
    )
    save_settings(path, original)
    assert load_settings(path) == original


def test_unknown_keys_ignored(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text('hotkey = "<f9>"\nfuture_option = true\n', encoding="utf-8")
    settings = load_settings(path)
    assert settings.hotkey == "<f9>"
    assert not hasattr(settings, "future_option")


def test_partial_file_keeps_defaults(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text('language = "en"\n', encoding="utf-8")
    settings = load_settings(path)
    assert settings.language == "en"
    assert settings.model_size == "large-v3-turbo"


def test_save_creates_parent_dirs(tmp_path: Path):
    path = tmp_path / "deep" / "nested" / "config.toml"
    save_settings(path, Settings())
    assert path.exists()
