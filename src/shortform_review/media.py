"""Bounded argv-only calls to local FFmpeg tools; no network or playlists."""

import json
import math
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any

from .models import MediaInfo, ReviewOptions

# These demuxers handle ordinary single-file containers, not playlists or image sequences.
FORMAT_WHITELIST = "mov,matroska,webm,avi,flv,mpeg,mpegts,ogg,asf"
_FORMATS = set(FORMAT_WHITELIST.split(",")) | {"mp4", "m4a", "3gp", "3g2", "mj2"}


class MediaError(Exception):
    """A safe, path-free diagnostic suitable for a shareable report."""


def _tool(name: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        raise MediaError(f"{name} is not available on PATH.")
    return executable


def _run(argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=timeout,
                              check=False, shell=False)
    except subprocess.TimeoutExpired as error:
        raise MediaError(f"{Path(argv[0]).name} exceeded the {timeout:g}-second command timeout.") from error
    except OSError as error:
        raise MediaError(f"{Path(argv[0]).name} could not be started.") from error


def _version(executable: str, timeout: float) -> str:
    result = _run([executable, "-version"], timeout)
    parts = result.stdout.splitlines()[0].split() if result.stdout.splitlines() else []
    # Do not store build flags, compiler details, or executable paths.
    if result.returncode == 0 and len(parts) >= 3 and parts[1] == "version":
        return parts[2] if re.fullmatch(r"[A-Za-z0-9.+_~:-]+", parts[2]) else "unknown"
    return "unknown"


def _duration(value: Any) -> float | None:
    if value in (None, "", "N/A"):
        return None
    if isinstance(value, bool):
        raise MediaError("ffprobe returned an invalid duration.")
    try:
        duration = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise MediaError("ffprobe returned an invalid duration.") from error
    if not math.isfinite(duration) or duration < 0:
        raise MediaError("ffprobe returned a non-finite or negative duration.")
    return duration if duration > 0 else None


def _stream_index(stream: dict[str, Any]) -> int:
    value = stream.get("index")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MediaError("ffprobe returned an invalid stream index.")
    return value


def probe(path: Path, options: ReviewOptions, versions: dict[str, str]) -> MediaInfo:
    executable = _tool("ffprobe")
    versions["ffprobe"] = _version(executable, options.timeout)
    result = _run([
        executable, "-v", "error", "-protocol_whitelist", "file,pipe",
        "-format_whitelist", FORMAT_WHITELIST,
        "-show_entries", "format=duration,format_name:stream=index,codec_type,duration",
        "-of", "json", "-i", str(path),
    ], options.timeout)
    if result.returncode != 0 or result.stderr.strip():
        raise MediaError(f"ffprobe could not inspect the local video (exit code {result.returncode}).")
    try:
        data = json.loads(result.stdout)
        streams = data["streams"]
        container = data.get("format", {})
        format_name = container.get("format_name", "")
        if not isinstance(format_name, str) or not format_name or not set(format_name.split(",")) <= _FORMATS:
            raise MediaError("Unsupported media format; playlists and image sequences are not accepted.")
        if not isinstance(streams, list) or not all(isinstance(stream, dict) for stream in streams):
            raise MediaError("ffprobe returned invalid stream metadata.")
        video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
        audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
        if video is None:
            raise MediaError("The input has no video stream.")
        container_duration = _duration(container.get("duration"))
        video_duration = _duration(video.get("duration"))
        audio_duration = _duration(audio.get("duration")) if audio else None
        video_basis = "stream" if video_duration is not None else "container_fallback"
        audio_basis = ("stream" if audio_duration is not None else "container_fallback") if audio else None
        video_duration = video_duration or container_duration
        if video_duration is None:
            raise MediaError("A finite positive video duration could not be established.")
        if audio:
            audio_duration = audio_duration or container_duration
            if audio_duration is None:
                raise MediaError("A finite positive audio duration could not be established.")
        duration = max(value for value in (container_duration, video_duration, audio_duration) if value is not None)
        return MediaInfo(format_name, duration, video_duration, audio_duration,
                         _stream_index(video), _stream_index(audio) if audio else None,
                         video_basis, audio_basis)
    except (KeyError, TypeError, AttributeError, json.JSONDecodeError) as error:
        raise MediaError("ffprobe returned malformed metadata.") from error


def _span(start: str | float, end: str | float) -> tuple[float, float]:
    try:
        first, last = float(start), float(end)
    except (ValueError, OverflowError) as error:
        raise MediaError("The detector returned an invalid interval.") from error
    if not math.isfinite(first) or not math.isfinite(last) or last < first:
        raise MediaError("The detector returned an invalid interval.")
    return max(0.0, first), max(0.0, last)


def _parse_spans(output: str, kind: str, duration: float, minimum: float) -> list[tuple[float, float]]:
    """Read filter metadata, including an open interval ending at decoded EOF."""
    spans = []
    pending: float | None = None
    for line in output.splitlines():
        if line.startswith(f"lavfi.{kind}_start="):
            if pending is not None:
                raise MediaError(f"The {kind} detector returned an unmatched start.")
            value = line.split("=", 1)[1]
            pending = _span(value, value)[0]
        elif line.startswith(f"lavfi.{kind}_end="):
            if pending is None:
                raise MediaError(f"The {kind} detector returned an unmatched end.")
            span = _span(pending, line.split("=", 1)[1])
            if span[1] - span[0] + 0.001 >= minimum:
                spans.append(span)
            pending = None
    if pending is not None:
        span = _span(pending, duration)
        if span[1] - span[0] + 0.001 >= minimum:
            spans.append(span)
    return spans


def parse_black(output: str, duration: float, minimum: float) -> list[tuple[float, float]]:
    return _parse_spans(output, "black", duration, minimum)


def parse_silence(output: str, duration: float, minimum: float) -> list[tuple[float, float]]:
    return _parse_spans(output, "silence", duration, minimum)


def _decoded_duration(output: str, detector: str) -> float:
    """Read actual decoded packet ends, not version-dependent progress times.

    FFmpeg 6 progress can end at the last packet PTS, while FFmpeg 8 includes
    its duration. framecrc provides both fields and their exact time base.
    """
    progress = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
    time_base: tuple[int, int] | None = None
    media_type: str | None = None
    maximum_end: int | None = None
    try:
        frames = int(progress.get("frame", "0"))
        for line in output.splitlines():
            if line.startswith("#tb "):
                match = re.fullmatch(r"#tb\s+0:\s*(\d+)/(\d+)", line)
                if match is None or time_base is not None:
                    raise ValueError("unexpected time base")
                time_base = int(match[1]), int(match[2])
                if min(time_base) <= 0:
                    raise ValueError("nonpositive time base")
            elif line.startswith("#media_type "):
                match = re.fullmatch(r"#media_type\s+0:\s*(video|audio)", line)
                if match is None or media_type is not None:
                    raise ValueError("unexpected media type")
                media_type = match[1]
            elif re.match(r"^\s*\d+\s*,", line):
                fields = line.split(",")
                if len(fields) < 6:
                    raise ValueError("incomplete packet record")
                stream, _dts, pts, packet_duration, size = (int(value) for value in fields[:5])
                if stream != 0 or packet_duration <= 0 or size <= 0:
                    raise ValueError("invalid decoded packet")
                end = pts + packet_duration
                maximum_end = end if maximum_end is None else max(maximum_end, end)
        if time_base is None or maximum_end is None:
            raise ValueError("missing decoded packets")
        duration = maximum_end * time_base[0] / time_base[1]
    except (ValueError, OverflowError) as error:
        raise MediaError("ffmpeg did not confirm valid decoded packet timing.") from error
    expected_type = "video" if detector == "black" else "audio"
    if (progress.get("progress") != "end" or media_type != expected_type
            or not math.isfinite(duration) or duration <= 0):
        raise MediaError("ffmpeg did not confirm a complete, nonempty decoded stream.")
    if detector == "black" and frames <= 0:
        raise MediaError("ffmpeg decoded no video frames.")
    return duration


def detect(path: Path, detector: str, info: MediaInfo, options: ReviewOptions,
           versions: dict[str, str]) -> list[tuple[float, float]]:
    executable = _tool("ffmpeg")
    if "ffmpeg" not in versions:
        versions["ffmpeg"] = _version(executable, options.timeout)
    argv = [executable, "-hide_banner", "-nostdin", "-v", "error", "-xerror",
            "-abort_on", "empty_output", "-progress", "pipe:1", "-nostats",
            "-protocol_whitelist", "file,pipe", "-format_whitelist", FORMAT_WHITELIST,
            "-i", str(path)]
    if detector == "black":
        argv += ["-map", "0:v:0", "-an", "-sn", "-dn", "-vf",
                 "blackdetect=d=0:pix_th=0.1:pic_th=0.98,"
                 "metadata=mode=print:file=-", "-c:v", "wrapped_avframe",
                 "-fps_mode", "passthrough", "-enc_time_base:v", "-1"]
    elif detector == "silence" and info.audio_duration_seconds is not None:
        argv += ["-map", "0:a:0", "-vn", "-sn", "-dn", "-af",
                 f"silencedetect=noise={options.silence_db:g}dB:d={options.min_silence_duration:g},"
                 "ametadata=mode=print:file=-", "-c:a", "pcm_s16le"]
    else:
        raise MediaError("The requested detector has no supported input stream.")
    # framecrc keeps only packet timing and a checksum, not media payloads.
    # wrapped_avframe avoids re-encoding video; PCM exposes audio sample counts.
    argv += ["-f", "framecrc", "-"]
    result = _run(argv, options.timeout)
    if result.returncode != 0 or result.stderr.strip():
        raise MediaError(f"ffmpeg {detector} analysis failed (exit code {result.returncode}); decoding was not verified.")
    decoded_duration = _decoded_duration(result.stdout, detector)
    if detector == "black":
        return parse_black(result.stdout, min(decoded_duration, info.video_duration_seconds), options.min_black_duration)
    return parse_silence(result.stdout, min(decoded_duration, info.audio_duration_seconds), options.min_silence_duration)
