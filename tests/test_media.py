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

    def test_decoded_video_end_is_independent_of_ffmpeg_progress_version(self):
        packet = "#tb 0: 1/15360\n#media_type 0: video\n0, 22528, 22528, 512, 416, 0x00000000\n"
        for progress_end in ("1466667", "1500000"):
            with self.subTest(progress_end=progress_end):
                output = packet + f"frame=45\nout_time_us={progress_end}\nprogress=end\n"
                self.assertEqual(media._decoded_duration(output, "black"), 1.5)

    def test_decoded_audio_end_includes_final_samples(self):
        output = "#tb 0: 1/8000\n#media_type 0: audio\n0, 15360, 15360, 640, 1280, 0x00000000\nout_time_us=1920000\nprogress=end\n"
        self.assertEqual(media._decoded_duration(output, "silence"), 2)

    def test_variable_frame_durations_use_packet_ends(self):
        output = "#tb 0: 1/1000\n#media_type 0: video\n0, 0, 0, 40, 416, 0x00000000\n0, 40, 40, 100, 416, 0x00000000\n0, 140, 140, 30, 416, 0x00000000\nframe=3\nprogress=end\n"
        self.assertEqual(media._decoded_duration(output, "black"), .17)

    def test_missing_or_invalid_decoded_packet_evidence_fails_closed(self):
        valid = "#tb 0: 1/1000\n#media_type 0: video\n0, 0, 0, 40, 416, 0x00000000\nframe=1\nprogress=end\n"
        cases = [
            valid.replace("#tb 0: 1/1000\n", ""),
            valid.replace("1/1000", "1/0"),
            valid.replace("1/1000", "0/1000"),
            valid.replace("0, 0, 0, 40, 416", "1, 0, 0, 40, 416"),
            valid.replace("0, 0, 0, 40, 416", "0, 0, 0, 0, 416"),
            valid.replace("0, 0, 0, 40, 416", "0, 0, 0, 40, 0"),
            valid.replace("video", "audio"),
            valid.replace("progress=end", "progress=continue"),
            valid.replace("frame=1", "frame=0"),
        ]
        for output in cases:
            with self.subTest(output=output), self.assertRaises(media.MediaError):
                media._decoded_duration(output, "black")

    def test_inflated_metadata_does_not_extend_decoded_eof(self):
        info = media.MediaInfo("avi", 100, 100, None, 0, None, "stream", None)
        output = "#tb 0: 1/1000\n#media_type 0: video\n0, 0, 0, 1000, 416, 0x00000000\nlavfi.black_start=0\nframe=1\nout_time_us=100000000\nprogress=end\n"
        result = subprocess.CompletedProcess([], 0, output, "")
        with patch.object(media, "_tool", return_value="ffmpeg"), patch.object(media, "_run", return_value=result) as run:
            spans = media.detect(Path("/synthetic/short.mp4"), "black", info, ReviewOptions(), {"ffmpeg": "test"})
        self.assertEqual(spans, [(0.0, 1.0)])
        argv = run.call_args.args[0]
        self.assertEqual(argv[argv.index("-fps_mode") + 1], "passthrough")
        self.assertEqual(argv[argv.index("-enc_time_base:v") + 1], "-1")
        self.assertEqual(argv[-3:], ["-f", "framecrc", "-"])


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
