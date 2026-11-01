import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from shortform_review import ReviewOptions, review
from shortform_review import media
from shortform_review.models import MediaInfo

INFO = MediaInfo("avi", 10, 10, 10, 0, 1, "stream", "stream")


class OptionsTests(unittest.TestCase):
    def test_nonfinite_and_out_of_range_rejected(self):
        for kwargs in ({"timeout": 0}, {"timeout": 3601}, {"timeout": float("nan")}, {"timeout": True}, {"min_black_duration": -1}, {"min_silence_duration": float("inf")}, {"silence_db": 1}, {"silence_db": -101}, {"detect": ("unknown",)}, {"detect": "black"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                ReviewOptions(**kwargs)

    def test_detector_order_is_canonical_and_duplicate_free(self):
        self.assertEqual(ReviewOptions(detect=("silence", "black", "black")).detect, ("black", "silence"))


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.video = self.root / "source.mp4"
        self.video.write_bytes(b"synthetic input")
        self.probe = patch.object(media, "probe", return_value=INFO).start()
        self.addCleanup(patch.stopall)

    def srt(self, content):
        path = self.root / "captions.srt"
        path.write_bytes(content if isinstance(content, bytes) else content.encode())
        return path

    def test_defaults_explicitly_skip_subtitles_and_detectors(self):
        report = review(self.video)
        self.assertEqual(report.state, "clean")
        checks = {check.name: check.status for check in report.checks}
        self.assertEqual(checks["subtitles"], "skipped")
        self.assertEqual(checks["black"], "skipped")
        self.assertEqual(checks["source_integrity"], "executed")
        self.assertEqual(report.inputs["video"].sha256, hashlib.sha256(b"synthetic input").hexdigest())

    def test_findings_are_chronological_and_have_local_ids(self):
        srt = self.srt("1\n00:00:08,000 --> 00:00:12,000\nx\n\n2\n00:00:01,000 --> 00:00:02,000\ny")
        with patch.object(media, "detect", return_value=[(4.0, 5.0)]):
            report = review(self.video, srt, ReviewOptions(detect=("black",)))
        self.assertEqual(report.state, "needs_review")
        self.assertEqual(report.exit_code, 1)
        self.assertEqual([f.start_seconds for f in report.findings], [1, 4, 8])
        self.assertEqual([f.id for f in report.findings], ["F0001", "F0002", "F0003"])

    def test_silence_gets_subtitle_context_only_after_complete_srt_check(self):
        good = self.srt("1\n00:00:01,000 --> 00:00:03,000\na\n\n2\n00:00:03,500 --> 00:00:05,000\nb")
        with patch.object(media, "detect", return_value=[(2.8, 3.6), (1.2, 2.0)]):
            report = review(self.video, good, ReviewOptions(detect=("black", "silence")))
        by_rule = {(f.rule, f.start_seconds): f.subtitle_context for f in report.findings}
        self.assertEqual(by_rule[("media.silence", 2.8)], "spans_cue_boundary")
        self.assertEqual(by_rule[("media.silence", 1.2)], "within_cue")
        self.assertIsNone(by_rule[("media.black", 2.8)])
        self.assertEqual(len(report.findings), 4)  # context never removes a finding

        with patch.object(media, "detect", return_value=[(2.8, 3.6)]):
            for srt in (None, self.srt("1\n00:00:01,000 --> 00:00:03,000\na\n\nbroken")):
                with self.subTest(srt=srt):
                    report = review(self.video, srt, ReviewOptions(detect=("silence",)))
                    self.assertTrue(all(f.subtitle_context is None for f in report.findings))

    def test_invalid_encoding_and_empty_srt_are_incomplete(self):
        for content in (b"\xff\xfe\x00", b"", b"malformed"):
            with self.subTest(content=content):
                report = review(self.video, self.srt(content))
                self.assertEqual(report.state, "incomplete")
                self.assertEqual(report.exit_code, 2)

    def test_input_directory_and_missing_file_are_incomplete(self):
        for path in (self.root, self.root / "missing.mp4"):
            with self.subTest(path=path):
                self.assertEqual(review(path).state, "incomplete")
        self.probe.assert_not_called()

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires FIFO support")
    def test_special_file_is_rejected_without_reading(self):
        fifo = self.root / "input.pipe"
        os.mkfifo(fifo)
        self.assertEqual(review(fifo).state, "incomplete")

    def test_missing_tool_and_detector_failure_are_not_clean(self):
        self.probe.side_effect = media.MediaError("ffprobe is not available on PATH.")
        self.assertEqual(review(self.video).state, "incomplete")
        self.probe.side_effect = None
        with patch.object(media, "detect", side_effect=[[(1, 2)], media.MediaError("decode failed")]):
            report = review(self.video, options=ReviewOptions(detect=("black", "silence")))
        self.assertEqual(report.state, "incomplete")
        self.assertEqual(len(report.findings), 1)
        self.assertEqual(next(c.status for c in report.checks if c.name == "silence"), "error")

    def test_source_change_invalidates_review(self):
        def change_source(*args):
            self.video.write_bytes(b"changed during analysis")
            return []
        with patch.object(media, "detect", side_effect=change_source):
            report = review(self.video, options=ReviewOptions(detect=("black",)))
        self.assertEqual(report.state, "incomplete")
        self.assertEqual(next(c.status for c in report.checks if c.name == "source_integrity"), "error")

    def test_no_audio_is_explicit_skip(self):
        self.probe.return_value = MediaInfo("avi", 10, 10, None, 0, None, "stream", None)
        with patch.object(media, "detect") as detect:
            report = review(self.video, options=ReviewOptions(detect=("silence",)))
        detect.assert_not_called()
        self.assertEqual(next(c.status for c in report.checks if c.name == "silence"), "skipped")

    def test_report_excludes_absolute_paths_and_subtitle_text(self):
        srt = self.srt("1\n00:00:00,000 --> 00:00:01,000\nPRIVATE CAPTION")
        report = review(self.video, srt)
        text = json.dumps(report.to_dict())
        self.assertNotIn(str(self.root), text)
        self.assertNotIn("PRIVATE CAPTION", text)
        self.assertIn("source.mp4", text)


if __name__ == "__main__":
    unittest.main()
