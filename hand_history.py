"""Read English GGPoker hand histories and derive auditable per-hand facts."""

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from io import TextIOWrapper
from itertools import groupby
from operator import itemgetter
from pathlib import PurePosixPath
from zipfile import ZipFile


ZERO = Decimal(0)
HAND_ID = r"[A-Z]*[0-9]+"
HEADER = re.compile(rf"Poker Hand #({HAND_ID}):\s*(.*)", re.I)
STAKES = re.compile(r"^(.*?)\s*\(([^()/]+)\s*/\s*([^()/]+)\)\s*-\s*(.+)$")
TABLE = re.compile(r"Table '(.*)' (\d+)-max(?: Seat #(\d+) is the button)?")
RUSH_TABLE = r"RushAndCash[0-9]+"
RUSH_MEMBER = re.compile(
    rf"GG[0-9]{{8}}-[0-9]{{4}} - ({RUSH_TABLE}) - ([0-9]+(?:\.[0-9]+)?) - "
    r"([0-9]+(?:\.[0-9]+)?) - ([0-9]+)max\.txt"
)
SEAT = re.compile(r"Seat (\d+): (.+?) \(([^()]+) in chips\)(.*)")
UNIT = r"[$€£]|C\$|T\$"
NUMBER = r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?"
AMOUNT = rf"(?:{UNIT})?\s*{NUMBER}"
AMOUNT_RE = re.compile(rf"(?P<unit>{UNIT})?\s*(?P<number>{NUMBER})")
PAYOUT = re.compile(rf"(.+?) collected ({AMOUNT}) from (?:.*\s)?pot.*")
RETURN = re.compile(rf"Uncalled bet \(({AMOUNT})\) returned to (.+)")
POST = re.compile(rf"posts (the ante|ante|small blind|big blind) ({AMOUNT})")
WAGER = re.compile(rf"(calls|bets) ({AMOUNT})")
RAISE = re.compile(rf"raises (?:{AMOUNT}) to ({AMOUNT})")
TOTAL = re.compile(rf"Total pot ({AMOUNT})")
FEE = re.compile(rf"(?:Rake|Jackpot|Bingo|Fortune|Tax|Fee) ({AMOUNT})", re.I)
DECISIONS = {"folds", "checks", "calls", "bets", "raises"}


def normalize_id(value):
    value = re.sub(r"^(?:POKER\s+HAND|HAND\s+ID|HAND)(?=[\s:#])\s*:?\s*", "", value.strip().upper())
    value = re.sub(r"^#\s*", "", value)
    match = re.fullmatch(r"([A-Z]*)([0-9]+)", value)
    if not match:
        raise ValueError(f"invalid hand ID: {value!r}")
    return match[1] + (match[2].lstrip("0") or "0")


def numeric_id(hand_id):
    return hand_id.lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ")


def parse_amount(value):
    match = AMOUNT_RE.fullmatch(value.strip())
    if not match:
        raise ValueError(f"unsupported amount: {value!r}")
    return Decimal(match["number"].replace(",", "")), match["unit"]


def wager_delta(kind, amount, committed):
    return amount - committed if kind == "raises" else amount


@dataclass
class Seat:
    number: int
    stack: Decimal
    sitting_out: bool
    paid: Decimal = ZERO
    collected: Decimal = ZERO
    decision: bool = False
    vpip: bool = False
    pfr: bool = False
    three_bet_chance: bool = False
    three_bet: bool = False


@dataclass
class Hand:
    id: str
    header: str
    table: str = ""
    max_seats: int | None = None
    button: int | None = None
    played_at: str = ""
    game: str = ""
    tournament: str = ""
    unit: str = ""
    big_blind: Decimal | None = None
    seats: dict = field(default_factory=dict)
    actions: list = field(default_factory=list)
    preflop_complete: bool = False
    ledger_complete: bool = False
    winner: str | None = None


