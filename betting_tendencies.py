"""Observed betting decisions and outcomes from imported hand actions."""

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from fractions import Fraction
from itertools import groupby

from hand_history import AMOUNT, DECISIONS, FEE, TOTAL, ZERO, parse_amount, wager_delta
from river_bdi import POT_TYPES, SIDE_POT, SIZING, bdi_report, sizing_bucket, stakes_label


STREETS = ("preflop", "flop", "turn", "river")
OPEN = "Betting frequency"
FREQUENCY = (OPEN, "Flop C-bet", "Turn barrel", "River barrel", "Double barrel", "Triple barrel",
             "River probe", "River bet after missed turn C-bet")
SUMMARY = ("WWSF", "WTSD", "W$SD", "Bet fold success", "Aggression frequency")
OUTCOMES = ("All opponents folded", "Called and won", "Called and lost", "Called and tied",
            "Raised", "Unresolved")
BREAKDOWNS = ("overall", "street", "sizing", "position", "stakes", "pot_type", "line", "street_size")
AGGRESSIVE = {"bets", "raises"}
FORCED = {"small blind", "big blind", "ante"}
DROP = re.compile(rf"Cash Drop to Pot\s*:\s*total ({AMOUNT})", re.I)
CASHOUT = re.compile(rf".+: (?:Chooses to EV Cashout|(?:Pays Cashout Risk|Receives Cashout) \({AMOUNT}\))")
BOARD = re.compile(r"(?:(first|second|third) )?(flop|turn|river)(?: ([123]))?")
SHOWDOWN = {"showdown", "first showdown", "second showdown", "third showdown"}
POSITIONS = {
    2: ("BTN/SB", "BB"),
    3: ("BTN", "SB", "BB"),
    4: ("BTN", "SB", "BB", "CO"),
    5: ("BTN", "SB", "BB", "UTG", "CO"),
    6: ("BTN", "SB", "BB", "UTG", "HJ", "CO"),
    7: ("BTN", "SB", "BB", "UTG", "LJ", "HJ", "CO"),
    8: ("BTN", "SB", "BB", "UTG", "UTG+1", "LJ", "HJ", "CO"),
    9: ("BTN", "SB", "BB", "UTG", "UTG+1", "UTG+2", "LJ", "HJ", "CO"),
    10: ("BTN", "SB", "BB", "UTG", "UTG+1", "UTG+2", "UTG+3", "LJ", "HJ", "CO"),
}


@dataclass(frozen=True, slots=True)
class Actor:
    token: str
    nickname: str | None
    scope: tuple
    position: str


@dataclass(frozen=True, slots=True)
class Context:
    street: str
    position: str
    pot_type: str
    stakes: str
    line: str
    # Distinguish players still in the hand and those able to take a betting action.
    field: tuple


@dataclass(frozen=True, slots=True)
class Decision:
    hand_id: str
    actor: Actor
    context: Context
    metric: str
    success: bool
    exposures: tuple = ()
    reached: tuple = ()


@dataclass(slots=True)
class Bet:
    hand_id: str
    actor: Actor
    context: Context
    pot: Decimal
    amount: Decimal
    sizing: str
    participants: set = field(default_factory=set)
    called: bool = False
    raised: bool = False
    folded_out: bool = False
    outcome: str = "Unresolved"

    @property
    def known_response(self):
        return self.called or self.raised or self.folded_out


@dataclass(slots=True)
class Street:
    name: str
    players: set
    opening: Bet | None = None
    aggression: list = field(default_factory=list)
    acted: set = field(default_factory=set)
    checks: list = field(default_factory=list)

    def unraised_bettor(self):
        return self.aggression[0] if len(self.aggression) == 1 and self.opening else None

    def label(self, token):
        if not self.aggression:
            return "checked through" if self.checks else "no betting"
        actor = "player" if self.aggression[-1] == token else "opponent"
        action = "bet" if len(self.aggression) == 1 else "raised"
        return f"{actor} {action}"


