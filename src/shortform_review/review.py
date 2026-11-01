"""Review orchestration. Missing evidence always remains visible."""

import hashlib
from pathlib import Path
import stat

from . import media
from .models import Check, Finding, ReviewOptions, ReviewReport, Source
from .rules import subtitle_context, subtitle_findings
from .srt import parse_srt

MAX_SRT_BYTES = 10 * 1024 * 1024


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _source(value: str | Path) -> tuple[Path, Source]:
    path = Path(value).expanduser().resolve(strict=True)
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("Input must be a regular local file.")
    return path, Source(path.name, _hash(path))


def review(video: str | Path, subtitles: str | Path | None = None,
           options: ReviewOptions | None = None) -> ReviewReport:
    """Inspect local inputs and return a report, including on ordinary analysis errors.

    This API neither writes reports nor uploads media. Invalid ReviewOptions raise
    ValueError during their construction. Input and tool errors produce incomplete.
    """
    options = options if options is not None else ReviewOptions()
    if not isinstance(options, ReviewOptions):
        raise TypeError("options must be ReviewOptions")
    report = ReviewReport(options)
    checked_cues = None  # set only when the SRT check executed completely
    paths: dict[str, Path] = {}
    for name, value in (("video", video), ("subtitles", subtitles)):
        if value is None:
            continue
        try:
            path, source = _source(value)
            paths[name] = path
            report.inputs[name] = source
        except (OSError, ValueError, RuntimeError):
            report.inputs[name] = Source(Path(value).name, None)
            report.checks.append(Check(f"{name}_input", "error", "Input must be an existing, readable, regular local file."))

    if "video" in paths:
        try:
            report.media = media.probe(paths["video"], options, report.tool_versions)
            report.checks.append(Check("metadata", "executed", "Video stream and finite positive duration confirmed; this does not decode the media."))
        except media.MediaError as error:
            report.checks.append(Check("metadata", "error", str(error)))
    else:
        report.checks.append(Check("metadata", "skipped", "Video input was unavailable."))

    if subtitles is None:
        report.checks.append(Check("subtitles", "skipped", "No SRT supplied; subtitles were not checked."))
    elif "subtitles" not in paths:
        report.checks.append(Check("subtitles", "skipped", "SRT input was unavailable."))
    else:
        try:
            with paths["subtitles"].open("rb") as source:
                content = source.read(MAX_SRT_BYTES + 1)
            if len(content) > MAX_SRT_BYTES:
                raise ValueError("SRT exceeds the 10 MiB input limit.")
            parsed = parse_srt(content.decode("utf-8-sig"))
            report.findings.extend(parsed.findings)
            report.findings.extend(subtitle_findings(parsed.cues, report.media.video_duration_seconds if report.media else None))
            if not parsed.complete:
                report.checks.append(Check("subtitles", "error", "Malformed or empty SRT; valid cues were checked but coverage is incomplete."))
            elif report.media is None:
                report.checks.append(Check("subtitles", "error", "SRT syntax and timing checked; video-boundary checks were unavailable."))
            else:
                report.checks.append(Check("subtitles", "executed", f"Checked {len(parsed.cues)} SRT cues for syntax, order, overlap, and video bounds."))
                checked_cues = parsed.cues
        except UnicodeDecodeError:
            report.checks.append(Check("subtitles", "error", "SRT must be UTF-8 or UTF-8 with BOM; no encoding was guessed."))
        except ValueError as error:
            report.checks.append(Check("subtitles", "error", str(error)))
        except OSError:
            report.checks.append(Check("subtitles", "error", "The SRT could not be read."))

    for detector in ("black", "silence"):
        if detector not in options.detect:
            report.checks.append(Check(detector, "skipped", "Detector was not requested."))
        elif report.media is None:
            report.checks.append(Check(detector, "skipped", "Video metadata was unavailable."))
        elif detector == "silence" and report.media.audio_stream_index is None:
            report.checks.append(Check(detector, "skipped", "No audio stream exists; silence was not checked."))
        else:
            try:
                spans = media.detect(paths["video"], detector, report.media, options, report.tool_versions)
                for start, end in spans:
                    description = "Near-black video" if detector == "black" else "Audio below the configured silence threshold"
                    finding = Finding("warning", f"media.{detector}", f"{description}; review whether this interval is intentional.", start, end)
                    if detector == "silence" and checked_cues is not None:
                        finding.subtitle_context = subtitle_context(start, end, checked_cues)
                    report.findings.append(finding)
                report.checks.append(Check(detector, "executed", f"Decoded the selected {'video' if detector == 'black' else 'audio'} stream; found {len(spans)} review candidate(s)."))
            except media.MediaError as error:
                report.checks.append(Check(detector, "error", str(error)))

    changed: list[str] = []
    for name, path in paths.items():
        try:
            if not stat.S_ISREG(path.stat().st_mode) or _hash(path) != report.inputs[name].sha256:
                changed.append(name)
        except OSError:
            changed.append(name)
    if changed:
        report.checks.append(Check("source_integrity", "error", "Source changed or became unreadable during review: " + ", ".join(changed) + ". Re-run on stable inputs."))
    elif paths:
        report.checks.append(Check("source_integrity", "executed", "Source SHA-256 hashes matched before and after analysis."))
    else:
        report.checks.append(Check("source_integrity", "skipped", "No valid source was available to hash."))
    report.finalize()
    return report
