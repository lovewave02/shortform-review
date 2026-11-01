import unittest

from shortform_review.rules import subtitle_findings
from shortform_review.srt import parse_srt


class SRTTests(unittest.TestCase):
    def test_bom_crlf_and_multiline(self):
        result = parse_srt("\ufeff1\r\n00:00:00,100 --> 00:00:01,200\r\n안녕\r\nsecond line\r\n")
        self.assertTrue(result.complete)
        self.assertEqual(result.cues[0].text, "안녕\nsecond line")
        self.assertEqual((result.cues[0].start, result.cues[0].end), (0.1, 1.2))

    def test_malformed_block_does_not_hide_later_valid_cue(self):
        result = parse_srt("bad\n00:00:00,000 --> 00:00:01,000\nx\n\n2\n00:00:01,000 --> 00:00:02,000\ny")
        self.assertFalse(result.complete)
        self.assertEqual([cue.index for cue in result.cues], [2])
        self.assertEqual(result.findings[0].rule, "srt.invalid_index")

    def test_empty_and_invalid_timestamps_are_incomplete(self):
        for text in ("", "\ufeff \r\n", "1", "1\n00:61:00,000 --> 00:62:00,000\nx", "1\nNaN --> 2\nx"):
            with self.subTest(text=text):
                self.assertFalse(parse_srt(text).complete)

    def test_reversed_zero_duration_and_bounds(self):
        result = parse_srt("1\n00:00:03,000 --> 00:00:02,000\na\n\n2\n00:00:04,000 --> 00:00:04,000\nb")
        rules = [finding.rule for finding in subtitle_findings(result.cues, 2.5)]
        self.assertIn("srt.reversed_time", rules)
        self.assertIn("srt.zero_duration", rules)
        self.assertEqual(rules.count("srt.out_of_bounds"), 2)

    def test_nested_and_out_of_order_overlaps(self):
        result = parse_srt("1\n00:00:00,000 --> 00:00:10,000\nlong\n\n2\n00:00:04,000 --> 00:00:05,000\nlater\n\n3\n00:00:01,000 --> 00:00:02,000\nearlier\n\n4\n00:00:06,000 --> 00:00:07,000\nnested")
        findings = subtitle_findings(result.cues, 10)
        self.assertEqual({f.cue for f in findings if f.rule == "srt.overlap"}, {2, 3, 4})
        self.assertEqual([f.cue for f in findings if f.rule == "srt.out_of_order"], [3])

    def test_touching_cues_are_not_overlaps(self):
        result = parse_srt("1\n00:00:00,000 --> 00:00:01,000\na\n\n2\n00:00:01,000 --> 00:00:02,000\nb")
        self.assertEqual(subtitle_findings(result.cues, 2), [])

    def test_duplicate_number_and_empty_text(self):
        result = parse_srt("1\n00:00:00,000 --> 00:00:01,000\na\n\n1\n00:00:02,000 --> 00:00:03,000")
        self.assertTrue(result.complete)
        self.assertEqual({f.rule for f in result.findings}, {"srt.duplicate_index", "srt.empty_text"})

    def test_missing_blank_separator_does_not_silently_consume_a_cue(self):
        result = parse_srt("1\n00:00:00,000 --> 00:00:00,100\nfirst\n2\n00:00:10,000 --> 00:00:20,000\nsecond")
        self.assertFalse(result.complete)
        self.assertIn("srt.missing_separator", [f.rule for f in result.findings])

    def test_multiple_blank_separators_are_accepted(self):
        for separator in ("\n\n\n", "\n\n\n\n", "\n \n\t\n"):
            result = parse_srt("1\n00:00:00,000 --> 00:00:00,100\nfirst" + separator + "2\n00:00:00,200 --> 00:00:00,300\nsecond")
            self.assertTrue(result.complete)
            self.assertEqual(len(result.cues), 2)


if __name__ == "__main__":
    unittest.main()