class HandReplay:
    def __init__(self, hand, appearances):
        self.hand = hand
        self.live = {row["token"] for row in appearances if not row["sitting_out"]}
        self.stacks = {row["token"]: Decimal(row["stack"]) for row in appearances}
        self.postflop_order = sorted((row for row in appearances if row["token"] in self.live),
                                     key=lambda row: row["seat"])
        button = hand["button"]
        labels = {}
        if any(row["seat"] == button for row in self.postflop_order) and len(self.live) in POSITIONS:
            self.postflop_order.sort(key=lambda row: (row["seat"] <= button, row["seat"]))
            button_order = [self.postflop_order[-1], *self.postflop_order[:-1]]
            labels = dict(zip((row["token"] for row in button_order), POSITIONS[len(self.live)]))
        else:
            self.postflop_order = []
        self.actors = {}
        counts = Counter(row["nickname"] for row in appearances if row["token"] in self.live)
        for row in appearances:
            token, nickname = row["token"], row["nickname"]
            if nickname and counts[nickname] > 1 and row["method"] != "direct":
                nickname = None
            scope = (hand["source_account"], hand["session_id"], token)
            self.actors[token] = Actor(token, nickname, scope, labels.get(token, "Unknown"))
        self.stakes = stakes_label(hand["header"], hand["unit"])
        self.committed, self.paid, self.antes, self.collected = Counter(), Counter(), Counter(), Counter()
        self.all_in = set()
        self.rounds = {}
        self.current = None
        self.flop_players = set()
        self.decisions = []
        self.bets = []
        self.pot = ZERO
        self.drop = False
        self.cashout = False
        self.multiple_boards = False
        self.side_pot = False
        self.summary = False
        self.showdown = False
        self.total_line = None
        self.broken = False

    def available(self):
        return {token for token in self.live - self.all_in if self.paid[token] < self.stacks[token]}

    def context(self, token, level=ZERO):
        street = self.current.name
        raises = len(self.rounds["preflop"].aggression)
        prior = [f"{key.title()} {self.rounds[key].label(token)}" for key in STREETS[1:STREETS.index(street)]]
        if street == "preflop":
            line = ("Unopened" if not self.current.acted & self.live else "Unraised with prior action") if not raises else (
                "Facing open" if raises == 1 else "Facing re-raise")
        else:
            line = " · ".join(prior) or "Flop opening"
            if level:
                line += " · facing bet" if len(self.current.aggression) == 1 else " · facing raise"
        active = self.available()
        order = [row["token"] for row in self.postflop_order if row["token"] in active]
        relative = "IP" if order and order[-1] == token else "OOP" if order else "Unknown"
        return Context(street, self.actors[token].position, POT_TYPES[min(raises, 3)],
                       self.stakes, line, (len(self.live), len(active), relative))

    def opening_metrics(self, token):
        metrics = [OPEN]
        street = self.current.name
        preflop = self.rounds["preflop"].aggression
        pfa = preflop[-1] if preflop else None
        if street == "flop" and token == pfa:
            metrics.append("Flop C-bet")
        if street in {"turn", "river"}:
            flop_bettor = self.rounds["flop"].unraised_bettor()
            if street == "turn" and token == flop_bettor:
                metrics.append("Turn barrel")
                if token == pfa:
                    metrics.append("Double barrel")
            if street == "river":
                turn = self.rounds["turn"]
                if token == turn.unraised_bettor():
                    metrics.append("River barrel")
                    if token == flop_bettor == pfa:
                        metrics.append("Triple barrel")
                turn_checked = not turn.aggression and set(turn.checks) == self.live
                if turn_checked and pfa is not None and flop_bettor == pfa and pfa in turn.checks:
                    metrics.append("River bet after missed turn C-bet")
                if (turn_checked and len(self.live) == 2 and len(turn.checks) == 2
                        and turn.checks == [token, pfa] and not self.current.acted):
                    metrics.append("River probe")
        return metrics

    def decision(self, token, kind, amount):
        active = self.available()
        level = max(self.committed.values(), default=ZERO)
        if token not in active or self.current is None or len(self.live) < 2:
            return False
        owed = level - self.committed[token]
        remaining = self.stacks[token] - self.paid[token]
        if (kind == "checks" and owed) or (kind == "calls" and not ZERO < amount == min(owed, remaining)):
            return False
        if (kind == "bets" and level) or (kind == "raises" and (not level or amount <= level)):
            return False
        delta = wager_delta(kind, amount, self.committed[token]) if kind in AGGRESSIVE | {"calls"} else ZERO
        if delta < ZERO or delta > remaining or (kind in AGGRESSIVE and delta == ZERO):
            return False
        other_can_contest = any(
            other != token and self.stacks[other] - self.paid[other] > level - self.committed[other]
            for other in active
        )
        if kind in AGGRESSIVE and not other_can_contest:
            return False
        context = self.context(token, level if self.current.aggression else ZERO)
        actor = self.actors[token]
        hand_id = self.hand["id"]
        first = token not in self.current.acted
        open_chance = first and not self.current.aggression and other_can_contest and remaining > owed
        if self.current.name != "preflop":
            open_chance = open_chance and kind in {"bets", "checks"}
        if open_chance:
            self.decisions.extend(Decision(hand_id, actor, context, metric, kind in AGGRESSIVE)
                                  for metric in self.opening_metrics(token))
        # Size is known on the opening wager and the decisions directly facing it.
        size = None
        if len(self.current.aggression) == 1 and self.current.opening and token != self.current.opening.actor.token:
            self.current.opening.participants.add(token)
            size = self.current.opening.sizing
        if kind in AGGRESSIVE:
            if self.current.opening is None:
                size = sizing_bucket(self.pot, delta) if self.pot > ZERO else "Unknown"
                self.current.opening = Bet(hand_id, actor, context, self.pot, delta, size, {token})
                self.bets.append(self.current.opening)
            else:
                self.current.opening.raised = True
            self.current.aggression.append(token)
        elif kind == "calls" and self.current.opening is not None:
            self.current.opening.called = True
        elif kind == "folds":
            self.live.remove(token)
            bet = self.current.opening
            if bet and self.live == {bet.actor.token} and not (bet.called or bet.raised):
                bet.folded_out = True
        elif kind == "checks":
            self.current.checks.append(token)
        if kind != "checks":
            exposures = ((context.street, size),) if size is not None else ()
            self.decisions.append(Decision(hand_id, actor, context, "Aggression frequency",
                                           kind in AGGRESSIVE, exposures))
        self.current.acted.add(token)
        self.paid[token] += delta
        self.committed[token] += delta
        self.pot += delta
        return True

    def round_closed(self):
        if len(self.live) < 2:
            return True
        active = self.available()
        level = max(self.committed.values(), default=ZERO)
        return all(self.committed[token] == level and token in self.current.acted for token in active) or (
            len(active) == 1 and all(self.committed[token] == level for token in active)
        )

    def section(self, street):
        if street == "summary":
            self.summary = True
            return True
        if street in SHOWDOWN:
            self.showdown = True
            self.multiple_boards |= street != "showdown"
            return True
        board = BOARD.fullmatch(street)
        if street != "preflop" and not board:
            return False
        if board:
            run, street, ordinal = board.groups()
            if run or ordinal:
                self.multiple_boards = True
            if run in {"second", "third"} or ordinal in {"2", "3"}:
                # Later runouts carry awards and board cards rather than another betting round.
                return True
        if street in self.rounds or street != STREETS[len(self.rounds)]:
            return False
        if self.current and (len(self.live) < 2 or not self.round_closed()):
            return False
        if street != "preflop":
            self.committed.clear()
        self.current = self.rounds[street] = Street(street, self.live.copy())
        if street == "flop":
            self.flop_players = self.live.copy()
        return True

    def action(self, row):
        kind, token, raw = row["kind"], row["token"], row["raw"]
        if kind == "section":
            return self.section(row["street"])
        if kind == "summary":
            if raw.startswith("Total pot "):
                self.total_line = raw if self.total_line is None else ""
            return True
        if kind in {"dealt", "show"}:
            return True
        if kind == "other":
            if row["street"] == "setup" and not self.drop and (drop := DROP.fullmatch(raw)):
                amount, unit = parse_amount(drop[1])
                if amount <= ZERO or (unit and unit != self.hand["unit"]):
                    return False
                self.pot += amount
                self.drop = True
                return True
            if CASHOUT.fullmatch(raw):
                self.cashout = True
                return True
            return False
        if token not in self.live:
            return False
        amount = Decimal(row["amount"]) if row["amount"] is not None else ZERO
        if kind in DECISIONS:
            street = row["street"]
            board = BOARD.fullmatch(street)
            if (self.summary or self.showdown or self.current is None
                    or (board and (board[1] in {"second", "third"} or board[3] in {"2", "3"}))
                    or (board[2] if board else street) != self.current.name
                    or not self.decision(token, kind, amount)):
                return False
            if raw.endswith(" and is all-in"):
                self.all_in.add(token)
        elif kind in FORCED:
            if self.current is not None or not ZERO < amount <= self.stacks[token] - self.paid[token]:
                return False
            self.paid[token] += amount
            if kind == "ante":
                self.antes[token] += amount
            else:
                self.committed[token] += amount
            self.pot += amount
        elif kind == "return":
            if not ZERO < amount <= self.committed[token]:
                return False
            self.committed[token] -= amount
            self.paid[token] -= amount
            self.pot -= amount
        elif kind == "collect":
            if amount <= ZERO:
                return False
            self.collected[token] += amount
            self.side_pot |= bool(SIDE_POT.search(raw.rpartition(" from ")[2]))
        else:
            return False
        return True

    def settled(self):
        parts = (self.total_line or "").split("|")
        total = TOTAL.fullmatch(parts[0].strip())
        if not total or parse_amount(total[1])[0] != self.pot or self.cashout:
            return False
        fees = ZERO
        for part in parts[1:]:
            if not (fee := FEE.fullmatch(part.strip())):
                return False
            fees += parse_amount(fee[1])[0]
        return bool(self.collected) and sum(self.collected.values(), ZERO) + fees == self.pot

    def finish(self):
        complete = (not self.broken and self.summary and self.current is not None
                    and (len(self.live) == 1 or (self.showdown and len(self.live) >= 2 and self.round_closed())))
        showdown = complete and self.showdown and len(self.live) >= 2
        known = complete and self.settled()
        odd_chip = Decimal(1) if self.hand["unit"] == "chips" else Decimal("0.01")
        awards = list(self.collected.values())
        if showdown:
            self.side_pot |= len({self.paid[token] - self.antes[token] for token in self.live}) > 1
        clear_awards = (known and not (self.multiple_boards or self.side_pot)
                        and max(awards) - min(awards) <= odd_chip)
        for bet in self.bets:
            if bet.raised:
                bet.outcome = "Raised"
            elif bet.folded_out:
                bet.outcome = "All opponents folded"
            elif bet.called and clear_awards:
                bet.outcome = ("Called and lost" if bet.actor.token not in self.collected
                               else "Called and tied" if len(awards) > 1 else "Called and won")
        if complete and self.flop_players:
            raises = len(self.rounds["preflop"].aggression)
            for token in self.flop_players:
                actor = self.actors[token]
                line = " · ".join(f"{name.title()} {round_.label(token)}" for name, round_ in self.rounds.items()
                                  if name != "preflop")
                context = Context("flop", actor.position, POT_TYPES[min(raises, 3)], self.stakes, line, ())
                exposures = tuple((bet.context.street, bet.sizing) for bet in self.bets if token in bet.participants)
                reached = tuple(name for name, round_ in self.rounds.items() if token in round_.players)
                self.decisions.append(Decision(self.hand["id"], actor, context, "WTSD",
                                               showdown and token in self.live, exposures, reached))
                if known:
                    won = token in self.collected
                    self.decisions.append(Decision(self.hand["id"], actor, context, "WWSF", won, exposures, reached))
                    if showdown and token in self.live:
                        self.decisions.append(Decision(self.hand["id"], actor, context, "W$SD", won, exposures, reached))
        return complete, known


