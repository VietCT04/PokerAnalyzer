import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from zipfile import ZipFile

from identity import ensure_identities
from poker_analyzer import connect, import_csv, import_zip
from river_bdi import SIZING, bdi_report, population_stat, river_bets, sizing_bucket


def fixture(raises=1, flop=0, turn=0, bet=3, call=None, outcome="caller", rake="0",
            river_raise=False, side_pot=False, unreconciled=False, third_all_in=False, ante=False):
    matched = Decimal(bet if call is None else call)
    preflop = {1: 3, 2: 9, 3: 18}[raises]
    caller_stack = 100 if call is None else preflop + flop + turn + matched
    lines = [
        "Poker Hand #HD900000010: Hold'em No Limit ($0.5/$1) - 2026/01/01 00:00:00",
        "Table 'Sample' 6-max Seat #3 is the button",
        "Seat 1: Bettor ($100 in chips)", f"Seat 2: Caller (${caller_stack} in chips)",
        f"Seat 3: Hero (${preflop if third_all_in else 100} in chips)",
        "Bettor: posts small blind $0.5", "Caller: posts big blind $1", "*** HOLE CARDS ***",
        "Hero: calls $1" if third_all_in else "Hero: folds", "Bettor: raises $2 to $3",
    ]
    if ante:
        lines.insert(5, "Hero: posts the ante $1")
    if raises == 2:
        lines += ["Caller: raises $6 to $9", "Bettor: calls $6"]
    elif raises == 3:
        lines += ["Caller: raises $6 to $9", "Bettor: raises $9 to $18", "Caller: calls $9"]
    else:
        lines += ["Caller: calls $2"]
    if third_all_in:
        lines += [f"Hero: calls ${preflop - 1} and is all-in"]
    lines += ["*** FLOP *** [As 9s 4d]"]
    lines += [f"Bettor: bets ${flop}", f"Caller: calls ${flop}"] if flop else ["Bettor: checks", "Caller: checks"]
    lines += ["*** TURN *** [As 9s 4d] [6h]"]
    lines += [f"Bettor: bets ${turn}", f"Caller: calls ${turn}"] if turn else ["Bettor: checks", "Caller: checks"]
    lines += ["*** RIVER *** [As 9s 4d 6h] [2c]", f"Bettor: bets ${bet}"]
    if river_raise:
        lines += [f"Caller: raises ${bet} to ${2 * bet}", f"Bettor: calls ${bet}"]
        matched = Decimal(2 * bet)
    else:
        lines += [f"Caller: calls ${matched}" + (" and is all-in" if matched < bet else "")]
        if matched < bet:
            lines += [f"Uncalled bet (${Decimal(bet) - matched}) returned to Bettor"]
    lines += ["*** SHOWDOWN ***"]
    total = 2 * (preflop + flop + turn + matched) + (preflop if third_all_in else 0) + int(ante)
    fee = Decimal(rake)
    prize = total - fee
    if outcome in {"tie", "uneven"}:
        first = (prize / 2).quantize(Decimal(".01"))
        if outcome == "uneven":
            first += Decimal(".02")
        lines += [f"Bettor collected ${first} from pot", f"Caller collected ${prize - first} from pot"]
    else:
        lines += [f"{outcome.title()} collected ${prize} from {'side pot' if side_pot else 'pot'}"]
    lines += ["*** SUMMARY ***", f"Total pot ${total + int(unreconciled)} | Rake ${fee}"]
    return "\n".join(lines) + "\n"


