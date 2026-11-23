"""Local PokerCraft winner matching and GGPoker hand-history statistics."""

import argparse
import csv
import hashlib
import json
import sqlite3
import sys
from contextlib import closing
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from zipfile import BadZipFile

from hand_history import ZERO, hands_in_zip, normalize_id, numeric_id, parse_hand


def connect(path):
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))
    return db


def import_csv(db, path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or not {"hand_id", "winner_nickname"} <= set(reader.fieldnames):
            raise ValueError(f"{path}: expected hand_id and winner_nickname CSV columns")
        added = 0
        for row in reader:
            try:
                if None in row or row["winner_nickname"] is None:
                    raise ValueError("wrong number of columns")
                hand_id = normalize_id(row["hand_id"] or "")
                nickname = row["winner_nickname"].strip()
                if not nickname or any(ord(char) < 32 or ord(char) == 127 for char in nickname):
                    raise ValueError("missing or malformed winner nickname")
                db.execute("INSERT OR IGNORE INTO players VALUES (?)", (nickname,))
                added += db.execute("INSERT OR IGNORE INTO winner_observations VALUES (?, ?, ?)",
                                    (hand_id, nickname, path.name)).rowcount
            except ValueError as exc:
                raise ValueError(f"{path}:{reader.line_num}: {exc}") from exc
        return added


def session_id(db, hand, session):
    key = (f"session:{session}" if session and hand.table else f"hand:{hand.id}", hand.table, hand.tournament)
    db.execute("INSERT OR IGNORE INTO sessions (scope, table_name, tournament) VALUES (?, ?, ?)", key)
    return db.execute("SELECT id FROM sessions WHERE (scope, table_name, tournament) = (?, ?, ?)", key).fetchone()[0]


def store_hand(db, hand, session, fingerprint, source):
    previous = db.execute("""
        SELECT h.fingerprint, h.session_id, s.scope
        FROM hands h JOIN sessions s ON s.id = h.session_id WHERE h.id = ?
    """, (hand.id,)).fetchone()
    if previous:
        if previous["fingerprint"] != fingerprint:
            return "content conflict"
        if session and hand.table:
            if previous["scope"].startswith("hand:"):
                new_session = session_id(db, hand, session)
                db.execute("UPDATE hands SET session_id = ? WHERE id = ?", (new_session, hand.id))
                db.execute("DELETE FROM sessions WHERE id = ?", (previous["session_id"],))
                return "scoped"
            if previous["scope"] != f"session:{session}":
                return "session conflict"
        return "duplicate"

    db.execute("INSERT INTO hands VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (
        hand.id, numeric_id(hand.id), session_id(db, hand, session), source, fingerprint, hand.header,
        hand.played_at, hand.max_seats, hand.button, hand.unit,
        str(hand.big_blind) if hand.big_blind is not None else None, hand.winner,
        hand.preflop_complete, hand.ledger_complete,
    ))
    db.executemany("INSERT INTO appearances VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (
        (hand.id, token, seat.number, str(seat.stack), seat.sitting_out, str(seat.paid), str(seat.collected),
         str(seat.collected - seat.paid) if hand.ledger_complete and not seat.sitting_out else None,
         hand.preflop_complete and seat.decision, hand.preflop_complete and seat.vpip,
         hand.preflop_complete and seat.pfr, hand.preflop_complete and seat.three_bet_chance,
         hand.preflop_complete and seat.three_bet)
        for token, seat in hand.seats.items()
    ))
    db.executemany("INSERT INTO actions VALUES (?, ?, ?, ?, ?, ?, ?)", (
        (hand.id, seq, street, token, kind, str(value) if value is not None else None, raw)
        for seq, (street, token, kind, value, raw) in enumerate(hand.actions, 1)
    ))
    return "added"


def import_zip(db, path, session=None, member_sessions=None):
    counts = dict.fromkeys(("added", "duplicate", "scoped", "content conflict", "session conflict", "other TXT"), 0)
    members = set()
    for member, lines in hands_in_zip(path):
        if lines is None:
            counts["other TXT"] += 1
            print(f"{path}/{member}: skipped TXT with no Poker Hand headers", file=sys.stderr)
            continue
        members.add(member)
        hand = parse_hand(lines)
        fingerprint = hashlib.sha256("\n".join([hand.id, hand.header, *lines[1:]]).encode()).hexdigest()
        scope = (member_sessions or {}).get(member, session)
        status = store_hand(db, hand, scope, fingerprint, f"{path.name}/{member}")
        counts[status] += 1
        if status.endswith("conflict") and counts[status] <= 5:
            print(f"{path}/{member}: {hand.id}: {status}; kept the existing hand and session", file=sys.stderr)
    if not members:
        raise ValueError(f"{path}: no supported hand histories")
    if missing := (member_sessions or {}).keys() - members:
        raise ValueError(f"{path}: session map entries with no parsed hands: {', '.join(sorted(missing))}")
    return counts


