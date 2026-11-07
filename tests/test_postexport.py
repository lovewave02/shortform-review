"""The post-export helper used by production pipelines, run against synthetic media."""

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "shortform-review-manager" / "scripts" / "postexport_review.py"
spec = importlib.util.spec_from_file_location("postexport_review", SCRIPT)
postexport = importlib.util.module_from_spec(spec)
spec.loader.exec_module(postexport)


class PostExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # Run the CLI from this interpreter so the test does not depend on PATH.
        self.cli = self.root / "shortform-review"
        self.cli.write_text(f"#!{sys.executable}\nimport sys\nfrom shortform_review.cli import main\nsys.exit(main())\n")
        self.cli.chmod(0o755)

    def run_helper(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = postexport.main([str(arg) for arg in args])
        line = json.loads(output.getvalue())
        return code, line, json.loads(Path(line["summary_path"]).read_text())

    def test_missing_cli_is_reported_not_run(self):
        video = self.root / "final.mp4"
        video.write_bytes(b"not used")
        with unittest.mock.patch.object(postexport, "find_cli", return_value=None):
            code, line, summary = self.run_helper(video, "--runs-root", self.root / "runs")
        self.assertEqual(code, 2)
        self.assertEqual(summary["status"], "HOLD_REVIEW_NOT_RUN")
        self.assertIn("not found", summary["reason"])

    def test_unreadable_media_is_incomplete_and_keeps_reports(self):
        video = self.root / "broken.mp4"
        video.write_bytes(b"not a video")
        code, line, summary = self.run_helper(video, "--runs-root", self.root / "runs", "--bin", self.cli)
        self.assertEqual(code, 2)
        self.assertEqual(summary["status"], "HOLD_REVIEW_INCOMPLETE")
        self.assertEqual(summary["state"], "incomplete")
        self.assertTrue(summary["errors"])
        self.assertTrue(Path(summary["report_html"]).is_file())

    def test_same_second_runs_reserve_distinct_names(self):
        root = self.root / "runs"
        root.mkdir()
        fixed = postexport.datetime(2026, 11, 7, 9, 0, 0, tzinfo=postexport.timezone.utc)
        with unittest.mock.patch.object(postexport, "datetime") as clock:
            clock.now.return_value = fixed
            first, second = postexport.reserve_run(root, "v"), postexport.reserve_run(root, "v")
        self.assertNotEqual(first, second)
        self.assertTrue(first[1].is_file() and second[1].is_file())
        self.assertFalse(first[0].exists())  # the report folder is left for the CLI to create

    def test_repeated_failures_keep_every_summary(self):
        video = self.root / "final.mp4"
        video.write_bytes(b"x")
        with unittest.mock.patch.object(postexport, "find_cli", return_value=None):
            paths = {self.run_helper(video, "--runs-root", self.root / "runs")[1]["summary_path"] for _ in range(3)}
        self.assertEqual(len(paths), 3)

    def test_named_executable_that_is_missing_does_not_fall_back(self):
        video = self.root / "final.mp4"
        video.write_bytes(b"x")
        code, line, summary = self.run_helper(video, "--runs-root", self.root / "runs", "--bin", self.root / "nope")
        self.assertEqual(code, 2)
        self.assertIn("nope", summary["reason"])

    def test_unusable_runs_root_exits_two_with_json(self):
        blocker = self.root / "file"
        blocker.write_text("x")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = postexport.main([str(self.root / "v.mp4"), "--runs-root", str(blocker / "runs")])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue())["status"], "HOLD_REVIEW_NOT_RUN")

    def test_report_write_failure_is_not_complete(self):
        out = self.root / "run"
        out.mkdir()
        (out / "report.json").write_text(json.dumps({"state": "needs_review", "checks": [], "findings": []}))
        # The CLI exited 2 after writing only report.json (HTML write failed).
        summary = postexport.summarize(json.loads((out / "report.json").read_text()), 2, out)
        self.assertEqual(summary["status"], "HOLD_REVIEW_INCOMPLETE")
        self.assertTrue(summary["errors"])
        (out / "report.html").write_text("ok")
        self.assertEqual(postexport.summarize({"state": "needs_review", "checks": [], "findings": []}, 1, out)["status"],
                         "PASS_REVIEW_COMPLETE")

    def test_repeated_runs_never_overwrite(self):
        video = self.root / "broken.mp4"
        video.write_bytes(b"x")
        first = self.run_helper(video, "--runs-root", self.root / "runs", "--bin", self.cli)[2]
        second = self.run_helper(video, "--runs-root", self.root / "runs", "--bin", self.cli)[2]
        self.assertNotEqual(first["report_json"], second["report_json"])
        self.assertTrue(Path(first["report_json"]).is_file() and Path(second["report_json"]).is_file())

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg and ffprobe are required")
    def test_candidates_are_complete_review_not_failure(self):
        video = self.root / "final.mp4"
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=64x64:r=10:d=4",
                        "-f", "lavfi", "-i", "sine=frequency=1000:duration=4",
                        "-af", "volume=volume=0:enable='between(t,1.5,2.5)'", "-c:v", "mpeg4", "-c:a", "aac",
                        str(video)], check=True, capture_output=True, timeout=20)
        srt = self.root / "final.srt"
        srt.write_text("1\n00:00:00,200 --> 00:00:01,700\na\n\n2\n00:00:02,300 --> 00:00:03,800\nb\n", encoding="utf-8")
        code, line, summary = self.run_helper(video, "--srt", srt, "--runs-root", self.root / "runs",
                                              "--label", "run-x", "--bin", self.cli)
        self.assertEqual(code, 0, summary)
        self.assertEqual(summary["status"], "PASS_REVIEW_COMPLETE")
        self.assertEqual(summary["state"], "needs_review")
        self.assertEqual(summary["candidates_by_rule"], {"media.silence": 1})
        self.assertEqual(summary["silence_subtitle_context"], {"spans_cue_boundary": 1})
        self.assertEqual(summary["checks"]["subtitles"], "executed")
        self.assertTrue(Path(summary["report_html"]).parent.name.startswith("run-x-"))
        self.assertEqual(line["status"], "PASS_REVIEW_COMPLETE")


if __name__ == "__main__":
    unittest.main()
