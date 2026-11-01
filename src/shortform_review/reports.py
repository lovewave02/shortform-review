"""Standalone, escaped HTML and JSON reports; no media is copied or embedded."""

import base64
import hashlib
from html import escape
import json
import math
import os
from pathlib import Path

from .models import ReviewReport

_CSS = """
:root{color-scheme:light dark;font:16px/1.55 system-ui,sans-serif;background:#f5f6f8;color:#20252e}
body{max-width:1120px;margin:auto;padding:32px 20px}h1{margin:0 0 8px;font-size:2rem}
h2{font-size:1.3rem;margin-top:0}p{margin:8px 0}section,header{background:#fff;border:1px solid #d5dbe3;border-radius:12px;padding:22px;margin-bottom:20px}
.state{display:inline-block;font-weight:750;padding:4px 12px;border-radius:6px;background:#e8edf4}
.clean{background:#dbf2e7;color:#145438}.needs_review{background:#fff0c6;color:#714b00}.incomplete{background:#ffe2e1;color:#862d29}
.muted{color:#566170}.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:.92rem}
th,td{padding:11px 9px;text-align:left;border-bottom:1px solid #d5dbe3;vertical-align:top}th{white-space:nowrap}
code{font-size:.87em;overflow-wrap:anywhere}button,input{font:inherit}button{cursor:pointer;padding:5px 10px;border:1px solid #8c99ac;border-radius:5px;background:#edf2fa;color:#193d72}
video{display:block;max-width:100%;max-height:65vh;margin:16px auto;background:#111}li{margin:5px 0}.label{font-weight:650}
@media(prefers-color-scheme:dark){:root{background:#111720;color:#e6ecf3}section,header{background:#192330;border-color:#465162}.muted{color:#aebccd}td,th{border-color:#465162}}
""".strip()

_SCRIPT = """
(() => {
  const chooser = document.getElementById('local-file');
  const player = document.getElementById('player');
  const status = document.getElementById('player-status');
  let objectUrl = null;
  let pendingSeek = null;
  chooser.addEventListener('change', () => {
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = null;
    pendingSeek = null;
    const file = chooser.files[0];
    if (!file) {
      player.removeAttribute('src'); player.load();
      status.textContent = 'No video selected. Choose the matching local video.';
      return;
    }
    objectUrl = URL.createObjectURL(file);
    player.src = objectUrl;
    player.load();
    status.textContent = 'Selected locally: ' + file.name + '. Identity has not been verified.';
  });
  function seek(seconds) {
    if (Number.isFinite(player.duration)) {
      player.currentTime = Math.min(Math.max(0, seconds), player.duration);
      pendingSeek = null;
      status.textContent = 'Moved to ' + player.currentTime.toFixed(3) + ' s. Selected video identity has not been verified.';
      player.scrollIntoView({block: 'center'});
    } else { pendingSeek = seconds; }
  }
  document.querySelectorAll('button[data-seek]').forEach(button => {
    button.addEventListener('click', () => {
      if (!objectUrl) { status.textContent = 'Choose the matching local video first.'; chooser.focus(); return; }
      const seconds = Number(button.dataset.seek);
      if (Number.isFinite(seconds)) seek(seconds);
    });
  });
  player.addEventListener('loadedmetadata', () => { if (pendingSeek !== null) seek(pendingSeek); });
  player.addEventListener('error', () => { status.textContent = 'This browser could not play the selected file. Use a compatible local player.'; });
  window.addEventListener('pagehide', () => { if (objectUrl) URL.revokeObjectURL(objectUrl); });
})();
""".strip()


def _csp_hash(content: str) -> str:
    return base64.b64encode(hashlib.sha256(content.encode("utf-8")).digest()).decode("ascii")


