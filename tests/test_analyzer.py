import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from hand_history import parse_hand
from poker_analyzer import STATS_COLUMNS, connect, import_csv, import_zip, print_coverage, statistics


EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
SESSIONS = {"morning.txt": "morning", "evening.txt": "evening"}
MORNING = (EXAMPLES / "table-morning.txt").read_text(encoding="utf-8")
EVENING = (EXAMPLES / "table-evening.txt").read_text(encoding="utf-8")


class AnalyzerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.db = connect(self.folder / "poker.sqlite")
        self.addCleanup(self.db.close)

    def archive(self, name="hands.zip", **members):
        path = self.folder / name
        with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
            for member, text in (members or {"morning.txt": MORNING, "evening.txt": EVENING}).items():
                archive.writestr(member, text)
        return path

    def csv(self, rows):
        path = self.folder / "observations.csv"
        path.write_text("hand_id,winner_nickname\n" + rows, encoding="utf-8")
        return path

    def stats(self, name):
        return [dict(zip(STATS_COLUMNS, row)) for row in statistics(self.db, name)]

    def test_incremental_scope_money_stats_and_deduplication(self):
        archive = self.archive()
        self.assertEqual(import_zip(self.db, archive)["added"], 3)
        import_csv(self.db, EXAMPLES / "winners.csv")
        self.assertEqual(import_csv(self.db, EXAMPLES / "winners.csv"), 0)
        self.assertEqual(self.stats("Example Alice")[0]["total_observed_hands"], 1)
        self.assertEqual(import_zip(self.db, archive, member_sessions=SESSIONS)["scoped"], 3)
        self.assertEqual(import_zip(self.db, archive, member_sessions=SESSIONS)["duplicate"], 3)
        result, = self.stats("Example Alice")
        self.assertEqual(result["total_observed_hands"], 2)
        self.assertEqual(result["preflop_opportunities"], 2)
        self.assertEqual(result["vpip_pct"], "100.00")
        self.assertEqual(result["pfr_pct"], "100.00")
        self.assertEqual(result["three_bet_opportunities"], 1)
        self.assertEqual(result["three_bet_pct"], "100.00")
        self.assertEqual(result["net_winnings"], "7.00")
        self.assertEqual(result["bb_per_100"], "350.00")
        casey, = self.stats("Example Casey")
        self.assertEqual(casey["total_observed_hands"], 1)
        self.assertEqual(casey["three_bet_pct"], "")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM appearances").fetchone()[0], 8)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM actions WHERE kind = 'return'").fetchone()[0], 3)

    def test_conflicting_evidence_revokes_previously_propagated_identity(self):
        import_zip(self.db, self.archive(), member_sessions=SESSIONS)
        import_csv(self.db, EXAMPLES / "winners.csv")
        import_csv(self.db, self.csv("HD900000002,Example Other\n"))
        self.assertEqual(self.stats("Example Alice"), [])
        self.assertEqual(self.stats("Example Other"), [])
        self.assertEqual(self.stats("Example Casey")[0]["total_observed_hands"], 1)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            print_coverage(self.db)
        self.assertIn("Mapping conflict:", output.getvalue())
        self.assertIn("HD900000002", output.getvalue())

    def test_bare_id_ambiguity_revokes_numeric_evidence(self):
        import_csv(self.db, self.csv("Hand ID: #900000001,Example Alice\n#HD900000003,Example Casey\n"))
        import_zip(self.db, self.archive(), member_sessions=SESSIONS)
        other = MORNING.split("\n\n", 1)[0].replace("HD900000001", "RC900000001")
        import_zip(self.db, self.archive("other.zip", **{"other.txt": other}))
        self.assertEqual(self.stats("Example Alice"), [])
        with contextlib.redirect_stdout(io.StringIO()) as output:
            print_coverage(self.db)
        self.assertIn("Ambiguous numeric ID", output.getvalue())
        self.assertEqual(self.stats("Example Casey")[0]["total_observed_hands"], 1)
        import_csv(self.db, EXAMPLES / "winners.csv")
        self.assertEqual(self.stats("Example Alice")[0]["total_observed_hands"], 2)

    def test_same_nickname_on_two_tokens_is_a_conflict(self):
        swapped = EVENING.replace("a0a0a0a0", "b0b0b0b0")
        import_zip(self.db, self.archive(**{"morning.txt": MORNING, "evening.txt": swapped}), session="same")
        import_csv(self.db, self.csv("900000001,Example Alice\n900000003,Example Alice\n"))
        self.assertEqual(self.stats("Example Alice"), [])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM mapping_candidates").fetchone()[0], 2)

    def test_split_pot_and_unknown_action(self):
        source = MORNING.split("\n\n", 1)[0].strip()
        split = source.replace("a0a0a0a0 collected $6 from pot",
                               "a0a0a0a0 collected $3 from pot\nHero collected $3 from pot")
        split = split.replace("Hero: folds\nUncalled bet ($4) returned to a0a0a0a0",
                              "Hero: calls $4").replace("Total pot $6.50", "Total pot $14.50")
        split = split.replace("collected $3", "collected $7")
        hand = parse_hand(split.splitlines())
        self.assertIsNone(hand.winner)
        self.assertTrue(hand.ledger_complete)
        unknown = source.replace("Hero: calls $2", "Hero: mystery action $2")
        hand = parse_hand(unknown.splitlines())
        self.assertFalse(hand.preflop_complete)
        self.assertFalse(hand.ledger_complete)
        self.assertIsNone(hand.winner)
        self.assertTrue(any(action[4] == "Hero: mystery action $2" for action in hand.actions))

    def test_short_all_in_does_not_reopen_and_antes_are_not_vpip(self):
        hand = parse_hand("""Poker Hand #TM900000004: Tournament #123, Synthetic Event Hold'em No Limit - Level4(50/100) - 2026/01/01 09:00:00
Table '7' 6-max Seat #2 is the button
Seat 1: a (1000 in chips)
Seat 2: b (1000 in chips)
Seat 3: c (160 in chips)
a: posts the ante 10
b: posts the ante 10
c: posts the ante 10
b: posts small blind 50
c: posts big blind 100
*** HOLE CARDS ***
a: calls 100
b: calls 50
c: raises 50 to 150 and is all-in
a: calls 50
b: calls 50
*** FLOP *** [2h 5s 9c]
a: checks
b: checks
*** SHOWDOWN ***
c collected 480 from pot
*** SUMMARY ***
Total pot 480 | Rake 0""".splitlines())
        self.assertTrue(hand.preflop_complete)
        self.assertTrue(hand.ledger_complete)
        self.assertEqual(hand.tournament, "123")
        self.assertEqual(hand.unit, "chips")
        self.assertEqual(hand.winner, "c")
        self.assertFalse(hand.seats["a"].pfr)
        self.assertFalse(hand.seats["a"].three_bet_chance)
        self.assertFalse(hand.seats["b"].three_bet_chance)

    def test_bootstraps_and_imports_10000_hands_with_overlap(self):
        template = MORNING.split("\n\n", 1)[0]
        data = "\n\n".join(template.replace("900000001", str(910000000 + index)) for index in range(10000))
        path = self.archive("large.zip", **{"large.txt": data})
        with self.db:
            self.assertEqual(import_zip(self.db, path, session="large")["added"], 10000)
            import_csv(self.db, self.csv("HD910000000,Example Alice\n"))
        overlap = "\n\n".join(template.replace("900000001", str(910000000 + index)) for index in (9999, 10000))
        with self.db:
            counts = import_zip(self.db, self.archive("overlap.zip", **{"overlap.txt": overlap}), session="large")
        self.assertEqual((counts["added"], counts["duplicate"]), (1, 1))
        self.assertEqual(self.stats("Example Alice")[0]["total_observed_hands"], 10001)


if __name__ == "__main__":
    unittest.main()