def coverage_summary(db):
    counts = db.execute("""
        SELECT COUNT(*) AS hands, COUNT(winner_token) AS unique_winner,
               COALESCE(SUM(preflop_complete), 0) AS preflop, COALESCE(SUM(ledger_complete), 0) AS ledgers
        FROM hands
    """).fetchone()
    total, covered = db.execute("""
        SELECT COUNT(*), COUNT(m.nickname)
        FROM appearances a JOIN hands h ON h.id = a.hand_id
        LEFT JOIN identity_mappings m ON m.session_id = h.session_id AND m.token = a.token
        WHERE NOT a.sitting_out
    """).fetchone()
    mapped = db.execute("SELECT COUNT(*) FROM identity_mappings").fetchone()[0]
    direct_hands = db.execute("SELECT COUNT(DISTINCT hand_id) FROM mapping_evidence").fetchone()[0]
    unlinked = db.execute("""
        SELECT COUNT(*) FROM winner_observations o
        WHERE NOT EXISTS (SELECT 1 FROM matched_observations m
                          WHERE (m.observed_id, m.nickname, m.source) = (o.hand_id, o.nickname, o.source))
    """).fetchone()[0]
    return {**dict(counts), "appearances": total, "identified": covered, "mappings": mapped,
            "direct_hands": direct_hands, "unlinked": unlinked}


def print_coverage(db):
    counts = coverage_summary(db)
    total, covered = counts["appearances"], counts["identified"]
    print(f"Hands: {counts['hands']} | unique recorded winner: {counts['unique_winner']} | "
          f"usable preflop: {counts['preflop']} | reconciled net: {counts['ledgers']}")
    print(f"Identified appearances: {covered}/{total} ({100 * covered / total if total else 0:.2f}%) | "
          f"verified scoped mappings: {counts['mappings']} | "
          f"evidence hands (including conflicts): {counts['direct_hands']} | unlinked CSV rows: {counts['unlinked']}")

    conflicts = db.execute("""
        SELECT s.scope, s.table_name, s.tournament, e.token, e.nickname, e.hand_id, e.source
        FROM mapping_evidence e JOIN sessions s ON s.id = e.session_id
        WHERE NOT EXISTS (SELECT 1 FROM identity_mappings m
                          WHERE (m.session_id, m.token, m.nickname) = (e.session_id, e.token, e.nickname))
        ORDER BY s.scope, s.table_name, e.token, e.nickname, e.hand_id, e.source
    """)
    any_conflicts = False
    for conflict in conflicts:
        any_conflicts = True
        print("Mapping conflict:", json.dumps(dict(conflict), ensure_ascii=False))
    for conflict in db.execute("""
        SELECT h.numeric_id, GROUP_CONCAT(h.id) AS matching_hands
        FROM hands h WHERE h.numeric_id IN (SELECT hand_id FROM winner_observations)
        GROUP BY h.numeric_id HAVING COUNT(*) > 1
        ORDER BY h.numeric_id
    """):
        any_conflicts = True
        print("Ambiguous numeric ID:", json.dumps(dict(conflict)))
    if not any_conflicts:
        print("Mapping conflicts: 0")


def ratio(numerator, denominator):
    return f"{100 * Decimal(numerator) / denominator:.2f}" if denominator else ""


@dataclass
class Stats:
    hands: int = 0
    preflop: int = 0
    vpip: int = 0
    pfr: int = 0
    three_bet: int = 0
    three_bet_opportunities: int = 0
    net_hands: int = 0
    net: Decimal = ZERO
    bb_hands: int = 0
    net_bb: Decimal = ZERO

    def add(self, appearance):
        self.hands += 1
        self.preflop += appearance["decision"]
        self.vpip += appearance["vpip"]
        self.pfr += appearance["pfr"]
        self.three_bet += appearance["three_bet"]
        self.three_bet_opportunities += appearance["three_bet_chance"]
        if appearance["net"] is not None:
            net = Decimal(appearance["net"])
            self.net_hands += 1
            self.net += net
            if appearance["big_blind"] and (blind := Decimal(appearance["big_blind"])) > 0:
                self.bb_hands += 1
                self.net_bb += net / blind

    def row(self, nickname, unit, total_hands):
        return (nickname, unit or "unknown", total_hands, self.hands, self.preflop, ratio(self.vpip, self.preflop),
                ratio(self.pfr, self.preflop), self.three_bet_opportunities,
                ratio(self.three_bet, self.three_bet_opportunities), self.net_hands,
                format(self.net, "f") if self.net_hands else "", self.bb_hands, ratio(self.net_bb, self.bb_hands))


