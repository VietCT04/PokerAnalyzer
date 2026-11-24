import unittest

try:
    from video_preflop import select_nickname
except ModuleNotFoundError as exc:
    if exc.name not in {"numpy", "PIL", "imageio_ffmpeg"}:
        raise
    select_nickname = None


@unittest.skipIf(select_nickname is None, "optional video dependencies are missing")
class NicknameOcrTest(unittest.TestCase):
    def test_repeated_reading_keeps_spelling_and_evidence_time(self):
        result = select_nickname([(12, "ChipRunner", 76, None), (12.5, "chiprunner", 81, None)])
        self.assertEqual(result[:5], ("chiprunner", 81, 12.5, 2, "repeated_ocr"))

    def test_repeat_at_same_timestamp_does_not_count_twice(self):
        result = select_nickname([(12, "ChipRunner", 76, None), (12, "ChipRunner", 81, None)])
        self.assertEqual(result[0], "")
        self.assertEqual(result[3], 0)

    def test_conflicting_reads_stay_blank(self):
        result = select_nickname([(12, "ChipRunner", 94, None), (12.5, "ChipRunn3r", 92, None)])
        self.assertEqual(result[0], "")
        self.assertEqual(result[4], "disagrees")

    def test_posting_label_is_excluded(self):
        result = select_nickname([(12, "Post Blinds", 99, None), (12.5, "ChipRunner", 91, None)])
        self.assertEqual(result[:5], ("ChipRunner", 91, 12.5, 1, "single_frame_ocr"))
