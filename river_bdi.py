"""Calculate the showdown-based Bluff Deviation Index from imported actions."""

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction

from hand_history import DECISIONS, FEE, STAKES, TOTAL, ZERO, parse_amount, wager_delta


SIZING = ("0–33%", "33–66%", "66–100%", "100–150%", "150%+")
POT_TYPES = ("Unraised", "Single-raised", "3-bet", "4-bet+")
BARRELS = ("Single barrel", "Double barrel", "Triple barrel")
SECTIONS = ["preflop", "flop", "turn", "river", "showdown", "summary"]
WAGERS = {"small blind", "big blind", "calls", "bets", "raises"}
SIDE_POT = re.compile(r"\bside[ -]+pot\b", re.I)
HALF = Decimal("0.5")


@dataclass(frozen=True)
class RiverBet:
    hand_id: str
    bettor: str
    caller: str
    nickname: str | None
    pot: Decimal
    bet: Decimal
    outcome: Decimal
    sizing: str
    pot_type: str
    stakes: str
    barrels: str

    @property
    def deviation(self):
        return Fraction(self.outcome) - Fraction(self.bet) / Fraction(self.pot + 2 * self.bet)


def sizing_bucket(pot, bet):
    return SIZING[sum(100 * bet >= bound * pot for bound in (33, 66, 100, 150))]


def stakes_label(header, unit):
    stakes = STAKES.fullmatch(header)
    if stakes:
        try:
            small, small_unit = parse_amount(stakes[2])
            big, big_unit = parse_amount(stakes[3])
        except ValueError:
            return "Unknown"
        if 0 < small <= big and small_unit == big_unit:
            amounts = f"{small_unit or ''}{small.normalize():f}/{big_unit or ''}{big.normalize():f}"
            return f"{amounts} chips" if unit == "chips" else amounts
    return "Unknown"


def replay_before_river(actions, seats, finalists):
    """Replay forced money and wagers, with raise-to amounts local to each street."""
    paid, committed = Counter(), Counter()
    live = {token for token, row in seats.items() if not row["sitting_out"]}
    all_in = set()
    for action in actions:
        kind, token = action["kind"], action["token"]
        if kind == "section" and action["street"] in {"flop", "turn", "river"}:
            if len({committed[player] for player in finalists}) != 1:
                return None
            committed.clear()
        if kind not in WAGERS | {"ante", "return", "folds", "checks"}:
            if kind not in {"section", "dealt", "show"}:
                return None
            continue
        if token not in live or (kind in DECISIONS and token in all_in):
            return None
        if kind == "folds":
            live.remove(token)
        elif kind == "return":
            amount = Decimal(action["amount"])
            if not ZERO < amount <= committed[token]:
                return None
            committed[token] -= amount
            paid[token] -= amount
        elif kind != "checks":
            amount = Decimal(action["amount"])
            delta = wager_delta(kind, amount, committed[token])
            if delta <= ZERO or paid[token] + delta > Decimal(seats[token]["stack"]):
                return None
            paid[token] += delta
            if kind != "ante":
                committed[token] += delta
        if action["raw"].endswith(" and is all-in"):
            all_in.add(token)
    if live != finalists or live & all_in:
        return None
    return paid


def showdown_outcome(hand, seats, actions, bettor, caller, final_pot):
    totals = [a["raw"].split("|") for a in actions
              if a["kind"] == "summary" and a["raw"].startswith("Total pot")]
    if len(totals) != 1 or not (total := TOTAL.fullmatch(totals[0][0].strip())):
        return None
    if parse_amount(total[1])[0] != final_pot:
        return None
    fees = ZERO
    for part in totals[0][1:]:
        if not (fee := FEE.fullmatch(part.strip())):
            return None
        fees += parse_amount(fee[1])[0]
    collected = Counter()
    for action in actions:
        if action["kind"] == "collect":
            if (action["street"] not in {"river", "showdown"} or action["token"] not in {bettor, caller}
                    or action["token"] in collected or SIDE_POT.search(action["raw"].partition(" from ")[2])):
                return None
            amount = Decimal(action["amount"])
            if amount <= ZERO:
                return None
            collected[action["token"]] += amount
    if sum(collected.values()) + fees != final_pot:
        return None
    if any(Decimal(seat["collected"]) != collected[token] for token, seat in seats.items()):
        return None
    if not collected:
        return None
    won, lost = collected[bettor], collected[caller]
    if won and lost:
        odd_chip = Decimal(1) if hand["unit"] == "chips" else Decimal("0.01")
        return HALF if abs(won - lost) <= odd_chip else None
    return ZERO if won else Decimal(1)


