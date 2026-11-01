"""Real FFmpeg fixtures are synthesized in temporary directories, never committed."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from shortform_review import ReviewOptions, review


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg and ffprobe are required")
class FFmpegIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.clip = cls.root / "-clip ; $(touch SHELL_MARKER).mp4"
        cls.video_only = cls.root / "video-only.mp4"
        cls.ending_black = cls.root / "ending-black.mp4"
        cls.multiple_intervals = cls.root / "multiple-intervals.mp4"
        cls.make(["-f", "lavfi", "-i", "color=c=black:s=96x96:r=10:d=2", "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-t", "2", "-c:v", "mpeg4", "-q:v", "5", "-c:a", "aac", str(cls.clip)])
        cls.make(["-f", "lavfi", "-i", "color=c=red:s=96x96:r=10:d=1", "-c:v", "mpeg4", "-q:v", "5", str(cls.video_only)])
        cls.make(["-f", "lavfi", "-i", "color=c=blue:s=64x64:r=30:d=1[blue];color=c=black:s=64x64:r=30:d=0.5[black];[blue][black]concat=n=2:v=1:a=0", "-c:v", "mpeg4", str(cls.ending_black)])
        cls.make(["-f", "lavfi", "-i", "color=c=blue:s=64x64:r=10:d=5", "-f", "lavfi", "-i", "sine=frequency=1000:duration=5", "-vf", "drawbox=c=black:t=fill:enable='between(t,1,1.9)+between(t,3,3.9)'", "-af", "volume=volume=0:enable='between(t,1,1.9)+between(t,3,3.9)'", "-c:v", "mpeg4", "-c:a", "aac", str(cls.multiple_intervals)])

    @classmethod
    def make(cls, args):
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", *args], check=True, capture_output=True, timeout=20)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_real_black_and_eof_silence(self):
        report = review(self.clip, options=ReviewOptions(detect=("black", "silence")))
        self.assertEqual(report.state, "needs_review", report.to_dict())
        black = [f for f in report.findings if f.rule == "media.black"]
        silence = [f for f in report.findings if f.rule == "media.silence"]
        self.assertEqual(len(black), 1)
        self.assertEqual(len(silence), 1)
        self.assertAlmostEqual(black[0].start_seconds, 0, places=2)
        self.assertGreater(black[0].end_seconds, 1.8)
        self.assertGreaterEqual(silence[0].end_seconds, 1.99)
        self.assertIn("ffmpeg", report.tool_versions)
        self.assertFalse((self.root / "SHELL_MARKER").exists())

    def test_no_audio_and_red_video_have_no_candidates(self):
        report = review(self.video_only, options=ReviewOptions(detect=("black", "silence")))
        self.assertEqual(report.state, "clean", report.to_dict())
        self.assertEqual(next(c.status for c in report.checks if c.name == "silence"), "skipped")

    def test_eof_black_includes_last_frame_duration(self):
        report = review(self.ending_black, options=ReviewOptions(detect=("black",), min_black_duration=.5))
        self.assertEqual(report.state, "needs_review", report.to_dict())
        self.assertEqual(len(report.findings), 1)
        self.assertAlmostEqual(report.findings[0].start_seconds, 1, places=3)
        self.assertAlmostEqual(report.findings[0].end_seconds, 1.5, places=3)

    def test_multiple_closed_black_and_silence_intervals_remain_ordered(self):
        report = review(self.multiple_intervals, options=ReviewOptions(detect=("black", "silence")))
        self.assertEqual(report.state, "needs_review", report.to_dict())
        black = [f for f in report.findings if f.rule == "media.black"]
        silence = [f for f in report.findings if f.rule == "media.silence"]
        self.assertEqual(len(black), 2)
        self.assertEqual(len(silence), 2)
        for span, start in zip(black, (1, 3)):
            self.assertAlmostEqual(span.start_seconds, start, delta=.11)
            self.assertAlmostEqual(span.end_seconds, start + 1, delta=.11)
        for span, start in zip(silence, (1, 3)):
            self.assertAlmostEqual(span.start_seconds, start, delta=.1)
            self.assertAlmostEqual(span.end_seconds, start + .9, delta=.1)

    def test_valid_metadata_with_zero_decoded_frames_is_incomplete(self):
        encoders = subprocess.run(["ffmpeg", "-v", "error", "-encoders"], capture_output=True, text=True, check=True, timeout=10)
        if "libx265" not in encoders.stdout:
            self.skipTest("libx265 is required for the metadata-valid empty-stream fixture")
        path = self.root / "empty-hevc.mp4"
        self.make(["-f", "lavfi", "-i", "color=c=blue:s=64x64:r=10:d=1", "-c:v", "libx265", "-x265-params", "pools=1:frame-threads=1:log-level=error", str(path)])
        contents = bytearray(path.read_bytes())
        # Zero sample counts in this synthetic MP4 while preserving its HEVC
        # codec header and declared duration, reproducing an empty decode.
        for atom in (b"stsz", b"stco", b"stsc", b"stts", b"stss", b"ctts"):
            offset = contents.find(atom)
            if offset >= 0:
                count = offset + (12 if atom == b"stsz" else 8)
                contents[count:count + 4] = b"\x00" * 4
        path.write_bytes(contents)
        report = review(path, options=ReviewOptions(detect=("black",)))
        self.assertEqual(next(c.status for c in report.checks if c.name == "metadata"), "executed", report.to_dict())
        self.assertEqual(next(c.status for c in report.checks if c.name == "black"), "error")
        self.assertEqual(report.state, "incomplete")

    def test_cli_module_writes_findings_and_returns_one(self):
        output = self.root / "cli-output"
        result = subprocess.run([sys.executable, "-m", "shortform_review", str(self.clip), "--detect", "black,silence", "--out", str(output)], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 1, result.stderr)
        report = json.loads((output / "report.json").read_text())
        self.assertEqual(report["state"], "needs_review")
        self.assertTrue((output / "report.html").is_file())
        self.assertNotIn(str(self.root), (output / "report.json").read_text())

    def test_garbage_and_playlist_inputs_are_incomplete(self):
        for name, contents in (("broken.mp4", b"not a media file"), ("playlist.m3u8", b"#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXTINF:1,\nhttps://127.0.0.1:9/media.ts\n#EXT-X-ENDLIST\n")):
            path = self.root / name
            path.write_bytes(contents)
            with self.subTest(name=name):
                report = review(path, options=ReviewOptions(detect=("black", "silence")))
                self.assertEqual(report.state, "incomplete")
                self.assertEqual(next(c.status for c in report.checks if c.name == "metadata"), "error")


if __name__ == "__main__":
    unittest.main()