def parse_hand(lines):
    header = HEADER.fullmatch(lines[0])
    hand = Hand(normalize_id(header[1]), header[2])
    hand.tournament = tournament[1] if (tournament := re.search(r"Tournament #(\d+)", header[2])) else ""
    if stakes := STAKES.fullmatch(header[2]):
        hand.game, hand.played_at = stakes[1], stakes[4]
        try:
            _, small_unit = parse_amount(stakes[2])
            hand.big_blind, big_unit = parse_amount(stakes[3])
            if small_unit == big_unit:
                hand.unit = big_unit or ("chips" if hand.tournament else "")
        except ValueError:
            pass

    street = "setup"
    committed = Counter()
    acted_at = {}
    folded = set()
    explicitly_all_in = set()
    bet_level = ZERO
    min_raise = hand.big_blind or ZERO
    raises = 0
    preflop_ok = bool(hand.big_blind and re.search(r"\b(?:No|Pot) Limit\b", hand.game, re.I))
    money_ok = bool(hand.unit)
    payout_ok = True
    saw_preflop = saw_big_blind = saw_board = saw_summary = False
    pot = None
    fees = ZERO

    def amount(value):
        nonlocal money_ok, payout_ok, preflop_ok
        number, symbol = parse_amount(value)
        if symbol and symbol != hand.unit:
            money_ok = payout_ok = preflop_ok = False
        return number

    def eligible(alias):
        seat = hand.seats[alias]
        return (alias not in folded and alias not in explicitly_all_in
                and not seat.sitting_out and seat.stack > seat.paid)

    for raw in lines[1:]:
        if street == "setup" and (match := TABLE.fullmatch(raw)):
            hand.table, hand.max_seats = match[1], int(match[2])
            hand.button = int(match[3]) if match[3] else None
            continue
        if street == "setup" and (match := SEAT.fullmatch(raw)):
            if match[2] in hand.seats or any(seat.number == int(match[1]) for seat in hand.seats.values()):
                raise ValueError(f"{hand.id}: duplicate seat number or token")
            hand.seats[match[2]] = Seat(int(match[1]), amount(match[3]), "sitting out" in match[4].lower())
            continue
        if raw.startswith("*** "):
            label = raw.split("***")[1].strip()
            if label == "HOLE CARDS":
                street = "preflop"
                saw_preflop = True
            elif re.fullmatch(r"(?:(?:FIRST|SECOND|THIRD) )?(FLOP|TURN|RIVER)(?: [123])?", label):
                street = label.lower()
                saw_board = True
                committed.clear()
            elif label == "SUMMARY":
                street = "summary"
                saw_summary = True
            elif label in {"SHOWDOWN", "SHOW DOWN"}:
                street = "showdown"
            else:
                street = label.lower()
                money_ok = False
                preflop_ok = preflop_ok and saw_board
            hand.actions.append((street, None, "section", None, raw))
            continue
        if street == "summary":
            parts = raw.split("|")
            if match := TOTAL.fullmatch(parts[0].strip()):
                if pot is not None:
                    payout_ok = False
                pot = amount(match[1])
                for part in parts[1:]:
                    if fee := FEE.fullmatch(part.strip()):
                        fees += amount(fee[1])
                    else:
                        payout_ok = False
            hand.actions.append((street, None, "summary", None, raw))
            continue
        if raw.startswith("Dealt to "):
            token = raw.removeprefix("Dealt to ").partition(" [")[0]
            hand.actions.append((street, token if token in hand.seats else None, "dealt", None, raw))
            continue

        kind, value, alias = "other", None, None
        if match := PAYOUT.fullmatch(raw):
            alias, value, kind = match[1], amount(match[2]), "collect"
        elif match := RETURN.fullmatch(raw):
            alias, value, kind = match[2], amount(match[1]), "return"
        elif ": " in raw:
            alias, action = raw.split(": ", 1)
            action = action.removesuffix(" and is all-in")
            if match := POST.fullmatch(action):
                kind, value = match[1].removeprefix("the "), amount(match[2])
            elif match := WAGER.fullmatch(action):
                kind, value = match[1], amount(match[2])
            elif match := RAISE.fullmatch(action):
                kind, value = "raises", amount(match[1])
            elif action in {"folds", "checks"}:
                kind = action
            elif action.startswith(("shows ", "mucks", "doesn't show")):
                kind = "show"

        hand.actions.append((street, alias if alias in hand.seats else None, kind, value, raw))
        if alias not in hand.seats or kind == "other":
            money_ok = False
            payout_ok = False
            if street in {"setup", "preflop"}:
                preflop_ok = False
            continue

        seat = hand.seats[alias]
        if street == "preflop" and kind in DECISIONS:
            seat.decision = True
            seat.vpip |= kind in {"calls", "bets", "raises"} and bool(value)
            seat.pfr |= kind in {"bets", "raises"}
            owed = bet_level - committed[alias]
            reopened = alias not in acted_at or bet_level - acted_at[alias] >= min_raise
            contestable = any(other != alias and eligible(other)
                              and hand.seats[other].stack - hand.seats[other].paid
                              > bet_level - committed[other] for other in hand.seats)
            chance = raises == 1 and reopened and eligible(alias) and contestable and seat.stack - seat.paid > owed
            seat.three_bet_chance |= chance
            seat.three_bet |= chance and kind in {"bets", "raises"}
            if (not eligible(alias)
                    or (kind == "checks" and owed > 0)
                    or (kind == "calls" and value != min(owed, seat.stack - seat.paid))
                    or (kind == "bets" and bet_level > 0)
                    or (kind == "raises" and (not reopened or value <= bet_level))):
                preflop_ok = False
            acted_at[alias] = value if kind == "raises" else bet_level
        elif street == "setup" and kind in DECISIONS:
            preflop_ok = False

        if kind in {"small blind", "big blind", "calls", "bets", "raises"}:
            delta = wager_delta(kind, value, committed[alias])
            if delta <= 0 or seat.paid + delta > seat.stack:
                money_ok = False
                if street in {"setup", "preflop"}:
                    preflop_ok = False
            committed[alias] += delta
            seat.paid += delta
            if street in {"setup", "preflop"}:
                if kind == "big blind":
                    saw_big_blind = value == hand.big_blind and not saw_big_blind
                if kind in {"bets", "raises"}:
                    raises += 1
                    min_raise = max(min_raise, committed[alias] - bet_level)
                bet_level = max(bet_level, committed[alias])
        elif kind == "ante":
            seat.paid += value
        elif kind == "folds":
            folded.add(alias)
        elif kind == "return":
            if value > seat.paid:
                money_ok = False
            seat.paid -= value
        elif kind == "collect":
            seat.collected += value
        if raw.endswith(" and is all-in"):
            explicitly_all_in.add(alias)

    live = sum(alias not in folded and not seat.sitting_out for alias, seat in hand.seats.items())
    hand.preflop_complete = bool(preflop_ok and saw_preflop and saw_big_blind and saw_summary
                                 and (saw_board or live == 1))
    winners = [alias for alias, seat in hand.seats.items() if seat.collected > 0]
    payouts = sum(seat.collected for seat in hand.seats.values())
    payout_complete = saw_summary and payout_ok and pot is not None and payouts == pot - fees and bool(winners)
    hand.ledger_complete = bool(money_ok and payout_complete and sum(seat.paid for seat in hand.seats.values()) == pot)
    if payout_complete and len(winners) == 1:
        hand.winner = winners[0]
    return hand


