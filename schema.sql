CREATE TABLE IF NOT EXISTS players (
    nickname TEXT PRIMARY KEY
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS dashboard_analysis (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    summary TEXT NOT NULL,
    mapping_keys TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY,
    scope TEXT NOT NULL,
    table_name TEXT NOT NULL,
    tournament TEXT NOT NULL,
    UNIQUE (scope, table_name, tournament)
);

CREATE TABLE IF NOT EXISTS hands (
    id TEXT PRIMARY KEY,
    numeric_id TEXT NOT NULL,
    session_id INTEGER NOT NULL REFERENCES sessions,
    source TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    header TEXT NOT NULL,
    played_at TEXT NOT NULL,
    max_seats INTEGER,
    button INTEGER,
    unit TEXT NOT NULL,
    big_blind TEXT,
    winner_token TEXT,
    preflop_complete INTEGER NOT NULL,
    ledger_complete INTEGER NOT NULL,
    table_name TEXT NOT NULL DEFAULT '',
    source_account TEXT NOT NULL DEFAULT 'local'
);
CREATE INDEX IF NOT EXISTS hands_numeric_id ON hands (numeric_id);
CREATE INDEX IF NOT EXISTS hands_session ON hands (session_id);

CREATE TABLE IF NOT EXISTS appearances (
    hand_id TEXT NOT NULL REFERENCES hands,
    token TEXT NOT NULL,
    seat INTEGER NOT NULL,
    stack TEXT NOT NULL,
    sitting_out INTEGER NOT NULL,
    contributed TEXT NOT NULL,
    collected TEXT NOT NULL,
    net TEXT,
    decision INTEGER NOT NULL,
    vpip INTEGER NOT NULL,
    pfr INTEGER NOT NULL,
    three_bet_chance INTEGER NOT NULL,
    three_bet INTEGER NOT NULL,
    PRIMARY KEY (hand_id, token),
    UNIQUE (hand_id, seat)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS actions (
    hand_id TEXT NOT NULL REFERENCES hands,
    seq INTEGER NOT NULL,
    street TEXT NOT NULL,
    token TEXT,
    kind TEXT NOT NULL,
    amount TEXT,
    raw TEXT NOT NULL,
    PRIMARY KEY (hand_id, seq),
    FOREIGN KEY (hand_id, token) REFERENCES appearances
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS winner_observations (
    hand_id TEXT NOT NULL,
    nickname TEXT NOT NULL REFERENCES players,
    source TEXT NOT NULL,
    source_account TEXT NOT NULL DEFAULT 'local',
    PRIMARY KEY (hand_id, nickname, source, source_account)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS appearance_identities (
    hand_id TEXT NOT NULL,
    token TEXT NOT NULL,
    nickname TEXT REFERENCES players,
    method TEXT NOT NULL CHECK (method IN ('direct', 'inferred', 'direct_conflict', 'timestamp_tie')),
    evidence_hand_id TEXT REFERENCES hands,
    PRIMARY KEY (hand_id, token),
    FOREIGN KEY (hand_id, token) REFERENCES appearances,
    CHECK ((method IN ('direct', 'inferred') AND nickname IS NOT NULL AND evidence_hand_id IS NOT NULL)
        OR (method IN ('direct_conflict', 'timestamp_tie') AND nickname IS NULL AND evidence_hand_id IS NULL))
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS appearance_identities_nickname ON appearance_identities (nickname);

CREATE TABLE IF NOT EXISTS identity_resolution_state (
    id INTEGER PRIMARY KEY CHECK (id = 1)
);
