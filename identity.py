"""Resolve an appearance using direct evidence or the nearest evidence timestamp."""

from bisect import bisect_left
from collections import defaultdict
from datetime import datetime, timezone


DEFAULT_ACCOUNT = "local"
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def account_label(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise ValueError("Source account must be a nonempty label of up to 128 characters")
    return value.strip()


def invalidate_identities(db):
    db.execute("DELETE FROM identity_resolution_state")


def nearest_evidence(timestamps, evidence, timestamp):
    index = bisect_left(timestamps, timestamp)
    nearby = timestamps[max(0, index - 1):index + 1]
    distance = min(abs(time - timestamp) for time in nearby)
    names = {}
    for time in nearby:
        if abs(time - timestamp) == distance:
            for nickname, hand_id in evidence[time].items():
                names.setdefault(nickname, hand_id)
    return names


def timestamp_value(value):
    if len(value) < 16 or value[10] not in " T":
        return None
    value = value.replace("/", "-")
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    since_epoch = parsed - EPOCH
    return (since_epoch.days * 86400 + since_epoch.seconds) * 1_000_000 + since_epoch.microseconds


def resolved_rows(db):
    hand_times = {row["id"]: timestamp_value(row["played_at"]) for row in db.execute("SELECT id, played_at FROM hands")}
    direct = defaultdict(set)
    evidence = defaultdict(lambda: defaultdict(dict))
    for row in db.execute("""
        SELECT * FROM mapping_evidence ORDER BY hand_id
    """):
        direct[row["hand_id"], row["token"]].add(row["nickname"])
        timestamp = hand_times[row["hand_id"]]
        if timestamp is not None:
            scope = (row["source_account"], row["session_id"], row["token"])
            evidence[scope][timestamp].setdefault(row["nickname"], row["hand_id"])
    timestamps = {scope: sorted(positions) for scope, positions in evidence.items()}

    for row in db.execute("""
        SELECT a.hand_id, a.token, h.source_account, h.session_id
        FROM appearances a JOIN hands h ON h.id = a.hand_id
    """):
        key = (row["hand_id"], row["token"])
        if names := direct.get(key):
            if len(names) == 1:
                yield (*key, next(iter(names)), "direct", row["hand_id"])
            else:
                yield (*key, None, "direct_conflict", None)
            continue
        scope = (row["source_account"], row["session_id"], row["token"])
        timestamp = hand_times[row["hand_id"]]
        if timestamp is None or scope not in evidence:
            continue
        closest = nearest_evidence(timestamps[scope], evidence[scope], timestamp)
        if len(closest) == 1:
            nickname, hand_id = next(iter(closest.items()))
            yield (*key, nickname, "inferred", hand_id)
        else:
            yield (*key, None, "timestamp_tie", None)


def ensure_identities(db):
    if db.execute("SELECT 1 FROM identity_resolution_state").fetchone():
        return
    if not db.in_transaction:
        with db:
            db.execute("BEGIN IMMEDIATE")
            ensure_identities(db)
        return
    db.execute("DELETE FROM appearance_identities")
    db.executemany("INSERT INTO appearance_identities VALUES (?, ?, ?, ?, ?)", resolved_rows(db))
    db.execute("INSERT INTO identity_resolution_state VALUES (1)")
