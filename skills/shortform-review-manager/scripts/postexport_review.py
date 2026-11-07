#!/usr/bin/env python3
"""Run shortform-review on a freshly exported short and summarize the result.

Production pipelines call this right after the final export. It never edits
the video and never decides whether the video ships: review candidates are a
list for a human to check, so they do not make this script fail.

Exit codes (these differ from the shortform-review CLI's own 0/1/2):
  0  the review ran completely, with or without candidates; read "state"
  2  the review was incomplete or could not run; read "reason"/"errors"

The summary is <runs-root>/<label>-<UTC time>.summary.json beside the report
folder of the same name. Neither is ever overwritten.
"""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

COMPLETE = "PASS_REVIEW_COMPLETE"
INCOMPLETE = "HOLD_REVIEW_INCOMPLETE"
NOT_RUN = "HOLD_REVIEW_NOT_RUN"


def find_cli(explicit: str | None) -> str | None:
    """--bin, then $SHORTFORM_REVIEW_BIN, then this checkout's .venv, then PATH.

    An explicitly named executable that cannot be found is an error, not a
    reason to fall back silently to a different installation.
    """
    for named in (explicit, os.environ.get("SHORTFORM_REVIEW_BIN")):
        if named:
            found = named if Path(named).is_file() and os.access(named, os.X_OK) else shutil.which(named)
            if found is None:
                raise FileNotFoundError(f"shortform-review executable not found: {named}")
            return found
    local = Path(__file__).resolve().parents[3] / ".venv" / "bin" / "shortform-review"
    if local.is_file() and os.access(local, os.X_OK):
        return str(local)
    return shutil.which("shortform-review")


def reserve_run(root: Path, label: str) -> tuple[Path, Path]:
    """Claim a run name by creating its summary file exclusively; the report folder stays unused."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base, number = f"{label}-{stamp}", 1
    while True:
        name = base if number == 1 else f"{base}-{number}"
        out, summary = root / name, root / f"{name}.summary.json"
        if not (out.exists() or out.is_symlink()):
            try:
                summary.open("x", encoding="utf-8").close()
                return out, summary
            except FileExistsError:
                pass
        number += 1


def summarize(report: dict, exit_code: int, out: Path) -> dict:
    findings = report.get("findings", [])
    by_rule: dict[str, int] = {}
    contexts: dict[str, int] = {}
    for finding in findings:
        by_rule[finding["rule"]] = by_rule.get(finding["rule"], 0) + 1
        if finding.get("subtitle_context"):
            contexts[finding["subtitle_context"]] = contexts.get(finding["subtitle_context"], 0) + 1
    state = report.get("state")
    # Trust the run only when the CLI's exit agrees with the report and both files exist.
    expected_exit = {"clean": 0, "needs_review": 1}.get(state)
    complete = expected_exit == exit_code and (out / "report.html").is_file()
    errors = [f"{c['name']}: {c['detail']}" for c in report.get("checks", []) if c["status"] == "error"]
    if expected_exit is not None and not complete:
        errors.append(f"shortform-review exited {exit_code} for state {state}, or report.html is missing")
    return {
        "status": COMPLETE if complete else INCOMPLETE,
        "state": state,
        "exit_code": exit_code,
        "tool_version": report.get("tool", {}).get("version"),
        "report_json": str(out / "report.json"),
        "report_html": str(out / "report.html"),
        "inputs": report.get("inputs", {}),
        "checks": {check["name"]: check["status"] for check in report.get("checks", [])},
        "errors": errors,
        "candidates_by_rule": by_rule,
        "silence_subtitle_context": contexts,
        "candidates": [
            {key: finding.get(key) for key in ("id", "rule", "start_seconds", "end_seconds", "subtitle_context")}
            for finding in findings
        ],
        "note": "Candidates need human review (use Play ±1 s in report.html). "
                "They are not export failures, and clean is not publishing approval.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("video", type=Path, help="final exported video")
    parser.add_argument("--srt", type=Path, help="matching final SRT, if the pipeline has one")
    parser.add_argument("--runs-root", required=True, type=Path,
                        help="private folder for review runs; created if missing, never inside a public repo")
    parser.add_argument("--label", help="run folder prefix (default: video file stem)")
    parser.add_argument("--detect", default="black,silence", help="detectors to run (default: black,silence)")
    parser.add_argument("--timeout", default="120", help="per-command timeout passed to shortform-review")
    parser.add_argument("--bin", help="shortform-review executable")
    args = parser.parse_args(argv)

    try:
        args.runs_root.mkdir(parents=True, exist_ok=True)
        out, summary_path = reserve_run(args.runs_root, args.label or args.video.stem)
    except OSError as error:
        print(json.dumps({"status": NOT_RUN, "reason": f"cannot prepare runs root: {type(error).__name__}: {error}"}))
        return 2
    try:
        cli = find_cli(args.bin)
    except FileNotFoundError as error:
        cli, summary = None, {"status": NOT_RUN, "reason": str(error)}
    else:
        summary = {"status": NOT_RUN, "reason": "shortform-review executable not found",
                   "hint": "install the repository .venv or set SHORTFORM_REVIEW_BIN"}
    if cli is not None:
        command = [cli, str(args.video), "--out", str(out), "--detect", args.detect, "--timeout", args.timeout]
        if args.srt is not None:
            command[2:2] = ["--srt", str(args.srt)]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=3600)
            report_path = out / "report.json"
            if report_path.is_file():
                summary = summarize(json.loads(report_path.read_text(encoding="utf-8")), result.returncode, out)
            else:
                summary = {"status": NOT_RUN, "exit_code": result.returncode,
                           "reason": "no report was written", "stderr": result.stderr.strip()[-2000:]}
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
            summary = {"status": NOT_RUN, "reason": f"{type(error).__name__}: {error}"}
    summary["srt_supplied"] = args.srt is not None
    summary["summary_path"] = str(summary_path)
    line = {key: summary.get(key) for key in
            ("status", "state", "candidates_by_rule", "errors", "report_html", "summary_path", "reason")
            if key in summary}
    try:
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as error:
        summary["status"] = line["status"] = NOT_RUN
        line["reason"] = f"cannot write summary: {type(error).__name__}: {error}"
    print(json.dumps(line, ensure_ascii=False))
    return 0 if summary["status"] == COMPLETE else 2


if __name__ == "__main__":
    sys.exit(main())