STATS_COLUMNS = (
    "nickname", "unit", "total_observed_hands", "observed_hands_in_unit", "preflop_opportunities",
    "vpip_pct", "pfr_pct", "three_bet_opportunities", "three_bet_pct", "net_hands", "net_winnings",
    "bb_hands", "bb_per_100",
)


def statistics(db, nickname=None):
    groups = {}
    totals = {}
    query = """
        SELECT m.nickname, h.unit, h.big_blind, a.*
        FROM identity_mappings m
        JOIN hands h ON h.session_id = m.session_id
        JOIN appearances a ON (a.hand_id, a.token) = (h.id, m.token)
        WHERE NOT a.sitting_out
    """
    params = ()
    if nickname is not None:
        query += " AND m.nickname = ?"
        params = (nickname,)
    for row in db.execute(query, params):
        name, unit = row["nickname"], row["unit"]
        key = (name, unit)
        if key not in groups:
            groups[key] = Stats()
        groups[key].add(row)
        totals[name] = totals.get(name, 0) + 1
    return [stats.row(name, unit, totals[name]) for (name, unit), stats in sorted(groups.items())]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("poker.sqlite"), help="SQLite file (default: poker.sqlite)")
    commands = parser.add_subparsers(dest="command", required=True)
    load = commands.add_parser("import", help="add winner CSV and/or hand-history ZIP files")
    load.add_argument("--csv", type=Path, action="append", default=[])
    load.add_argument("--zip", type=Path, action="append", default=[])
    scopes = load.add_mutually_exclusive_group()
    scopes.add_argument("--session", help="verified session label for all supplied ZIPs")
    scopes.add_argument("--session-map", type=Path, help="JSON object: TXT member path to verified session label")
    commands.add_parser("coverage", help="print mapping coverage and conflict evidence")
    stats = commands.add_parser("stats", help="query one verified nickname")
    stats.add_argument("nickname")
    export = commands.add_parser("export", help="write verified statistics to a new CSV file")
    export.add_argument("output", type=Path)

    args = parser.parse_args(argv)
    try:
        with closing(connect(args.db)) as db:
            if args.command == "import":
                if not (args.csv or args.zip):
                    parser.error("import needs --csv and/or --zip")
                member_sessions = None
                if args.session_map:
                    if len(args.zip) != 1:
                        parser.error("--session-map requires exactly one --zip")
                    member_sessions = json.loads(args.session_map.read_text(encoding="utf-8-sig"))
                    if not isinstance(member_sessions, dict) or not all(
                        isinstance(key, str) and isinstance(value, str) and value.strip()
                        for key, value in member_sessions.items()
                    ):
                        raise ValueError("session map must be a JSON object with non-empty session labels")
                    member_sessions = {key: value.strip() for key, value in member_sessions.items()}
                session = args.session.strip() if args.session else None
                if args.session is not None and (not args.zip or not session):
                    parser.error("--session requires a non-empty label and at least one --zip")
                messages = []
                with db:
                    db.execute("BEGIN IMMEDIATE")
                    for path in args.csv:
                        messages.append(f"{path}: {import_csv(db, path)} new winner observations")
                    for path in args.zip:
                        counts = import_zip(db, path, session, member_sessions)
                        messages.append(f"{path}: " + ", ".join(f"{n} {key}" for key, n in counts.items()))
                print("\n".join(messages))
                print_coverage(db)
            elif args.command == "coverage":
                print_coverage(db)
            elif args.command == "stats":
                rows = statistics(db, args.nickname)
                if not rows:
                    print(f"No verified appearances for {args.nickname!r}.", file=sys.stderr)
                    return 1
                for row in rows:
                    print(json.dumps(dict(zip(STATS_COLUMNS, row)), ensure_ascii=False, indent=2))
            else:
                with args.output.open("x", encoding="utf-8-sig", newline="") as stream:
                    writer = csv.writer(stream)
                    writer.writerow(STATS_COLUMNS)
                    writer.writerows(statistics(db))
                print(f"Exported {args.output}")
    except (OSError, ValueError, BadZipFile, sqlite3.Error, csv.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