class RiverBDITest(unittest.TestCase):
    def import_text(self, db, text, folder):
        archive = Path(folder) / "test.zip"
        with ZipFile(archive, "w") as bundle:
            bundle.writestr("hands.txt", text)
        return import_zip(db, archive)

    def observe(self, **options):
        with tempfile.TemporaryDirectory() as folder, closing(connect(":memory:")) as db:
            self.import_text(db, fixture(**options), folder)
            return list(river_bets(db))

    def test_raise_to_contributions_and_triple_barrel_between_other_players(self):
        [bet] = self.observe(raises=2, flop=2, turn=3, bet=7)
        self.assertEqual((bet.bettor, bet.caller, bet.pot, bet.bet), ("Bettor", "Caller", 28, 7))
        self.assertEqual((bet.pot_type, bet.barrels, bet.sizing), ("3-bet", "Triple barrel", SIZING[0]))
        self.assertAlmostEqual(float(bet.deviation), 1 - 7 / 42)

    def test_effective_size_excludes_return_and_uses_exact_pot_odds(self):
        [bet] = self.observe(bet=10, call=3, outcome="bettor")
        self.assertEqual((bet.pot, bet.bet, bet.outcome, bet.deviation), (6, 3, 0, Decimal("-0.25")))
        self.assertEqual(bet.pot_type, "Single-raised")

    def test_dead_ante_stays_in_the_pot_after_the_player_folds(self):
        [bet] = self.observe(ante=True)
        self.assertEqual((bet.pot, bet.bet), (7, 3))
        self.assertAlmostEqual(float(bet.deviation), 1 - 3 / 13)

    def test_tie_with_odd_cent_and_turn_to_river_barrel(self):
        [bet] = self.observe(raises=3, turn=3, outcome="tie", rake="0.01")
        self.assertEqual((bet.pot_type, bet.barrels, bet.outcome), ("4-bet+", "Double barrel", Decimal("0.5")))

    def test_ineligible_or_ambiguous_hands_are_skipped(self):
        for options in ({"river_raise": True}, {"side_pot": True}, {"unreconciled": True},
                        {"outcome": "uneven"}, {"third_all_in": True}):
            with self.subTest(options=options):
                self.assertEqual(self.observe(**options), [])

    def test_sizing_boundaries_assign_each_size_once(self):
        for amount, bucket in [(32, 0), (33, 1), (65, 1), (66, 2), (99, 2), (100, 3),
                               (149, 3), (150, 4), (250, 4)]:
            self.assertEqual(sizing_bucket(Decimal(100), Decimal(amount)), SIZING[bucket])

    def test_duplicate_export_counts_once(self):
        with tempfile.TemporaryDirectory() as folder, closing(connect(":memory:")) as db:
            self.import_text(db, fixture(), folder)
            self.assertEqual(self.import_text(db, fixture(), folder)["duplicate"], 1)
            report = bdi_report(db)
        overall = report["population"]["overall"][0]
        self.assertEqual((overall["bets"], overall["bet_weighted"], overall["player_weighted"]), (1, 75, None))

    def test_population_weights_players_equally_and_keeps_unknown_bets(self):
        [base] = self.observe()
        first = replace(base, nickname="Alice")
        second = replace(base, nickname="Bob", outcome=Decimal(0))
        result = population_stat([first, first, first, second, base])
        self.assertEqual(result, {"bets": 5, "named_bets": 4, "players": 2,
                                  "bet_weighted": 55, "player_weighted": 25})

    def test_per_player_breakdowns_include_sample_counts(self):
        with tempfile.TemporaryDirectory() as folder, closing(connect(":memory:")) as db:
            self.import_text(db, fixture(outcome="bettor"), folder)
            evidence = Path(folder) / "winners.csv"
            evidence.write_text("hand_id,winner_nickname\nHD900000010,Example Bettor\n", encoding="utf-8")
            import_csv(db, evidence)
            ensure_identities(db)
            report = bdi_report(db)
        player = report["players"]["Example Bettor"]
        self.assertEqual(player["overall"], [{"label": "Overall", "bdi": -25, "bets": 1}])
        self.assertEqual(player["sizing"][1], {"label": SIZING[1], "bdi": -25, "bets": 1})
        self.assertEqual(player["pot_type"][1], {"label": "Single-raised", "bdi": -25, "bets": 1})
        self.assertEqual(report["population"]["distribution"][1]["players"], 1)