def historical_observations(db):
    actors = groupby(db.execute("""
        SELECT a.*, r.nickname, r.method FROM appearances a
        LEFT JOIN appearance_identities r USING (hand_id, token) ORDER BY a.hand_id, a.seat
    """), lambda row: row["hand_id"])
    actions = groupby(db.execute("SELECT * FROM actions ORDER BY hand_id, seq"), lambda row: row["hand_id"])
    decisions, bets, identities = [], [], defaultdict(set)
    audit = Counter()
    appearance_id, appearances = next(actors, (None, ()))
    action_id, records = next(actions, (None, ()))
    for hand in db.execute("SELECT * FROM hands ORDER BY id"):
        audit["imported_hands"] += 1
        seats = []
        hand_actions = []
        if appearance_id == hand["id"]:
            seats = list(appearances)
            appearance_id, appearances = next(actors, (None, ()))
        if action_id == hand["id"]:
            hand_actions = list(records)
            action_id, records = next(actions, (None, ()))
        for seat in seats:
            if seat["nickname"] and not seat["sitting_out"]:
                identities[seat["nickname"]].add((hand["source_account"], hand["session_id"], seat["token"]))
        if not seats or not hand_actions:
            audit["missing_actions_or_seats"] += 1
            continue
        replay = HandReplay(hand, seats)
        for row in hand_actions:
            if not replay.action(row):
                replay.broken = True
                audit[f"unsupported_{row['kind']}"] += 1
                break
        complete, known = replay.finish()
        audit["complete_hands"] += complete
        audit["settled_hands"] += known
        audit["prefix_only_hands"] += replay.broken
        audit["multiple_board_hands"] += replay.multiple_boards
        audit["cash_drop_hands"] += replay.drop
        audit["cashout_hands"] += replay.cashout
        audit["side_pot_hands"] += replay.side_pot
        decisions.extend(replay.decisions)
        bets.extend(replay.bets)
    return decisions, bets, identities, dict(audit)


