"""Strict, small SRT parser. Malformed blocks are reported, never dropped silently."""

from dataclasses import dataclass, field
import math
import re

from .models import Finding

_TIME = r"\d{2,}:\d{2}:\d{2}[,.]\d{3}"
_TIMING = re.compile(rf"^({_TIME})[ \t]*-->[ \t]*({_TIME})[ \t]*$")


@dataclass(frozen=True)
class Cue:
    index: int
    start: float
    end: float
    text: str
    position: int


@dataclass
class ParsedSRT:
    cues: list[Cue] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    complete: bool = True


def _seconds(timestamp: str) -> float:
    hours, minutes, rest = timestamp.replace(",", ".").split(":")
    seconds, millis = rest.split(".")
    if int(minutes) >= 60 or int(seconds) >= 60:
        raise ValueError("minutes and seconds must be below 60")
    value = int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000
    if not math.isfinite(value):
        raise ValueError("timestamp must be finite")
    return value


def parse_srt(text: str) -> ParsedSRT:
    result = ParsedSRT()
    normalized = text.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        result.complete = False
        result.findings.append(Finding("error", "srt.empty", "The SRT contains no cues."))
        return result
    indices: set[int] = set()
    for position, block in enumerate(re.split(r"\n(?:[ \t]*\n)+", normalized), 1):
        lines = block.splitlines()
        index = None
        try:
            if not re.fullmatch(r"[0-9]+", lines[0].strip()):
                raise ValueError("invalid cue index")
            index = int(lines[0].strip())
        except (ValueError, IndexError):
            result.complete = False
            result.findings.append(Finding("error", "srt.invalid_index", f"Block {position} has no numeric cue index."))
            continue
        match = _TIMING.fullmatch(lines[1].strip()) if len(lines) >= 2 else None
        try:
            if match is None:
                raise ValueError("missing or malformed timing")
            start, end = (_seconds(value) for value in match.groups())
        except (ValueError, OverflowError):
            result.complete = False
            result.findings.append(Finding("error", "srt.invalid_timing", f"Cue {index} has a missing or malformed timing line.", cue=index))
            continue
        cue = Cue(index, start, end, "\n".join(lines[2:]).strip(), position)
        result.cues.append(cue)
        if any(_TIMING.fullmatch(line.strip()) for line in lines[2:]):
            result.complete = False
            result.findings.append(Finding("error", "srt.missing_separator", "A timing line appears inside cue text; a blank separator between cues may be missing.", start, end, index))
        if index in indices:
            result.findings.append(Finding("warning", "srt.duplicate_index", f"Cue number {index} is repeated.", start, end, index))
        indices.add(index)
        if not cue.text:
            result.findings.append(Finding("warning", "srt.empty_text", "Cue has no subtitle text.", start, end, index))
    return result