def river_bet(hand, appearances, actions):
    if not (hand["ledger_complete"] and hand["preflop_complete"]):
        return None
    if [action["street"] for action in actions if action["kind"] == "section"] != SECTIONS:
        return None
    if any(action["street"] == "showdown" and action["kind"] not in {"section", "show", "collect"}
           for action in actions):
        return None
    river = [action for action in actions if action["street"] == "river"]
    decisions = [action for action in river if action["kind"] in DECISIONS]
    if [action["kind"] for action in decisions] not in (["bets", "calls"], ["checks", "bets", "calls"]):
        return None
    bet, call = decisions[-2:]
    bettor, caller = bet["token"], call["token"]
    if bettor == caller or (len(decisions) == 3 and decisions[0]["token"] != caller):
        return None
    seats = {row["token"]: row for row in appearances}
    if bettor not in seats or caller not in seats:
        return None
    prefix = actions[:actions.index(river[0]) + 1]
    paid = replay_before_river(prefix, seats, {bettor, caller})
    if paid is None:
        return None
    pot = sum(paid.values(), ZERO)
    offered, matched = Decimal(bet["amount"]), Decimal(call["amount"])
    remaining = {token: Decimal(seats[token]["stack"]) - paid[token] for token in (bettor, caller)}
    if (pot <= ZERO or not ZERO < offered <= remaining[bettor]
            or not ZERO < matched == min(offered, remaining[caller])):
        return None
    returns = [action for action in river if action["kind"] == "return"]
    if offered > matched:
        if (len(returns) != 1 or returns[0]["token"] != bettor or returns[0]["seq"] <= call["seq"]
                or Decimal(returns[0]["amount"]) != offered - matched):
            return None
    elif returns:
        return None
    if any(action["seq"] < call["seq"] and action["kind"] == "collect" for action in river):
        return None
    paid[bettor] += matched
    paid[caller] += matched
    if any(paid[token] != Decimal(seat["contributed"]) for token, seat in seats.items()):
        return None
    outcome = showdown_outcome(hand, seats, actions, bettor, caller, pot + 2 * matched)
    if outcome is None:
        return None

    preflop_raises = sum(action["street"] == "preflop" and action["kind"] in {"bets", "raises"} for action in prefix)
    barrels = 1
    for street in ("turn", "flop"):
        aggression = [(action["token"], action["kind"]) for action in prefix
                      if action["street"] == street and action["kind"] in {"bets", "raises"}]
        if aggression != [(bettor, "bets")]:
            break
        barrels += 1

    name = seats[bettor]["nickname"]
    if name and seats[bettor]["method"] != "direct" and any(
        token != bettor and not seat["sitting_out"] and seat["nickname"] == name for token, seat in seats.items()
    ):
        name = None
    return RiverBet(hand["id"], bettor, caller, name, pot, matched, outcome, sizing_bucket(pot, matched),
                    POT_TYPES[min(3, preflop_raises)], stakes_label(hand["header"], hand["unit"]), BARRELS[barrels - 1])


def river_bets(db):
    # The hand primary key and one accepted decision per hand prevent overlap duplicates.
    for hand in db.execute("""
        SELECT id, header, unit, ledger_complete, preflop_complete
        FROM hands
        WHERE ledger_complete AND preflop_complete AND EXISTS (
            SELECT 1 FROM actions
            WHERE hand_id = hands.id AND street = 'river' AND kind = 'bets'
        )
        ORDER BY id
    """):
        appearances = db.execute("""
            SELECT a.*, r.nickname, r.method
            FROM appearances a LEFT JOIN appearance_identities r USING (hand_id, token)
            WHERE a.hand_id = ?
        """, (hand["id"],)).fetchall()
        actions = db.execute("SELECT * FROM actions WHERE hand_id = ? ORDER BY seq", (hand["id"],)).fetchall()
        if (observation := river_bet(hand, appearances, actions)) is not None:
            yield observation


def as_bdi(deviation):
    return float(deviation * 100)


def mean_deviation(bets):
    return sum(bet.deviation for bet in bets) / len(bets)


def player_stat(bets):
    return {"bets": len(bets), "bdi": as_bdi(mean_deviation(bets)) if bets else None}


def group_by(bets, key):
    grouped = defaultdict(list)
    for bet in bets:
        grouped[key(bet)].append(bet)
    return grouped


def population_stat(bets):
    named = group_by((bet for bet in bets if bet.nickname is not None), lambda bet: bet.nickname)
    average = sum(mean_deviation(group) for group in named.values()) / len(named) if named else None
    return {"bets": len(bets), "named_bets": sum(map(len, named.values())), "players": len(named),
            "bet_weighted": as_bdi(mean_deviation(bets)) if bets else None,
            "player_weighted": as_bdi(average) if average is not None else None}


def breakdown(bets, attr, summarize, labels=()):
    groups = group_by(bets, lambda bet: getattr(bet, attr))
    return [{"label": label, **summarize(groups[label])} for label in (labels or sorted(groups))]


def player_distribution(players):
    labels = ("Below −25", "−25 to <0", "0", ">0 to <25", "25 and above")
    counts = [{"label": label, "players": 0, "bets": 0} for label in labels]
    for stats in players.values():
        overall = stats["overall"][0]
        value = overall["bdi"]
        index = 0 if value < -25 else 1 if value < 0 else 2 if value == 0 else 3 if value < 25 else 4
        counts[index]["players"] += 1
        counts[index]["bets"] += overall["bets"]
    return counts


def bdi_report(db):
    bets = list(river_bets(db))
    players = {
        nickname: {"overall": [{"label": "Overall", **player_stat(group)}],
                   "sizing": breakdown(group, "sizing", player_stat, SIZING),
                   "pot_type": breakdown(group, "pot_type", player_stat, POT_TYPES)}
        for nickname, group in sorted(group_by((bet for bet in bets if bet.nickname is not None),
                                               lambda bet: bet.nickname).items())
    }
    return {
        "population": {
            "overall": [{"label": "Overall", **population_stat(bets)}],
            "sizing": breakdown(bets, "sizing", population_stat, SIZING),
            "pot_type": breakdown(bets, "pot_type", population_stat, POT_TYPES),
            "stakes": breakdown(bets, "stakes", population_stat),
            "barrels": breakdown(bets, "barrels", population_stat, BARRELS),
            "distribution": player_distribution(players),
        },
        "players": players,
    }
