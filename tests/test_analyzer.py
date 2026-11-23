import contextlib
import io
import sqlite3
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from hand_history import parse_hand
from poker_analyzer import (STATS_COLUMNS, connect, import_batch, import_csv, import_zip, print_coverage,
                            rebuild_database, statistics)
from raw_store import raw_dir


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

    def test_disagreeing_direct_evidence_leaves_identity_unresolved(self):
        import_zip(self.db, self.archive(), member_sessions=SESSIONS)
        import_csv(self.db, EXAMPLES / "winners.csv")
        import_csv(self.db, self.csv("HD900000001,Example Other\n"))
        self.assertEqual(self.stats("Example Alice"), [])
        self.assertEqual(self.stats("Example Other"), [])
        self.assertEqual(self.stats("Example Casey")[0]["total_observed_hands"], 1)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            print_coverage(self.db)
        self.assertIn("Mapping conflict:", output.getvalue())
        self.assertIn("direct_conflict", output.getvalue())
        self.assertIn("timestamp_tie", output.getvalue())

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

    def test_multiple_tokens_share_a_nickname_without_double_counting_hands(self):
        swapped = EVENING.replace("a0a0a0a0", "b0b0b0b0")
        import_zip(self.db, self.archive(**{"morning.txt": MORNING, "evening.txt": swapped}), session="same")
        import_csv(self.db, self.csv("900000001,Example Alice\n900000003,Example Alice\n"))
        player, = self.stats("Example Alice")
        self.assertEqual((player["total_observed_hands"], player["direct_hands"], player["inferred_hands"]), (3, 2, 1))
        self.assertEqual(player["net_hands"], 2)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM mapping_candidates").fetchone()[0], 2)

    def test_one_token_changes_nickname_by_nearest_timestamp_and_ties_stay_unresolved(self):
        template = MORNING.split("\n\n", 1)[0]
        stamps = ("09:00:00.100", "09:00:00.900", "09:00:00.200", "09:00:00.800", "09:00:00.500")
        records = "\n\n".join(template.replace("900000001", str(900000010 + index)).replace("09:00:00", time)
                              for index, time in enumerate(stamps))
        import_zip(self.db, self.archive(**{"timeline.txt": records}), session="timeline")
        import_csv(self.db, self.csv("HD900000010,Example Alice\n"))
        self.assertEqual(self.stats("Example Alice")[0]["total_observed_hands"], 5)
        import_csv(self.db, self.csv("HD900000011,Example Bob\n"))
        alice, = self.stats("Example Alice")
        bob, = self.stats("Example Bob")
        self.assertEqual((alice["direct_hands"], alice["inferred_hands"], bob["direct_hands"], bob["inferred_hands"]),
                         (1, 1, 1, 1))
        self.assertEqual([tuple(row) for row in self.db.execute("""
            SELECT hand_id, nickname, method, evidence_hand_id
            FROM appearance_identities WHERE token = 'a0a0a0a0' ORDER BY hand_id
        """)], [
            ("HD900000010", "Example Alice", "direct", "HD900000010"),
            ("HD900000011", "Example Bob", "direct", "HD900000011"),
            ("HD900000012", "Example Alice", "inferred", "HD900000010"),
            ("HD900000013", "Example Bob", "inferred", "HD900000011"),
            ("HD900000014", None, "timestamp_tie", None),
        ])
        self.assertEqual([row[0] for row in self.db.execute("""
            SELECT evidence_timestamp FROM mapping_evidence ORDER BY hand_id
        """)], ["2026/01/01 09:00:00.100", "2026/01/01 09:00:00.900"])

    def test_direct_evidence_wins_when_other_names_have_the_same_timestamp(self):
        template = MORNING.split("\n\n", 1)[0]
        records = "\n\n".join(template.replace("900000001", str(900000020 + index)) for index in range(3))
        import_zip(self.db, self.archive(**{"timeline.txt": records}), session="timeline")
        import_csv(self.db, self.csv("HD900000020,Example Alice\nHD900000021,Example Bob\n"))
        self.assertEqual(self.stats("Example Alice")[0]["direct_hands"], 1)
        self.assertEqual(self.stats("Example Bob")[0]["direct_hands"], 1)
        self.assertEqual(self.db.execute("""
            SELECT method FROM appearance_identities WHERE hand_id = 'HD900000022' AND token = 'a0a0a0a0'
        """).fetchone()[0], "timestamp_tie")

    def test_evidence_stays_with_its_source_account_and_session(self):
        first = self.archive("morning.zip", **{"morning.txt": MORNING})
        second = self.archive("evening.zip", **{"evening.txt": EVENING})
        import_zip(self.db, first, session="same", source_account="account-a")
        import_zip(self.db, second, session="same", source_account="account-b")
        import_csv(self.db, self.csv("HD900000001,Example Alice\nHD900000003,Wrong Account\n"),
                   source_account="account-a")
        self.assertEqual(self.stats("Example Alice")[0]["total_observed_hands"], 2)
        self.assertEqual(self.stats("Wrong Account"), [])
        self.assertIsNone(self.db.execute("SELECT 1 FROM appearance_identities WHERE hand_id = 'HD900000003'").fetchone())
        import_csv(self.db, self.csv("HD900000003,Example Casey\n"), source_account="account-b")
        self.assertEqual(self.stats("Example Casey")[0]["direct_hands"], 1)
        self.assertEqual(import_zip(self.db, first, session="same", source_account="account-b")["account conflict"], 2)
        third = EVENING.replace("HD900000003", "HD900000004")
        import_zip(self.db, self.archive("next.zip", **{"next.txt": third}), session="next", source_account="account-a")
        self.assertEqual(self.stats("Example Alice")[0]["total_observed_hands"], 2)

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

    def test_native_rush_exports_propagate_hero_across_tables_within_each_member(self):
        def as_rush(text, table):
            return text.replace("Poker Hand #HD", "Poker Hand #RC").replace("Synthetic 1", table)

        def hero_wins(text):
            return text.replace("Hero", "TemporaryAlias").replace("a0a0a0a0", "Hero").replace("TemporaryAlias", "a0a0a0a0")

        win, loss = MORNING.split("\n\n", 1)
        morning = as_rush(hero_wins(win), "RushAndCash9101") + "\n\n" + as_rush(loss, "RushAndCash9102")
        evening = as_rush(hero_wins(EVENING), "RushAndCash9103")
        members = {
            "GG20260101-0900 - RushAndCash9101 - 0.50 - 1 - 6max.txt": morning,
            "GG20260101-1900 - RushAndCash9103 - 0.50 - 1 - 6max.txt": evening,
        }
        archive = self.archive(**members)
        import_csv(self.db, self.csv("RC900000001,Example Alice\nRC900000003,Example Casey\n"))
        self.assertEqual(import_zip(self.db, archive, member_sessions={})["added"], 3)
        self.assertEqual(self.stats("Example Alice")[0]["total_observed_hands"], 1)

        self.assertEqual(import_zip(self.db, archive)["scoped"], 3)
        alice, = self.stats("Example Alice")
        self.assertEqual(alice["total_observed_hands"], 2)
        self.assertEqual(Decimal(alice["net_winnings"]), Decimal(0))
        self.assertEqual(self.stats("Example Casey")[0]["total_observed_hands"], 1)
        self.assertEqual(self.db.execute("SELECT COUNT(DISTINCT session_id), COUNT(DISTINCT table_name) FROM hands")
                         .fetchone()[:], (2, 3))
        self.assertEqual(import_zip(self.db, self.archive("overlap.zip", **members))["duplicate"], 3)

    def test_mislabeled_rush_member_stays_hand_local(self):
        valid = MORNING.split("\n\n", 1)[0].replace("Poker Hand #HD", "Poker Hand #RC").replace(
            "Synthetic 1", "RushAndCash9101")
        member = "GG20260101-0900 - RushAndCash9101 - 0.50 - 1 - 6max.txt"
        archive = self.archive(**{member: valid + "\n\n" + EVENING})
        import_zip(self.db, archive)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM sessions WHERE scope LIKE 'hand:%'").fetchone()[0], 2)

    def test_preexisting_database_retains_original_hand_table(self):
        path = self.folder / "legacy.sqlite"
        schema = (EXAMPLES.parent / "schema.sql").read_text(encoding="utf-8")
        with contextlib.closing(sqlite3.connect(path)) as legacy, legacy:
            legacy.executescript(schema.replace(",\n    table_name TEXT NOT NULL DEFAULT ''", "", 1)
                                 .replace(",\n    source_account TEXT NOT NULL DEFAULT 'local'", "", 1))
            legacy.execute("INSERT INTO sessions VALUES (1, 'hand:RC1', 'RushAndCash1', '')")
            legacy.execute("""
                INSERT INTO hands VALUES ('RC1', '1', 1, 'x.zip/x.txt', '', '', '', 6, 1, '$', '1', NULL, 0, 0)
            """)
        with contextlib.closing(connect(path)) as migrated:
            self.assertEqual(migrated.execute("SELECT table_name FROM hands").fetchone()[0], "RushAndCash1")

    def test_rebuild_preserves_explicit_scope_and_skips_failed_batches(self):
        path = self.folder / "poker.sqlite"
        export = self.archive()
        import_batch(self.db, path, [EXAMPLES / "winners.csv", export], member_sessions=SESSIONS,
                     source_account="research-account")
        failed = self.folder / "invalid.csv"
        failed.write_text("missing,fields\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            import_batch(self.db, path, [failed])
        archive = raw_dir(path)
        self.assertEqual(len(list((archive / "batches").glob("*.failed.json"))), 1)
        recovered = self.folder / "recovered.sqlite"
        self.assertEqual(rebuild_database(recovered, archive), 1)
        with contextlib.closing(connect(recovered)) as db:
            self.assertEqual(statistics(db), statistics(self.db))
            self.assertEqual(db.execute("SELECT DISTINCT source_account FROM hands").fetchone()[0], "research-account")


if __name__ == "__main__":
    unittest.main()
