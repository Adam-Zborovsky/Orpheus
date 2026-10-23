import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from scripts import benchmark_transcription as benchmark


def test_matrix_has_every_required_finite_configuration():
    configs = benchmark.configuration_matrix()
    assert len(configs) == 12
    assert {(c.compute_type, c.cpu_threads, c.beam_size) for c in configs} == {
        (precision, threads, beam)
        for precision in ("float32", "int8")
        for threads in (8, 16, 32)
        for beam in (5, 1)
    }


def test_json_manifest_resolves_relative_audio_path(tmp_path):
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"synthetic placeholder; parser does not decode")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"items": [{
        "audio": audio.name, "reference": "A reference.", "language": "en",
    }]}), encoding="utf-8")

    item, = benchmark.load_manifest(manifest)
    assert item.audio_path == audio.resolve()
    assert item.reference == "A reference."
    assert item.language == "en"


def test_jsonl_manifest_accepts_optional_auto_language(tmp_path):
    audio = tmp_path / "clip.flac"
    audio.write_bytes(b"synthetic placeholder")
    manifest = tmp_path / "corpus.jsonl"
    manifest.write_text(json.dumps({"audio": audio.name, "reference": "שלום"})
                        + "\n", encoding="utf-8")
    item, = benchmark.load_manifest(manifest)
    assert item.language == "auto"


def test_manifest_rejects_unknown_language_and_missing_audio(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"items": [{
        "audio": "missing.wav", "reference": "text", "language": "fr",
    }]}), encoding="utf-8")
    with pytest.raises(ValueError, match="language must be auto, en, or he"):
        benchmark.load_manifest(manifest)

    manifest.write_text(json.dumps({"items": [{
        "audio": "missing.wav", "reference": "text", "language": "en",
    }]}), encoding="utf-8")
    with pytest.raises(ValueError, match="audio file does not exist"):
        benchmark.load_manifest(manifest)


def test_private_paths_inside_repository_are_refused():
    with pytest.raises(ValueError, match="outside the project directory"):
        benchmark._outside_repository(Path(__file__), "Corpus manifest")


def test_wer_normalization_covers_english_punctuation_and_hebrew_marks():
    assert benchmark.word_error_count("HELLO, world!", "hello world.") == (0, 2)
    assert benchmark.word_error_count("שָׁלוֹם, עולם", "שלום עולם.") == (0, 2)
    assert benchmark.word_error_count("one two three", "one four five") == (2, 3)
    assert benchmark.word_error_count("", "unscored") == (None, 0)


def test_run_benchmark_records_metrics_and_never_writes_transcript_text(tmp_path,
                                                                        monkeypatch):
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"synthetic placeholder")
    corpus = [benchmark.CorpusItem(audio, "the quick fox", "en")]
    constructed = []
    calls = []

    class FakeModel:
        def __init__(self, model, **kwargs):
            constructed.append((model, kwargs))

        def transcribe(self, samples, **kwargs):
            calls.append(kwargs)
            return iter([SimpleNamespace(text=" the quick fox ")]), SimpleNamespace()

    def decode(path, sampling_rate):
        assert Path(path) == audio
        assert sampling_rate == 16_000
        return np.zeros(32_000, dtype=np.float32)

    monkeypatch.setattr(benchmark, "memory_snapshot", lambda: {"peak_rss_bytes": 123})
    output = tmp_path / "results.json"
    result = benchmark.run_benchmark(
        corpus, output, model_name="cached-test-model", runs=2,
        configurations=[benchmark.Configuration("float32", 8, 5)],
        model_factory=FakeModel, audio_decoder=decode,
    )

    row = result["results"][0]
    assert result["status"] == "complete"
    assert constructed == [("cached-test-model", {
        "device": "cpu", "compute_type": "float32", "cpu_threads": 8,
        "num_workers": 1, "local_files_only": True,
    })]
    assert len(calls) == 3  # one warm-up plus two measured passes
    assert all(call == {"language": "en", "beam_size": 5, "vad_filter": True}
               for call in calls)
    assert row["model_load_s"] >= 0
    assert row["warmup_transcription_s"] >= 0
    assert row["mean_transcription_seconds_per_pass"] >= 0
    assert row["real_time_factor"] is not None
    assert row["word_error_rate"] == 0
    assert row["quality_by_language"]["en"]["word_error_rate"] == 0
    assert row["memory_after_measured_transcription"] == {"peak_rss_bytes": 123}
    persisted = output.read_text(encoding="utf-8")
    assert "the quick fox" not in persisted
    assert json.loads(persisted)["status"] == "complete"


def test_dry_run_prints_plan_without_reading_manifest_or_loading_model(
        tmp_path, capsys, monkeypatch):
    manifest = tmp_path / "does-not-exist.json"
    output = tmp_path / "planned.json"
    monkeypatch.setattr(benchmark, "load_manifest",
                        lambda path: pytest.fail("dry-run read the manifest"))
    monkeypatch.setattr(benchmark, "run_benchmark",
                        lambda *args, **kwargs: pytest.fail("dry-run loaded a model"))
    assert benchmark.main(["--corpus", str(manifest), "--output", str(output),
                           "--dry-run"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert len(plan["configurations"]) == 12
    assert plan["corpus_manifest"] == str(manifest.resolve())
    assert not output.exists()
