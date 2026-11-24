import unittest
from contextlib import closing
from dataclasses import replace

from betting_tendencies import (
    Actor, Context, Decision, OPEN, benchmark_pool, frequency_row, historical_observations, tendencies_report,
)
from hand_history import parse_hand
from poker_analyzer import connect, store_hand
from test_river_bdi import fixture


def add_hand(db, text):
    hand = parse_hand(text.splitlines())
    return store_hand(db, hand, ("session:test", "Sample", ""), hand.id, "fixture", "local")


class BettingTendenciesTest(unittest.TestCase):
    def replay(self, text):
        with closing(connect(":memory:")) as db:
            add_hand(db, text)
            decisions, bets, _, audit = historical_observations(db)
        return decisions, bets, audit

    def test_non_showdown_bet_uses_prebet_pot_and_keeps_unnamed_players(self):
        early = fixture(ante=True).split("*** FLOP")[0]
        text = early + """*** FLOP *** [As 9s 4d]
Bettor: bets $3
Caller: folds
Uncalled bet ($3) returned to Bettor
*** SHOWDOWN ***
Bettor collected $7 from pot
*** SUMMARY ***
Total pot $7 | Rake $0
"""
        with closing(connect(":memory:")) as db:
            self.assertEqual(add_hand(db, text), "added")
            self.assertEqual(add_hand(db, text), "duplicate")
            decisions, bets, _, _ = historical_observations(db)
            report = tendencies_report(db)
        flop, = [bet for bet in bets if bet.context.street == "flop"]
        self.assertEqual((flop.pot, flop.amount, flop.outcome, flop.sizing), (7, 3, "All opponents folded", "33–66%"))
        cbet, = [event for event in decisions if event.metric == "Flop C-bet"]
        self.assertTrue(cbet.success)
        row = next(row for row in report["outcomes"]["street"] if row["label"] == "Flop")
        self.assertEqual((row["samples"], row["named"], row["outcomes"]["All opponents folded"]), (1, 0, 1))
        stats = {row["metric"]: row for row in report["summary"]["overall"]}
        self.assertEqual((stats["WTSD"]["events"], stats["WWSF"]["events"], stats["WWSF"]["samples"]), (0, 1, 2))

    def test_called_flop_can_win_by_a_later_fold(self):
        text = fixture(flop=2).split("*** TURN")[0] + """*** TURN *** [As 9s 4d] [6h]
Bettor: bets $2
Caller: folds
Uncalled bet ($2) returned to Bettor
*** SHOWDOWN ***
Bettor collected $10 from pot
*** SUMMARY ***
Total pot $10 | Rake $0
"""
        decisions, bets, _ = self.replay(text)
        self.assertEqual([bet.outcome for bet in bets], ["Called and won", "Called and won", "All opponents folded"])
        self.assertTrue(any(event.metric == "Double barrel" and event.success for event in decisions))

    def test_river_raise_is_exclusive_and_unraised_split_is_tied(self):
        for text, wanted in [(fixture(river_raise=True), "Raised"), (fixture(outcome="tie"), "Called and tied"),
                             (fixture(), "Called and lost")]:
            with self.subTest(outcome=wanted):
                _, bets, _ = self.replay(text)
                self.assertEqual(bets[-1].outcome, wanted)

    def test_triple_barrel_and_river_probe_have_different_eligibility(self):
        decisions, _, _ = self.replay(fixture(flop=2, turn=2))
        self.assertTrue(any(event.metric == "Triple barrel" and event.success for event in decisions))
        self.assertFalse(any(event.metric == "River probe" for event in decisions))
        probe = fixture(raises=2, flop=2).replace(
            "Bettor: bets $2\nCaller: calls $2", "Bettor: checks\nCaller: bets $2\nBettor: calls $2"
        )
        decisions, _, _ = self.replay(probe)
        for name in ("River probe", "River bet after missed turn C-bet"):
            event, = [event for event in decisions if event.metric == name]
            self.assertEqual((event.actor.token, event.success), ("Bettor", True))
        double, = [event for event in decisions if event.metric == "Double barrel"]
        self.assertFalse(double.success)

    def test_recorded_decisions_survive_missing_payout_evidence(self):
        text = fixture(flop=2).replace("Total pot $16", "Total pot $17")
        _, bets, audit = self.replay(text)
        self.assertTrue(all(bet.outcome == "Unresolved" and bet.known_response for bet in bets))
        self.assertEqual((audit["complete_hands"], audit["settled_hands"]), (1, 0))

    def test_side_pot_bets_count_with_unresolved_results(self):
        for options in ({"side_pot": True}, {"third_all_in": True}):
            with self.subTest(options=options):
                _, bets, audit = self.replay(fixture(**options))
                self.assertEqual(bets[-1].outcome, "Unresolved")
                self.assertEqual(audit["side_pot_hands"], 1)

    def test_cash_drop_adds_money_before_the_first_bet(self):
        text = fixture().replace("Bettor: posts small blind",
                                 "Cash Drop to Pot : total $2\nBettor: posts small blind")
        text = text.replace("collected $12", "collected $14").replace("Total pot $12", "Total pot $14")
        _, bets, audit = self.replay(text)
        self.assertEqual((bets[0].pot, bets[-1].pot, bets[-1].outcome), (3.5, 8, "Called and lost"))
        self.assertEqual(audit["cash_drop_hands"], 1)

    def test_bfd_matches_situations_and_excludes_target_and_scoped_unknowns(self):
        actor = Actor("a", "Target", ("account", 1, "a"), "BTN")
        context = Context("flop", "BTN", "Single-raised", "$1/$2", "Flop opening", (2, 2, "IP"))
        matched = Decision("1", actor, context, OPEN, True)
        second = replace(matched, hand_id="2", context=replace(context, pot_type="3-bet"))
        third = replace(matched, hand_id="3", context=replace(context, position="CO"))
        target = [matched, replace(matched, success=False), matched, replace(second, success=False), third]
        anonymous = Actor("b", None, ("account", 1, "b"), "BTN")
        named = Actor("c", "Other", ("account", 1, "c"), "BTN")
        others = [replace(matched, actor=anonymous), replace(matched, actor=named, success=False),
                  replace(second, actor=anonymous, success=False), replace(matched, actor=replace(actor, nickname=None))]
        pool = benchmark_pool([*target, *others], "Target", {actor.scope})
        line = frequency_row(target, pool)
        self.assertEqual((line["pct"], line["matched_pct"], line["benchmark_pct"], line["bfd"]), (60, 50, 37.5, 12.5))
        self.assertEqual((line["matched"], line["benchmark_samples"]), (4, 3))

    def test_missing_records_do_not_hide_other_hands(self):
        with closing(connect(":memory:")) as db:
            add_hand(db, "Poker Hand #HD1: Hold'em No Limit ($0.5/$1) - 2026/01/01 00:00:00")
            add_hand(db, fixture())
            decisions, _, _, audit = historical_observations(db)
        self.assertEqual(audit["missing_actions_or_seats"], 1)
        self.assertEqual({event.hand_id for event in decisions}, {"HD900000010"})
