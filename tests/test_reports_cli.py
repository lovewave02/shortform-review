import base64
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from shortform_review import ReviewOptions
from shortform_review.cli import main
from shortform_review.models import Check, Finding, ReviewReport, Source
from shortform_review.reports import render_html, write_reports


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def report(self):
        result = ReviewReport(ReviewOptions())
        result.checks.append(Check("metadata", "executed", "checked"))
        result.finalize()
        return result

    def test_html_escapes_untrusted_text_and_static_script_hash_matches(self):
        report = self.report()
        attack = "</script><img src=x onerror=alert(1)>\"&"
        report.inputs["video"] = Source(attack, None)
        report.findings.append(Finding("warning", "test", attack, 1, 2))
        report.finalize()
        html = render_html(report)
        self.assertNotIn(attack, html)
        self.assertIn("&lt;/script&gt;&lt;img", html)
        self.assertEqual(html.count("<script>"), 1)
        script = re.search(r"<script>(.*?)</script>", html, re.DOTALL)[1]
        digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
        self.assertIn("sha256-" + digest, html)
        self.assertIn("media-src blob:", html)
        self.assertNotIn("innerHTML", html)
        self.assertIn("Identity has not been verified", html)

    def test_json_and_html_written_to_new_directory(self):
        paths = write_reports(self.report(), self.root / "new")
        self.assertEqual([path.name for path in paths], ["report.json", "report.html"])
        self.assertEqual(json.loads(paths[0].read_text())["schema_version"], "1.0")
        self.assertIn("Coverage and limits", paths[1].read_text())

    def test_collision_preserves_existing_content(self):
        directory = self.root / "existing"
        directory.mkdir()
        original = directory / "report.json"
        original.write_text("original")
        with self.assertRaises(FileExistsError):
            write_reports(self.report(), directory)
        self.assertEqual(original.read_text(), "original")

    def test_invalid_report_fails_before_creating_directory(self):
        report = self.report()
        report.findings.append(Finding("warning", "test", "nonfinite", float("nan")))
        output = self.root / "invalid"
        with self.assertRaises(ValueError):
            write_reports(report, output)
        self.assertFalse(output.exists())

    def test_partial_io_failure_propagates(self):
        with patch("shortform_review.reports.os.fsync", side_effect=OSError("disk error")), self.assertRaises(OSError):
            write_reports(self.report(), self.root / "partial")

    def test_extreme_finite_timestamp_does_not_overflow_html(self):
        report = self.report()
        report.findings.append(Finding("warning", "srt.out_of_bounds", "extreme", 3.6e306, 3.6e306))
        report.finalize()
        json_path, html_path = write_reports(report, self.root / "extreme")
        self.assertEqual(json.loads(json_path.read_text())["state"], "needs_review")
        self.assertIn("srt.out_of_bounds", html_path.read_text())


class CLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def invoke(self, argv):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return main(argv)

    def test_output_collision_skips_review(self):
        with patch("shortform_review.cli.review") as review:
            code = self.invoke(["missing.mp4", "--out", str(self.root)])
        self.assertEqual(code, 2)
        review.assert_not_called()

    def test_missing_input_writes_incomplete_report(self):
        output = self.root / "result"
        self.assertEqual(self.invoke([str(self.root / "missing.mp4"), "--out", str(output)]), 2)
        self.assertEqual(json.loads((output / "report.json").read_text())["state"], "incomplete")

    def test_write_failure_is_nonzero(self):
        report = ReviewReport(ReviewOptions())
        with patch("shortform_review.cli.review", return_value=report), patch("shortform_review.cli.write_reports", side_effect=OSError("disk full")):
            self.assertEqual(self.invoke(["test.mp4", "--out", str(self.root / "new")]), 2)

    def test_invalid_finite_thresholds_and_detector_names_are_usage_errors(self):
        for args in (["--timeout", "nan"], ["--silence-db", "4"], ["--detect", "black,unknown"]):
            with self.subTest(args=args), self.assertRaises(SystemExit) as caught:
                self.invoke(["video.mp4", "--out", str(self.root / "new"), *args])
            self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
