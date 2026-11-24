import unittest
from decimal import Decimal
from unittest.mock import patch

try:
    from video_preflop import (
        FrameSample, HandSamples, agree_on_wider_label, check_four_digit_stack, read_delayed_names, select_nickname,
    )
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

    def test_unresolved_preview_uses_the_frame_with_the_attempted_name(self):
        banner, name_crop = object(), object()
        result = select_nickname([(12, "Post Blinds", 99, banner), (12.5, "ChipRunner", 80, name_crop)])
        self.assertEqual(result, ("", None, 12.5, 0, "unreadable", "ChipRunner", name_crop))

    def test_delayed_lookup_stops_on_a_dealer_or_card_change(self):
        cards = ("hd", [])
        anchor = FrameSample(12, "top-left", cards, "top", {}, b"")
        for changed_cards, changed_dealer in [(cards, "bottom"), (("ds", []), "top")]:
            with self.subTest(cards=changed_cards[0], dealer=changed_dealer):
                hand = HandSamples([anchor], [], [], 12)
                self.assertTrue(hand.followup_active(12.5))
                self.assertTrue(hand.confirm_followup(12.5, cards, "top"))
                self.assertFalse(hand.confirm_followup(12.75, changed_cards, changed_dealer))
                self.assertFalse(hand.followup_active(13))

    def test_delayed_lookup_needs_continuous_card_evidence(self):
        cards = ("hd", [])
        anchor = FrameSample(12, "top-left", cards, "top", {}, b"")
        hand = HandSamples([anchor], [], [], 12)
        self.assertTrue(hand.followup_active(12.75))
        self.assertFalse(hand.followup_active(13))
        self.assertFalse(hand.confirm_followup(12.5, cards, ""))

    def test_delayed_lookup_skips_action_badges(self):
        cards = ("hd", [])
        anchor = FrameSample(12, "top-left", cards, "top", {}, b"")
        frames = [(13, cards, "top", [(None, None)]), (14, cards, "top", [(None, None)])]
        hand = HandSamples([anchor], [], frames, 14)
        results = [("", None, None, 0, "unreadable", "", None)]
        reads = [[(13, "Check", 99, None), (14, "ChipRunner", 93, None)]]
        with patch("video_preflop.has_name_text", return_value=True):
            with patch("video_preflop.frame_nickname_readings", return_value=reads):
                read_delayed_names([(anchor, hand)], ("top",), results, None, None)
        self.assertEqual(results[0][:5], ("ChipRunner", 93, 14, 1, "delayed_name_ocr"))

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