def _timestamp(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    # Split before multiplying so even a finite but extreme malformed cue can
    # be displayed as an out-of-bounds finding without overflowing to infinity.
    whole = math.floor(seconds)
    millis = round((seconds - whole) * 1000)
    if millis == 1000:
        whole, millis = whole + 1, 0
    hours, remainder = divmod(whole, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def render_html(report: ReviewReport) -> str:
    """Render a self-contained report with only static, hash-authorized JavaScript."""
    data = report.to_dict()
    state = report.state
    captions = {
        "clean": "No candidates in completed checks. Review the coverage below.",
        "needs_review": "Review candidates were found. They may be intentional.",
        "incomplete": "One or more required analyses failed. This report cannot establish a clean result.",
    }
    sources = "".join(
        f"<li><span class=label>{escape(name.title())}:</span> {escape(source.basename)}"
        f"<br>SHA-256: <code>{escape(source.sha256 or 'unavailable')}</code></li>"
        for name, source in report.inputs.items()
    )
    coverage = "".join(
        f"<tr><th scope=row>{escape(check.name)}</th><td>{escape(check.status)}</td><td>{escape(check.detail)}</td></tr>"
        for check in report.checks
    )
    counts = {status: sum(check.status == status for check in report.checks)
              for status in ("executed", "skipped", "error")}
    rows = []
    for finding in report.findings:
        jump = (f'<button type="button" aria-label="Seek {escape(finding.id, quote=True)} at {_timestamp(finding.start_seconds)}" data-seek="{escape(str(finding.start_seconds), quote=True)}">Seek</button>'
                if finding.start_seconds is not None else "—")
        rows.append(
            f"<tr><th scope=row>{escape(finding.id)}</th><td>{escape(finding.severity)}</td>"
            f"<td>{_timestamp(finding.start_seconds)}<br>{_timestamp(finding.end_seconds)}</td>"
            f"<td><code>{escape(finding.rule)}</code><br>Cue: {escape(str(finding.cue)) if finding.cue is not None else '—'}</td>"
            f"<td>{escape(finding.message)}</td><td>{jump}</td></tr>"
        )
    findings = ("<div class=scroll><table><thead><tr><th>ID</th><th>Severity</th><th>Start / end</th><th>Rule / cue</th><th>Review note</th><th>Player</th></tr></thead><tbody>"
                + "".join(rows) + "</tbody></table></div>" if rows else
                "<p>No findings were recorded. Skipped or failed checks are not passes.</p>")
    unsupported = "".join(f"<li>{escape(item)}</li>" for item in data["scope"]["unsupported"])
    options = escape(json.dumps(data["options"], ensure_ascii=False, sort_keys=True))
    versions = escape(", ".join(f"{name}: {version}" for name, version in sorted(report.tool_versions.items())))
    csp = ("default-src 'none'; base-uri 'none'; form-action 'none'; "
           f"style-src 'sha256-{_csp_hash(_CSS)}'; script-src 'sha256-{_csp_hash(_SCRIPT)}'; media-src blob:")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="{escape(csp, quote=True)}">
<title>Shortform review — {escape(state)}</title><style>{_CSS}</style></head><body>
<header><h1>Shortform review</h1><p><span class="state {state}">{escape(state.upper().replace('_', ' '))}</span></p>
<p>{counts['executed']} executed · {counts['skipped']} skipped · {counts['error']} failed checks</p>
<p>{captions[state]}</p><p class=muted>Technical candidates for human review. This is not editorial approval.</p>
<p class=muted>Created {escape(report.created_at)} · report schema {escape(data['schema_version'])}</p></header>
<section aria-labelledby="coverage-title"><h2 id="coverage-title">Coverage and limits</h2>
<p>Only the first video stream and first audio stream are considered. Metadata checks do not decode media.</p>
<div class=scroll><table><thead><tr><th>Check</th><th>Status</th><th>Evidence / limitation</th></tr></thead><tbody>{coverage}</tbody></table></div>
<p class=label>Unavailable in this tool</p><ul>{unsupported}</ul>
<p>External UTF-8 or UTF-8-BOM SRT only (up to 10 MiB). Subtitle text is omitted from reports.</p></section>
<section><h2>Sources</h2><ul>{sources}</ul><p class=muted>Reports include basenames and content hashes, not original paths or embedded media.</p></section>
<section><h2>Timeline</h2><p>Findings are ordered by start time. Findings without a known time appear last.</p>{findings}</section>
<section><h2>Optional local playback</h2><p>Choose the same video to inspect the timeline. This selection stays in your browser and is not identity verification. Nothing is uploaded.</p>
<label for="local-file">Local video: </label><input id="local-file" type="file" accept="video/*">
<p id="player-status" role="status" class=muted>No video selected. Browser codec support may differ from FFmpeg.</p>
<video id="player" controls preload="metadata"></video></section>
<section><h2>Effective settings</h2><p><code>{options}</code></p><p class=muted>{versions}</p></section>
<script>{_SCRIPT}</script></body></html>
"""


def write_reports(report: ReviewReport, directory: str | Path) -> tuple[Path, Path]:
    """Exclusively create a new output directory and report.json / report.html.

    The parent must exist. Existing output paths are never overwritten. An I/O
    failure can leave a partial new directory and is raised to the caller.
    """
    json_text = json.dumps(report.to_dict(), ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    html_text = render_html(report)
    destination = Path(directory).expanduser().absolute()
    destination.mkdir(mode=0o700, parents=False, exist_ok=False)
    json_path, html_path = destination / "report.json", destination / "report.html"
    for path, content in ((json_path, json_text), (html_path, html_text)):
        with path.open("x", encoding="utf-8", newline="\n") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
    return json_path, html_path
