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
