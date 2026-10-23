"""Reproducible, local-only CPU transcription benchmark for Orpheus."""
from __future__ import annotations

import argparse
import gc
import importlib.metadata
import json
import os
import platform
import re
import sys
import tempfile
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SAMPLE_RATE = 16_000
PRECISIONS = ("float32", "int8")
THREAD_COUNTS = (8, 16, 32)
BEAM_SIZES = (5, 1)
REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class CorpusItem:
    audio_path: Path
    reference: str
    language: str


@dataclass(frozen=True)
class Configuration:
    compute_type: str
    cpu_threads: int
    beam_size: int


def configuration_matrix() -> list[Configuration]:
    return [Configuration(precision, threads, beam)
            for precision in PRECISIONS
            for threads in THREAD_COUNTS
            for beam in BEAM_SIZES]


def _outside_repository(path: Path, description: str) -> Path:
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(REPO_ROOT)
    except ValueError:
        return resolved
    raise ValueError(f"{description} must be outside the project directory: {resolved}")


def _manifest_rows(path: Path) -> list[dict[str, Any]]:
    try:
        content = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ValueError(f"Could not read corpus manifest ({type(exc).__name__})") from exc
    if path.suffix.lower() == ".jsonl":
        rows = []
        for number, line in enumerate(content.splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at line {number}: {exc.msg}") from exc
            rows.append(row)
        return rows
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON manifest: {exc.msg}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ValueError("JSON manifest must be an object with an 'items' array")
    return data["items"]


def load_manifest(path: Path) -> list[CorpusItem]:
    manifest_path = _outside_repository(path, "Corpus manifest")
    rows = _manifest_rows(manifest_path)
    if not rows:
        raise ValueError("Corpus manifest contains no audio items")
    items = []
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ValueError(f"Corpus item {index} must be a JSON object")
        audio_value, reference = row.get("audio"), row.get("reference")
        language = row.get("language", "auto")
        if not isinstance(audio_value, str) or not audio_value.strip():
            raise ValueError(f"Corpus item {index}: 'audio' must be a path string")
        if not isinstance(reference, str):
            raise ValueError(f"Corpus item {index}: 'reference' must be a string")
        if not isinstance(language, str) or language not in {"auto", "en", "he"}:
            raise ValueError(f"Corpus item {index}: language must be auto, en, or he")
        candidate = Path(audio_value).expanduser()
        if not candidate.is_absolute():
            candidate = manifest_path.parent / candidate
        audio_path = _outside_repository(candidate, f"Audio path for item {index}")
        if not audio_path.is_file():
            raise ValueError(f"Corpus item {index}: audio file does not exist")
        items.append(CorpusItem(audio_path, reference, language))
    return items


_SPACE_RE = re.compile(r"\s+")


def normalize_for_wer(text: str) -> list[str]:
    """NFKC, case-fold, remove combining marks, separate punctuation, split."""
    normalized = unicodedata.normalize("NFKC", text).casefold()
    output = []
    for char in normalized:
        category = unicodedata.category(char)
        if category.startswith("M"):
            continue
        output.append(" " if category.startswith("P") else char)
    return _SPACE_RE.sub(" ", "".join(output)).strip().split()


def word_error_count(reference: str, hypothesis: str) -> tuple[int | None, int]:
    """Return minimum word edit count and reference word count; empty ref -> None."""
    ref_words = normalize_for_wer(reference)
    hyp_words = normalize_for_wer(hypothesis)
    if not ref_words:
        return None, 0
    previous = list(range(len(hyp_words) + 1))
    for ref_index, ref_word in enumerate(ref_words, 1):
        current = [ref_index]
        for hyp_index, hyp_word in enumerate(hyp_words, 1):
            current.append(min(
                current[hyp_index - 1] + 1,
                previous[hyp_index] + 1,
                previous[hyp_index - 1] + (ref_word != hyp_word),
            ))
        previous = current
    return previous[-1], len(ref_words)


def _versions() -> dict[str, str | None]:
    packages = ("faster-whisper", "ctranslate2", "av", "numpy", "huggingface-hub")
    return {package: _package_version(package) for package in packages}


def _package_version(package: str) -> str | None:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return None


def _hardware_metadata() -> dict[str, Any]:
    return {
        "os": platform.platform(),
        "architecture": platform.machine(),
        "processor": platform.processor() or "unknown",
        "logical_cpu_count": os.cpu_count(),
        "python": platform.python_version(),
        "omp_num_threads_environment": os.environ.get("OMP_NUM_THREADS"),
        "libraries": _versions(),
    }


def memory_snapshot() -> dict[str, int | None]:
    """Return process working set/private bytes on Windows, peak RSS elsewhere."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class ProcessMemoryCountersEx(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCountersEx()
        counters.cb = ctypes.sizeof(counters)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = (
            wintypes.HANDLE, ctypes.POINTER(ProcessMemoryCountersEx), wintypes.DWORD,
        )
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        process = kernel32.GetCurrentProcess()
        ok = psapi.GetProcessMemoryInfo(
            process, ctypes.byref(counters), counters.cb
        )
        if not ok:
            raise OSError("GetProcessMemoryInfo failed")
        return {
            "working_set_bytes": int(counters.WorkingSetSize),
            "peak_working_set_bytes": int(counters.PeakWorkingSetSize),
            "private_bytes": int(counters.PrivateUsage),
        }

    import resource

    peak = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    if sys.platform != "darwin":
        peak *= 1024  # Linux and the BSDs report KiB; macOS reports bytes.
    return {"peak_rss_bytes": peak}


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n",
                prefix=f".{path.name}.", suffix=".tmp",
                dir=path.parent, delete=False) as temporary:
            temp_path = Path(temporary.name)
            json.dump(data, temporary, ensure_ascii=False, indent=2, allow_nan=False)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except OSError:
                pass


def _transcribe(model, audio, language: str, beam_size: int) -> str:
    segments, _info = model.transcribe(
        audio,
        language=None if language == "auto" else language,
        beam_size=beam_size,
        vad_filter=True,
    )
    return " ".join(segment.text.strip() for segment in segments).strip()


def _run_configuration(config: Configuration, model_name: str,
                       corpus: list[CorpusItem], runs: int,
                       decode_audio, whisper_model_class) -> dict[str, Any]:
    load_started = time.perf_counter()
    model = whisper_model_class(
        model_name,
        device="cpu",
        compute_type=config.compute_type,
        cpu_threads=config.cpu_threads,
        num_workers=1,
        local_files_only=True,
    )
    load_s = time.perf_counter() - load_started

    warmup_item = corpus[0]
    decode_started = time.perf_counter()
    warmup_audio = decode_audio(str(warmup_item.audio_path), sampling_rate=SAMPLE_RATE)
    warmup_decode_s = time.perf_counter() - decode_started
    warmup_started = time.perf_counter()
    _transcribe(model, warmup_audio, warmup_item.language, config.beam_size)
    warmup_s = time.perf_counter() - warmup_started
    del warmup_audio

    audio_duration_s = 0.0
    decode_s = 0.0
    transcription_s = 0.0
    edit_count = 0
    reference_word_count = 0
    scored_items = 0
    by_language: dict[str, dict[str, int]] = {}
    for _run_index in range(runs):
        for item in corpus:
            decode_started = time.perf_counter()
            audio = decode_audio(str(item.audio_path), sampling_rate=SAMPLE_RATE)
            decode_s += time.perf_counter() - decode_started
            duration_s = len(audio) / SAMPLE_RATE
            start = time.perf_counter()
            hypothesis = _transcribe(model, audio, item.language, config.beam_size)
            transcription_s += time.perf_counter() - start
            audio_duration_s += duration_s
            edits, words = word_error_count(item.reference, hypothesis)
            if edits is not None:
                edit_count += edits
                reference_word_count += words
                scored_items += 1
                language_stats = by_language.setdefault(
                    item.language, {"word_errors": 0, "reference_words": 0,
                                    "scored_item_runs": 0}
                )
                language_stats["word_errors"] += edits
                language_stats["reference_words"] += words
                language_stats["scored_item_runs"] += 1
            del audio, hypothesis

    try:
        memory = memory_snapshot()
    except Exception as exc:
        memory = {"unavailable": type(exc).__name__}
    del model
    gc.collect()
    return {
        "status": "complete",
        "compute_type": config.compute_type,
        "cpu_threads": config.cpu_threads,
        "beam_size": config.beam_size,
        "vad_filter": True,
        "model_load_s": load_s,
        "warmup_decode_s": warmup_decode_s,
        "warmup_transcription_s": warmup_s,
        "measured_runs": runs,
        "corpus_items": len(corpus),
        "measured_audio_seconds": audio_duration_s,
        "audio_decode_seconds": decode_s,
        "transcription_seconds": transcription_s,
        "mean_transcription_seconds_per_pass": transcription_s / runs,
        "real_time_factor": (transcription_s / audio_duration_s
                             if audio_duration_s else None),
        "word_error_rate": (edit_count / reference_word_count
                            if reference_word_count else None),
        "word_errors": edit_count if reference_word_count else None,
        "reference_words": reference_word_count if reference_word_count else None,
        "scored_item_runs": scored_items,
        "quality_by_language": {
            language: {
                **stats,
                "word_error_rate": stats["word_errors"] / stats["reference_words"],
            }
            for language, stats in by_language.items()
        },
        "quality_note": (None if reference_word_count else
                         "No nonempty reference text was available; WER omitted."),
        "memory_after_measured_transcription": memory,
    }


def run_benchmark(corpus: list[CorpusItem], output: Path,
                  model_name: str = "large-v3-turbo", runs: int = 3,
                  configurations: Iterable[Configuration] | None = None,
                  model_factory=None, audio_decoder=None) -> dict[str, Any]:
    """Run the fixed CPU matrix; injected dependencies support offline tests."""
    if not corpus:
        raise ValueError("Corpus manifest contains no audio items")
    if not 1 <= runs <= 20:
        raise ValueError("runs must be from 1 to 20")
    if not model_name.strip():
        raise ValueError("model name must not be empty")
    if output.suffix.lower() != ".json":
        raise ValueError("Results output must use a .json extension")
    if model_factory is None or audio_decoder is None:
        from faster_whisper import WhisperModel
        from faster_whisper.audio import decode_audio

        model_factory = model_factory or WhisperModel
        audio_decoder = audio_decoder or decode_audio
    output_path = _outside_repository(output, "Results output")
    if output_path in {item.audio_path.resolve() for item in corpus}:
        raise ValueError("Results output must not overwrite a corpus audio file")
    selected_configurations = (list(configurations) if configurations is not None
                               else configuration_matrix())
    if not selected_configurations:
        raise ValueError("No benchmark configurations were selected")
    results: dict[str, Any] = {
        "schema_version": 1,
        "status": "running",
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "model": model_name,
        "device": "cpu",
        "sample_rate": SAMPLE_RATE,
        "runs_per_configuration": runs,
        "corpus_item_count": len(corpus),
        "corpus_audio_paths": [str(item.audio_path) for item in corpus],
        "normalization": "NFKC, case-fold, remove Unicode combining marks, replace punctuation with spaces, split whitespace; digits remain literal.",
        "configuration_matrix": [config.__dict__ for config in selected_configurations],
        "environment": _hardware_metadata(),
        "results": [],
    }
    atomic_write_json(output_path, results)
    for index, config in enumerate(selected_configurations, 1):
        print(f"[{index}/{len(selected_configurations)}] {config.compute_type}, "
              f"threads={config.cpu_threads}, "
              f"beam={config.beam_size}", flush=True)
        entry = {"status": "running", **config.__dict__}
        results["results"].append(entry)
        atomic_write_json(output_path, results)
        try:
            entry.update(_run_configuration(config, model_name, corpus, runs,
                                            audio_decoder, model_factory))
        except Exception as exc:
            entry.update({"status": "failed", "failure_phase": "load_or_transcribe",
                          "failure_type": type(exc).__name__})
            print(f"  failed ({type(exc).__name__})", flush=True)
        else:
            print(f"  load={entry['model_load_s']:.2f}s "
                  f"mean={entry['mean_transcription_seconds_per_pass']:.2f}s/pass "
                  f"rtf={entry['real_time_factor']!s} "
                  f"wer={entry['word_error_rate']!s}", flush=True)
        atomic_write_json(output_path, results)
    results["status"] = "complete" if all(
        row["status"] == "complete" for row in results["results"]
    ) else "completed_with_failures"
    atomic_write_json(output_path, results)
    return results


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Benchmark a cached faster-whisper model on an explicit local corpus."
    )
    parser.add_argument("--corpus", type=Path, required=True,
                        help="Local JSON or JSONL manifest with audio/reference/language.")
    parser.add_argument("--output", type=Path, required=True,
                        help="Explicit result JSON path outside the Orpheus project.")
    parser.add_argument("--model", default="large-v3-turbo",
                        help="Cached faster-whisper model ID or local model directory.")
    parser.add_argument("--runs", type=int, default=3,
                        help="Measured passes per matrix configuration (1 to 20).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the planned matrix without model/audio imports.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not 1 <= args.runs <= 20:
        parser.error("--runs must be from 1 to 20")
    if not args.model.strip():
        parser.error("--model must not be empty")
    try:
        corpus_path = _outside_repository(args.corpus, "Corpus manifest")
        output_path = _outside_repository(args.output, "Results output")
    except ValueError as exc:
        parser.error(str(exc))
    if output_path.suffix.lower() != ".json":
        parser.error("--output must use a .json extension")
    if args.dry_run:
        print(json.dumps({
            "model": args.model,
            "device": "cpu",
            "runs_per_configuration": args.runs,
            "configurations": [config.__dict__ for config in configuration_matrix()],
            "corpus_manifest": str(corpus_path),
            "output": str(output_path),
        }, indent=2))
        return 0
    try:
        corpus = load_manifest(corpus_path)
        if output_path == corpus_path:
            raise ValueError("Results output must not overwrite the corpus manifest")
        result = run_benchmark(corpus, output_path, args.model, args.runs)
    except KeyboardInterrupt:
        print("Interrupted; completed configuration results are saved.", file=sys.stderr)
        return 130
    except ValueError as exc:
        parser.error(str(exc))
    return 0 if result["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
