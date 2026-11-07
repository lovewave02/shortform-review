"""The public report contract and effective review options."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import math
from typing import Any

TOOL_VERSION = "0.3.0"
SCHEMA_VERSION = "1.1"


@dataclass(frozen=True)
class ReviewOptions:
    """Timeout applies to each external command, in seconds."""

    detect: tuple[str, ...] = ()
    timeout: float = 30.0
    min_black_duration: float = 0.5
    min_silence_duration: float = 0.5
    silence_db: float = -35.0

    def __post_init__(self) -> None:
        if isinstance(self.detect, str):
            raise ValueError("detect must be a sequence of detector names")
        names = tuple(self.detect)
        if any(name not in ("black", "silence") for name in names):
            raise ValueError("detect supports only black and silence")
        object.__setattr__(self, "detect", tuple(n for n in ("black", "silence") if n in names))
        for name in ("timeout", "min_black_duration", "min_silence_duration", "silence_db"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{name} must be a finite number")
        if not 0 < self.timeout <= 3600:
            raise ValueError("timeout must be greater than 0 and at most 3600 seconds")
        for name in ("min_black_duration", "min_silence_duration"):
            if not 0 < getattr(self, name) <= 86400:
                raise ValueError(f"{name} must be greater than 0 and at most 86400 seconds")
        if not -100 <= self.silence_db <= 0:
            raise ValueError("silence_db must be between -100 and 0 dB")

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["detect"] = list(self.detect)
        result["black_picture_threshold"] = 0.98
        result["black_pixel_threshold"] = 0.10
        result["timeout_scope"] = "per_external_command"
        return result


@dataclass
class Finding:
    severity: str
    rule: str
    message: str
    start_seconds: float | None = None
    end_seconds: float | None = None
    cue: int | None = None
    id: str = ""
    # Silence findings only, when a complete SRT was checked: where the interval
    # falls relative to subtitle timing. A hint for triage, never a verdict.
    subtitle_context: str | None = None


@dataclass
class Check:
    name: str
    status: str  # executed, skipped, or error
    detail: str


@dataclass(frozen=True)
class Source:
    basename: str
    sha256: str | None


@dataclass(frozen=True)
class MediaInfo:
    format: str
    duration_seconds: float
    video_duration_seconds: float
    audio_duration_seconds: float | None
    video_stream_index: int
    audio_stream_index: int | None
    video_duration_basis: str
    audio_duration_basis: str | None


@dataclass
class ReviewReport:
    options: ReviewOptions
    inputs: dict[str, Source] = field(default_factory=dict)
    checks: list[Check] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    media: MediaInfo | None = None
    tool_versions: dict[str, str] = field(default_factory=lambda: {"shortform_review": TOOL_VERSION})
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def state(self) -> str:
        if any(check.status == "error" for check in self.checks):
            return "incomplete"
        return "needs_review" if self.findings else "clean"

    @property
    def exit_code(self) -> int:
        return {"clean": 0, "needs_review": 1, "incomplete": 2}[self.state]

    def finalize(self) -> None:
        self.findings.sort(key=lambda item: (
            item.start_seconds is None,
            item.start_seconds if item.start_seconds is not None else 0,
            item.end_seconds if item.end_seconds is not None else 0,
            item.rule,
            item.cue if item.cue is not None else -1,
        ))
        for number, finding in enumerate(self.findings, 1):
            finding.id = f"F{number:04d}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "tool": {"name": "shortform-review", "version": TOOL_VERSION},
            "created_at": self.created_at,
            "state": self.state,
            "inputs": {name: asdict(source) for name, source in self.inputs.items()},
            "options": self.options.to_dict(),
            "tool_versions": dict(self.tool_versions),
            "media": asdict(self.media) if self.media else None,
            "checks": [asdict(check) for check in self.checks],
            "findings": [asdict(finding) for finding in self.findings],
            "scope": {
                "streams": "First video stream and first audio stream only.",
                "clean_means": "No findings in completed checks; not editorial approval or proof of full media integrity.",
                "detectors": "Black and silence intervals are review candidates and may be intentional.",
                "metadata": "Metadata-only review does not decode video or audio.",
                "unsupported": [
                    "Burned-in subtitle OCR", "Speech recognition or transcript alignment",
                    "Editorial quality or factual accuracy", "All-stream integrity validation",
                ],
                "subtitles": "External UTF-8 or UTF-8-BOM SRT only; no subtitle text is included in the report.",
                "subtitle_context": "Silence findings note their position relative to SRT cue timing (spans_cue_boundary, between_cues, within_cue, outside_cues) only when the SRT check executed completely. It is a triage hint; findings are never removed or downgraded.",
            },
        }