def counts(items):
    return {"samples": len(items), "hands": len({item.hand_id for item in items}),
            "named": sum(item.actor.nickname is not None for item in items)}


def rate(events, samples):
    return 100 * events / samples if samples else None


def matches_scope(item, nickname):
    return nickname is None or item.actor.nickname == nickname


def benchmark_pool(decisions, nickname, excluded_scopes):
    """Bet-weighted counts for matching situations, leaving out the target."""
    pool = defaultdict(Counter)
    for item in decisions:
        actor = item.actor
        if (actor.nickname == nickname or (actor.nickname is None and actor.scope in excluded_scopes)
                or item.context.position == "Unknown" or item.context.stakes == "Unknown"):
            continue
        pool[(item.metric, item.context)][item.success] += 1
    return pool


def frequency_row(items, pool=None):
    result = counts(items)
    events = sum(item.success for item in items)
    result.update(events=events, pct=rate(events, len(items)))
    if pool is None:
        return result
    baseline = Fraction(0)
    observed = matched = reference = 0
    strata = defaultdict(Counter)
    for item in items:
        strata[(item.metric, item.context)][item.success] += 1
    for key, player in strata.items():
        samples = sum(player.values())
        others = pool.get(key, {})
        total = sum(others.values())
        if total:
            matched += samples
            observed += player[True]
            baseline += Fraction(others.get(True, 0) * samples, total)
            reference += total
    result.update(matched=matched, benchmark_samples=reference,
                  matched_pct=rate(observed, matched),
                  benchmark_pct=float(100 * baseline / matched) if matched else None,
                  bfd=float(100 * (observed - baseline) / matched) if matched else None)
    return result


