import unittest
from decimal import Decimal

try:
    from video_preflop import agree_on_wider_label, check_four_digit_stack, select_nickname
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

    def test_wider_crop_requires_agreement_on_spelling(self):
        wider = (12, "ChipRunner", 93, None)
        agree = agree_on_wider_label([wider, (12, "ChipRunner", 81, None)])
        conflict = agree_on_wider_label([wider, (12, "ChipRunn3r", 91, None)])
        self.assertEqual(agree[:5], ("ChipRunner", 93, 12, 1, "wide_label_ocr"))
        self.assertEqual(conflict[0], "")

    def test_four_digit_stack_needs_two_matching_full_labels(self):
        label = ("104.6 BB", 90)
        self.assertEqual(check_four_digit_stack("1046", [label, label]), Decimal("104.6"))
        self.assertEqual(check_four_digit_stack("1046", [("1046 BB", 90)] * 2), Decimal(1046))
        self.assertIsNone(check_four_digit_stack("1046", [label, ("1046 BB", 91)]))
