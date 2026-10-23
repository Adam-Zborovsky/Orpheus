# CPU transcription benchmark

This harness measures the existing faster-whisper backend without changing
Orpheus settings. It runs only against a caller-supplied local corpus and a
model already present in the local faster-whisper cache. The model is created
with `local_files_only=True`; a cache miss fails instead of downloading.

Do not run it while dictating or doing other CPU-heavy work. Choose an idle
window, close the daily Orpheus process if practical, and use a dedicated
result path outside this repository. The result contains corpus audio paths
and timing/quality metrics, so treat it as private. It does not write audio,
references, or recognized transcript text to the result. The harness does not
read or modify Orpheus settings, APPDATA, history, startup entries, or the daily
process.

## Corpus manifest

Use JSON with an `items` array, or JSON Lines with one object per nonempty line.
Each item has an `audio` path and a `reference` string. `language` is optional
and defaults to `auto`; accepted values are `auto`, `en`, and `he`. Relative
audio paths are relative to the manifest file. Supported audio containers and
codecs are those accepted by the installed faster-whisper/PyAV decoder.

```json
{
  "items": [
    {
      "audio": "english-short.wav",
      "reference": "Please send the invoice tomorrow.",
      "language": "en"
    },
    {
      "audio": "hebrew-mixed.m4a",
      "reference": "הפגישה ביום שלישי בשעה עשר",
      "language": "he"
    }
  ]
}
```

Keep the manifest, audio, references, and result outside the Git working tree.
The command refuses a manifest, input audio, or output path inside this project
directory. Do not add private corpus data to source control.

Build a representative corpus from speech you have permission to use. Include
short and long recordings, English, Hebrew, mixed language, numbers, names,
and varied speaking rates. Keep the same fixed corpus for every configuration.
Do not use the recognition output to revise references between configurations.

## Invocation

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_transcription.py `
  --corpus C:\private\orpheus-corpus\manifest.json `
  --output C:\private\orpheus-results\cpu-large-v3-turbo.json
```

The default model is `large-v3-turbo`; pass `--model` only to select another
already cached model ID or local faster-whisper model directory. `--runs`
accepts 1–20 measured corpus passes per configuration and defaults to 3. Every
configuration also performs one unmeasured warm-up transcription using the
first corpus item.

Preview the finite plan without opening the manifest, importing faster-whisper,
decoding audio, or constructing a model:

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_transcription.py `
  --corpus C:\private\orpheus-corpus\manifest.json `
  --output C:\private\orpheus-results\cpu-large-v3-turbo.json `
  --dry-run
```

There are 12 CPU configurations: `float32` and `int8`; 8, 16, and 32 CPU
threads; beam size 5 and 1. The model is loaded once per configuration with
`num_workers=1`. VAD stays enabled and the language comes from the manifest.
The harness records model load separately from warm-up and measured
transcription. Audio decoding time is also reported separately and is excluded
from transcription elapsed time and real-time factor.

The JSON output is atomically replaced after each configuration. If interrupted,
completed configurations remain available and the current one is marked
`running`. Result entries include the complete parameters, per-pass mean,
transcription elapsed time, real-time factor, aggregate and per-language WER,
process memory snapshot, hardware/runtime metadata, and failure type when a
configuration cannot run.
No audio or recognized/reference text is copied into the result. WER is
aggregated across all nonempty references and measured passes.

WER normalization is deliberately modest: Unicode NFKC normalization,
case-folding, removal of combining marks, punctuation-to-space conversion,
then whitespace tokenization. Digits stay literal, so `21` and `twenty-one`
are not treated as equivalent. WER is a corpus comparison signal, not a
human-quality judgment; review representative outputs if quality is being
compared. An empty reference is excluded from WER. No config is chosen or
applied automatically.

## Current status

Harness implemented; no real benchmark has been run. Model tuning remains
pending a user-chosen idle window and a representative private corpus. No
performance or accuracy recommendation is available yet.

## API references

- [faster-whisper README and CPU settings](https://github.com/SYSTRAN/faster-whisper/blob/master/README.md)
- [faster-whisper model constructor and local-only loading](https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/transcribe.py)
- [faster-whisper audio decoder](https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/audio.py)