def labels(item, by):
    if by == "overall":
        return ("Overall",)
    if by in {"sizing", "street_size"}:
        exposures = ((item.context.street, item.sizing),) if isinstance(item, Bet) else item.exposures
        return tuple(dict.fromkeys(size if by == "sizing" else f"{street.title()} · {size}"
                                   for street, size in exposures))
    if by == "street":
        streets = (item.reached or (item.context.street,)) if isinstance(item, Decision) else (item.context.street,)
        return tuple(street.title() for street in streets)
    return (getattr(item.context, by),)


def ordered_labels(keys, by):
    order = {
        "street": tuple(street.title() for street in STREETS),
        "sizing": SIZING + ("Unknown",),
        "street_size": tuple(f"{street.title()} · {size}" for street in STREETS for size in SIZING + ("Unknown",)),
        "pot_type": POT_TYPES,
    }.get(by, ())
    return list(dict.fromkeys([*(label for label in order if label in keys), *sorted(keys)]))


def by_metric(items, by, metrics, summarize):
    grouped = defaultdict(lambda: defaultdict(list))
    for item in items:
        for label in ((item.context.street.title(),) if by == "overall" and item.metric == OPEN else labels(item, by)):
            grouped[item.metric][label].append(item)
    result = []
    for metric in metrics:
        rows = grouped[metric]
        if by == "overall":
            row_labels = [street.title() for street in STREETS] if metric == OPEN else ["Overall"]
        else:
            row_labels = ordered_labels(rows, by)
        for label in row_labels:
            result.append({"metric": metric, "label": label, **summarize(rows[label])})
    return result


