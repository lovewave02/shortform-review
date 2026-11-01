"""Command line interface for local-only reviews."""

import argparse
from pathlib import Path
import sys

from .models import ReviewOptions, TOOL_VERSION
from .reports import write_reports
from .review import review


def _detectors(value: str) -> tuple[str, ...]:
    names = tuple(part.strip() for part in value.split(","))
    if any(name not in ("black", "silence") for name in names):
        raise argparse.ArgumentTypeError("use black, silence, or black,silence")
    return names


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Review a local video and optional UTF-8 SRT; no uploads or model calls.")
    parser.add_argument("video", type=Path, help="existing regular local video file")
    parser.add_argument("--srt", type=Path, help="optional UTF-8 or UTF-8-BOM SRT file (up to 10 MiB)")
    parser.add_argument("--out", required=True, type=Path, help="new output directory; parent must already exist")
    parser.add_argument("--detect", type=_detectors, default=(), metavar="black,silence", help="opt in to video black and/or audio silence decoding")
    parser.add_argument("--timeout", type=float, default=30.0, metavar="SECONDS", help="per-command timeout, greater than 0 and at most 3600 (default: 30)")
    parser.add_argument("--min-black-duration", type=float, default=0.5, metavar="SECONDS", help="minimum black candidate duration (default: 0.5)")
    parser.add_argument("--min-silence-duration", type=float, default=0.5, metavar="SECONDS", help="minimum silence candidate duration (default: 0.5)")
    parser.add_argument("--silence-db", type=float, default=-35.0, metavar="DB", help="silence threshold from -100 to 0 dB (default: -35)")
    parser.add_argument("--version", action="version", version=f"shortform-review {TOOL_VERSION}")
    args = parser.parse_args(argv)
    try:
        options = ReviewOptions(args.detect, args.timeout, args.min_black_duration,
                                args.min_silence_duration, args.silence_db)
    except ValueError as error:
        parser.error(str(error))
    # Fail before expensive analysis for known collisions; mkdir remains exclusive.
    if args.out.exists() or args.out.is_symlink():
        print("error: output path already exists; choose a new directory", file=sys.stderr)
        return 2
    report = review(args.video, args.srt, options)
    try:
        write_reports(report, args.out)
    except (OSError, ValueError) as error:
        print(f"error: could not write complete reports ({type(error).__name__}); no successful output is claimed", file=sys.stderr)
        return 2
    print(f"{report.state}: {len(report.findings)} finding(s); wrote report.json and report.html")
    return report.exit_code
