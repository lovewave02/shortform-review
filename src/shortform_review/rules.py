"""Timing rules produce candidates for a human to review."""

from .models import Finding
from .srt import Cue


def subtitle_findings(cues: list[Cue], video_duration: float | None) -> list[Finding]:
    findings: list[Finding] = []
    previous: Cue | None = None
    for cue in cues:
        if cue.end < cue.start:
            findings.append(Finding("warning", "srt.reversed_time", "Cue ends before it starts.", cue.start, cue.end, cue.index))
        elif cue.end == cue.start:
            findings.append(Finding("warning", "srt.zero_duration", "Cue has zero duration.", cue.start, cue.end, cue.index))
        if video_duration is not None and max(cue.start, cue.end) > video_duration + 0.001:
            findings.append(Finding("warning", "srt.out_of_bounds", "Cue extends beyond the selected video's duration.", cue.start, cue.end, cue.index))
        if previous is not None and cue.start < previous.start:
            findings.append(Finding("warning", "srt.out_of_order", "Cue starts before the preceding cue in the SRT file.", cue.start, cue.end, cue.index))
        previous = cue

    # The greatest active end detects nested overlaps without quadratic pair enumeration.
    covering: Cue | None = None
    for cue in sorted((cue for cue in cues if cue.end > cue.start), key=lambda cue: (cue.start, cue.position)):
        if covering is not None and cue.start < covering.end:
            findings.append(Finding("warning", "srt.overlap", f"Cue overlaps cue {covering.index}; simultaneous captions may be intentional.", cue.start, cue.end, cue.index))
        if covering is None or cue.end > covering.end:
            covering = cue
    return findings


# Absorbs float representation error only (SRT times are whole milliseconds).
_EDGE_EPSILON = 1e-6

SUBTITLE_CONTEXTS = ("spans_cue_boundary", "between_cues", "within_cue", "outside_cues")


def subtitle_context(start: float, end: float, cues: list[Cue]) -> str:
    """Locate an interval relative to positive-duration cues.

    spans_cue_boundary: a cue start or end lies inside the interval (inclusive),
    which is typical of a pause between spoken lines. between_cues: the interval
    sits wholly in a gap with cues on both sides. within_cue: a caption is shown
    for the whole interval. outside_cues: before the first or after the last
    positive-duration cue, or no such cue exists.
    """
    timed = [cue for cue in cues if cue.end > cue.start]
    if any(start - _EDGE_EPSILON <= edge <= end + _EDGE_EPSILON for cue in timed for edge in (cue.start, cue.end)):
        return "spans_cue_boundary"
    if any(cue.start < end and cue.end > start for cue in timed):
        return "within_cue"
    if any(cue.end < start for cue in timed) and any(cue.start > end for cue in timed):
        return "between_cues"
    return "outside_cues"
