from pathlib import Path

from orpheus.settings import (DEFAULT_CLEANUP_PROMPT, Settings, load_settings,
                              save_settings, validate_settings, SettingsError)
import pytest


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
    assert settings.max_recording_seconds == 1800


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


def test_legacy_settings_get_recording_limit_and_preserve_device(tmp_path: Path):
    path = tmp_path / "legacy.toml"
    path.write_text('device = "cpu"\ncompute_type = "float32"\n', encoding="utf-8")
    loaded = load_settings(path)
    assert loaded.device == "cpu"
    assert loaded.compute_type == "float32"
    assert loaded.max_recording_seconds == 1800


@pytest.mark.parametrize("contents", [
    'hotkey = "<ctrl>+<alt>+<space>"\ninvalid = [\n',
    'hotkey = 42\n',
    'cleanup_enabled = "false"\n',
    'cpu_threads = true\n',
    'max_recording_seconds = 30\n',
    'vocabulary = ["valid", 3]\n',
    'device = "quantum"\n',
    'compute_type = "nonsense"\n',
    'ollama_url = "file:///tmp/nope"\n',
])
def test_invalid_config_raises_actionable_error_and_preserves_file(
        tmp_path: Path, contents: str):
    path = tmp_path / "bad.toml"
    path.write_text(contents, encoding="utf-8")
    original = path.read_bytes()
    with pytest.raises(SettingsError):
        load_settings(path)
    assert path.read_bytes() == original


def test_modifier_only_hotkey_is_valid_and_empty_is_not():
    settings = Settings(hotkey="<ctrl>+<alt>")
    validate_settings(settings)
    with pytest.raises(SettingsError, match="hotkey"):
        validate_settings(Settings(hotkey=""))


def test_custom_model_and_https_endpoint_round_trip(tmp_path: Path):
    settings = Settings(model_size="org/private-whisper:v2", ollama_url="https://llm.example.test/api")
    path = tmp_path / "config.toml"
    save_settings(path, settings)
    assert load_settings(path) == settings


@pytest.mark.parametrize("changes,field", [
    ({"cpu_threads": -1}, "cpu_threads"),
    ({"cpu_threads": 129}, "cpu_threads"),
    ({"num_workers": 0}, "num_workers"),
    ({"num_workers": 17}, "num_workers"),
    ({"max_recording_seconds": 59}, "max_recording_seconds"),
    ({"cleanup_enabled": 1}, "cleanup_enabled"),
    ({"vocabulary": "word"}, "vocabulary"),
    ({"model_size": "  "}, "model_size"),
])
def test_validation_names_invalid_field(changes: dict, field: str):
    with pytest.raises(SettingsError, match=field):
        validate_settings(Settings(**changes))


def test_atomic_replace_failure_keeps_existing_bytes(tmp_path: Path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_bytes(b"existing valid config")

    def fail_replace(_source, _target):
        raise OSError("replace failed")

    monkeypatch.setattr("orpheus.settings.os.replace", fail_replace)
    with pytest.raises(SettingsError, match="could not save"):
        save_settings(path, Settings(language="he"))
    assert path.read_bytes() == b"existing valid config"
    assert list(tmp_path.glob("*.tmp")) == []