def hands_in_zip(path):
    """Yield (archive member, immutable normalized text lines) without extracting files."""
    with ZipFile(path) as archive:
        members = [info for info in archive.infolist() if not info.is_dir() and info.filename.lower().endswith(".txt")]
        if not members:
            raise ValueError(f"{path}: no TXT members")
        if len({info.filename for info in members}) != len(members):
            raise ValueError(f"{path}: duplicate TXT member names")
        for info in members:
            lines = []
            found = False
            with archive.open(info) as raw, TextIOWrapper(raw, encoding="utf-8-sig") as stream:
                for line in stream:
                    line = line.strip()
                    if HEADER.fullmatch(line):
                        if lines:
                            yield info.filename, lines
                        lines = [line]
                        found = True
                    elif re.match(r"Poker Hand #", line, re.I):
                        raise ValueError(f"{path}/{info.filename}: unsupported hand header {line!r}")
                    elif line and lines:
                        lines.append(line)
            if found:
                yield info.filename, lines
            else:
                yield info.filename, None


def rush_hand_details(lines, member):
    if not lines or len(lines) < 2:
        return None
    header = HEADER.fullmatch(lines[0])
    if not header:
        return None
    table = TABLE.fullmatch(lines[1])
    stakes = STAKES.fullmatch(header[2])
    if (not header[1].upper().startswith("RC") or not table or not stakes or
            not re.fullmatch(RUSH_TABLE, table[1]) or table[2] != member[4] or "Tournament #" in header[2]):
        return None
    try:
        small_blind, small_unit = parse_amount(stakes[2])
        big_blind, big_unit = parse_amount(stakes[3])
    except ValueError:
        return None
    if small_unit != big_unit or small_blind != Decimal(member[2]) or big_blind != Decimal(member[3]):
        return None
    return table[1], (stakes[1], big_unit)


def rush_export_scopes(path):
    """Recognize native Rush & Cash members without merging independent exports."""
    scopes = {}
    for filename, group in groupby(hands_in_zip(path), key=itemgetter(0)):
        member = RUSH_MEMBER.fullmatch(PurePosixPath(filename).name)
        if not member:
            continue
        _, first = next(group)
        anchor = rush_hand_details(first, member)
        if not anchor or anchor[0] != member[1]:
            continue
        for _, lines in group:
            details = rush_hand_details(lines, member)
            if not details or details[1] != anchor[1]:
                break
        else:
            # The anchor includes the export's anonymous tokens and Hero's seat, so
            # matching labels alone never join two accounts or two independently named members.
            digest = hashlib.sha256("\n".join([filename, *first]).encode()).hexdigest()
            scopes[filename] = (f"rush:{digest}", member[1], "")
    return scopes
