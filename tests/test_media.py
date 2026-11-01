import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from shortform_review import ReviewOptions
from shortform_review import media


class DetectorParsingTests(unittest.TestCase):
    def test_black_interval(self):
        spans = media.parse_black("lavfi.black_start=1\nlavfi.black_end=2.5\n", 3, 0.5)
        self.assertEqual(spans, [(1.0, 2.5)])

    def test_eof_silence_closes_on_audio_duration(self):
        logs = "lavfi.silence_start=1\nlavfi.silence_end=2\nlavfi.silence_start=4\n"
        self.assertEqual(media.parse_silence(logs, 7.0, 0.5), [(1.0, 2.0), (4.0, 7.0)])

    def test_explicit_eof_end_is_not_duplicated(self):
        logs = "lavfi.silence_start=0\nlavfi.silence_end=3\n"
        self.assertEqual(media.parse_silence(logs, 3.0, 0.5), [(0.0, 3.0)])

    def test_malformed_detector_events_fail_closed(self):
        cases = [
            lambda: media.parse_black("lavfi.black_start=NaN\nlavfi.black_end=2", 3, .5),
            lambda: media.parse_silence("lavfi.silence_end=2", 3, .5),
            lambda: media.parse_silence("lavfi.silence_start=5", 3, .5),
        ]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(media.MediaError):
                case()

    def test_metadata_keys_required(self):
        self.assertEqual(media.parse_black("some metadata black_start:1 black_end:2 black_duration:1", 3, .5), [])

    def test_open_black_uses_decoded_eof_and_threshold(self):
        self.assertEqual(media.parse_black("lavfi.black_start=1\n", 1.5, .5), [(1.0, 1.5)])
        self.assertEqual(media.parse_black("lavfi.black_start=1\n", 1.4, .5), [])

    def test_empty_and_unfinished_decoded_progress_fail(self):
        for output in ("frame=0\nout_time_us=0\nprogress=end\n", "frame=1\nout_time_us=100000\nprogress=continue\n"):
            with self.subTest(output=output), self.assertRaises(media.MediaError):
                media._decoded_duration(output, "black")


class ProbeTests(unittest.TestCase):
    def result(self, data):
        return subprocess.CompletedProcess([], 0, json.dumps(data), "")

    def metadata(self):
        return {"format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "5"},
                "streams": [{"index": 0, "codec_type": "video", "duration": "3"},
                            {"index": 1, "codec_type": "audio", "duration": "7"}]}

    def call_probe(self, data):
        with patch.object(media, "_tool", return_value="ffprobe"), patch.object(media, "_version", return_value="test"), patch.object(media, "_run", return_value=self.result(data)):
            return media.probe(Path("/synthetic/clip.mp4"), ReviewOptions(), {})

    def test_separate_video_audio_and_container_durations(self):
        result = self.call_probe(self.metadata())
        self.assertEqual(result.duration_seconds, 7)
        self.assertEqual(result.video_duration_seconds, 3)
        self.assertEqual(result.audio_duration_seconds, 7)

    def test_duration_fallback_is_explicit(self):
        data = self.metadata()
        del data["streams"][0]["duration"]
        result = self.call_probe(data)
        self.assertEqual(result.video_duration_seconds, 5)
        self.assertEqual(result.video_duration_basis, "container_fallback")

    def test_no_video_or_positive_duration_is_error(self):
        for streams, duration in (([], "5"), ([{"index": 0, "codec_type": "audio"}], "5"), ([{"index": 0, "codec_type": "video"}], "0")):
            data = {"streams": streams, "format": {"format_name": "avi", "duration": duration}}
            with self.subTest(data=data), self.assertRaises(media.MediaError):
                self.call_probe(data)

    def test_nonfinite_duration_and_playlist_are_errors(self):
        for value in ("NaN", "inf", "-1", "nonsense"):
            data = self.metadata()
            data["streams"][0]["duration"] = value
            with self.subTest(value=value), self.assertRaises(media.MediaError):
                self.call_probe(data)
        data = self.metadata()
        data["format"]["format_name"] = "hls"
        with self.assertRaises(media.MediaError):
            self.call_probe(data)

    def test_probe_argv_limits_formats_and_protocols(self):
        path = Path("/synthetic/-clip;$(touch PWNED).mp4")
        with patch.object(media, "_tool", return_value="ffprobe"), patch.object(media, "_version", return_value="test"), patch.object(media, "_run", return_value=self.result(self.metadata())) as run:
            media.probe(path, ReviewOptions(), {})
        argv = run.call_args.args[0]
        self.assertEqual(argv[-2:], ["-i", str(path)])
        self.assertEqual(argv[argv.index("-protocol_whitelist") + 1], "file,pipe")
        self.assertNotIn("hls", argv[argv.index("-format_whitelist") + 1])

    def test_timeout_and_missing_executable(self):
        with patch.object(media.shutil, "which", return_value=None), self.assertRaises(media.MediaError):
            media._tool("ffprobe")
        with patch.object(media.subprocess, "run", side_effect=subprocess.TimeoutExpired(["ffprobe"], .1)), self.assertRaisesRegex(media.MediaError, "timeout"):
            media._run(["ffprobe", "-version"], .1)

    def test_subprocess_has_no_shell_and_no_stdin(self):
        with patch.object(media.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
            media._run(["ffprobe", "weird;$(x)"], 1)
        self.assertFalse(run.call_args.kwargs["shell"])
        self.assertEqual(run.call_args.kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(run.call_args.kwargs["timeout"], 1)


if __name__ == "__main__":
    unittest.main()
