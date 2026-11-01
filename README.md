# shortform-review

Review local short videos and SRT subtitles in one chronological report. Find subtitle timing problems and optional black-screen or silence candidates before manually reviewing an export.

숏폼 영상과 SRT를 로컬에서 검사하고, 시간순 HTML/JSON 보고서로 검토할 구간을 모읍니다. 자동 탐지는 검토 후보이며 최종 편집 판단은 사람이 합니다.

This is an early tool being improved through real video-production use. It does not edit, upload, or approve videos. All processing runs locally; no model, API key, or cloud service is required.

## Install

Requires Python 3.10+ and `ffprobe` on PATH. Optional black/silence analysis also requires `ffmpeg`. Use FFmpeg 6.1 or newer and install it separately using your platform's package manager.

```sh
git clone https://github.com/lovewave02/shortform-review.git
cd shortform-review
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
shortform-review --help
```

The Python runtime uses the standard library. Setuptools is used for packaging.

## Review a video

```sh
mkdir -p .local-runs
shortform-review final.mp4 --srt captions.srt --out .local-runs/first-review
shortform-review final.mp4 --srt captions.srt --out .local-runs/full-review --detect black,silence
```

Each output directory must be new, and its parent must already exist. Open the generated HTML report, choose the matching local video in its player, and seek to candidate timestamps. JSON provides the same findings for scripts. Media and subtitle text are not copied into the report. Reports still contain filenames, hashes, and analysis results: keep them local unless you have reviewed their contents for sharing.

The default run inspects video metadata and, when supplied, UTF-8 SRT structure and timing. Black and silence detectors are opt-in. A run without SRT does not check subtitles. Burned-in captions are not extracted or checked. `.local-runs/` is gitignored; use a different run directory for each review.

| Check | Candidates or failures |
| --- | --- |
| SRT parsing | Empty file, invalid cue index/timing, malformed cue boundaries |
| SRT timing | Reversed/zero duration, out-of-order cues, overlaps, video-boundary overflow |
| SRT content structure | Duplicate indices and empty cue text |
| Black detection | Near-black intervals lasting at least 0.5 s; change with `--min-black-duration` |
| Silence detection | Audio below −35 dB for at least 0.5 s; change with `--silence-db` and `--min-silence-duration` |

| Exit | State | Meaning |
| --- | --- | --- |
| 0 | `clean` | No findings in the checks actually run |
| 1 | `needs_review` | Review candidates were found |
| 2 | `incomplete` | Input, tool, or analysis failure prevented a complete run |

Always read the check coverage, including skipped and failed checks. `clean` is not a publishing or delivery approval. Silence and black frames may be intentional. Metadata-only review does not establish that every frame decodes successfully. Analysis failures normally produce an `incomplete` report; invalid command arguments or output-write failures can exit 2 without complete reports.

## Python API

```python
from shortform_review import ReviewOptions, review
from shortform_review.reports import write_reports

report = review(
    "final.mp4",
    subtitles="captions.srt",
    options=ReviewOptions(detect=("black", "silence")),
)
write_reports(report, "new-review-directory")
payload = report.to_dict()
```

Reports bind inputs to SHA-256 hashes and record effective settings and check coverage. Re-run after changing a video, subtitle file, or detector setting; preserve earlier reports for comparison.

## Improve it while producing videos

1. Inspect the exact final export and its matching SRT.
2. Review candidate intervals and record confirmed issues, intentional effects/false positives, or unresolved items separately from the generated report.
3. Reproduce one tool defect with a synthetic fixture, update the responsible module, and run the relevant tests.
4. Create a new report after the change. Compare input hashes, settings, and coverage before comparing findings.

The bundled [shortform-review-manager skill](skills/shortform-review-manager/SKILL.md) supports this workflow in Korean. Link that directory into your agent's skill directory to use it; do not copy production assets into this repository.

## Development

```sh
python -m pip install -e .
python -m unittest discover -s tests -v
```

Tests use synthetic data. FFmpeg integration tests require FFmpeg/ffprobe; inspect skipped tests when those programs are absent. The implementation separates data models, SRT parsing, media inspection, review rules, report rendering, and CLI orchestration. New checks should return structured findings with coverage and failures visible to both API and CLI callers.

For bug reports, include the tool version, command options, expected/actual behavior, and a minimal synthetic reproduction. Avoid real client footage, private subtitles, absolute personal paths, or unreviewed reports.

## Limits

- No OCR, transcription, speech/subtitle semantic alignment, aesthetic judgment, or rights verification.
- SRT sidecars only; no extraction of embedded subtitle tracks.
- Only the first video and audio streams are checked. SRT must be UTF-8 or UTF-8-BOM, at most 10 MiB; re-export other encodings as UTF-8 before review.
- Local supported media containers only; remote URLs and playlists are not a supported input workflow.
- Detector thresholds are heuristics. Do not change them solely to make a report appear clean.
- HTML playback depends on your browser's support for the selected video's codecs.

MIT license. FFmpeg is a separate dependency with its own license; it is not bundled.