def bet_outcomes(bets, by):
    grouped = defaultdict(list)
    for bet in bets:
        for label in labels(bet, by):
            grouped[label].append(bet)
    if by == "overall":
        grouped["Overall"]
    elif by == "street":
        for street in STREETS:
            grouped[street.title()]
    elif by == "sizing":
        for size in SIZING:
            grouped[size]
    rows = []
    for label in ordered_labels(grouped, by):
        group = grouped[label]
        outcomes = Counter(bet.outcome for bet in group)
        rows.append({"label": label, **counts(group), "outcomes": dict.fromkeys(OUTCOMES, 0) | outcomes})
    return rows


def tendencies_report(db, nickname=None):
    decisions, bets, identities, audit = historical_observations(db)
    audit["hands_with_decisions"] = len({item.hand_id for item in decisions})
    audit["hands_with_bets"] = len({item.hand_id for item in bets})
    opening = [item for item in decisions if item.metric in FREQUENCY]
    pool = benchmark_pool(opening, nickname, identities[nickname]) if nickname is not None else None
    frequencies = [item for item in opening if matches_scope(item, nickname)]
    selected_bets = [bet for bet in bets if matches_scope(bet, nickname)]
    summary = [item for item in decisions if item.metric in SUMMARY and matches_scope(item, nickname)]
    summary.extend(Decision(bet.hand_id, bet.actor, bet.context, "Bet fold success", bet.folded_out,
                            ((bet.context.street, bet.sizing),)) for bet in selected_bets if bet.known_response)

    bdi = bdi_report(db)
    if nickname is None:
        bdi_overall = bdi["population"]["overall"][0]
    else:
        bdi_overall = bdi["players"].get(nickname, {"overall": [{"bets": 0, "bdi": None}]})["overall"][0]
    return {
        "nickname": nickname, "audit": audit, "bdi": bdi_overall, "outcome_labels": OUTCOMES,
        "frequencies": {by: by_metric(frequencies, by, FREQUENCY, lambda values: frequency_row(values, pool))
                        for by in BREAKDOWNS if by not in {"sizing", "street_size"}},
        "outcomes": {by: bet_outcomes(selected_bets, by) for by in BREAKDOWNS},
        "summary": {by: by_metric(summary, by, SUMMARY, frequency_row) for by in BREAKDOWNS},
    }
