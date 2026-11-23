CREATE TABLE IF NOT EXISTS players (
    nickname TEXT PRIMARY KEY
) WITHOUT ROWID;

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
    ledger_complete INTEGER NOT NULL
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
    PRIMARY KEY (hand_id, nickname, source)
) WITHOUT ROWID;

-- Bare numeric IDs can only be linked while exactly one stored hand has those digits.
CREATE VIEW IF NOT EXISTS matched_observations AS
SELECT h.id AS hand_id, o.hand_id AS observed_id, o.nickname, o.source
FROM winner_observations o JOIN hands h ON h.id = o.hand_id
WHERE o.hand_id GLOB '[A-Z]*' AND o.hand_id NOT GLOB 'VH[0-9]*'
UNION ALL
SELECT h.id, o.hand_id, o.nickname, o.source
FROM winner_observations o JOIN hands h ON h.id = 'HD' || SUBSTR(o.hand_id, 3)
WHERE o.hand_id GLOB 'VH[0-9]*'
UNION ALL
SELECT h.id, o.hand_id, o.nickname, o.source
FROM winner_observations o JOIN hands h ON h.numeric_id = o.hand_id
WHERE o.hand_id GLOB '[0-9]*' AND
      (SELECT COUNT(*) FROM hands x WHERE x.numeric_id = o.hand_id) = 1;

CREATE VIEW IF NOT EXISTS mapping_evidence AS
SELECT h.session_id, h.winner_token AS token, m.nickname,
       h.id AS hand_id, m.observed_id, m.source
FROM matched_observations m JOIN hands h ON h.id = m.hand_id
WHERE h.winner_token IS NOT NULL;

CREATE VIEW IF NOT EXISTS mapping_candidates AS
SELECT session_id, token, nickname, COUNT(DISTINCT hand_id) AS evidence_hands
FROM mapping_evidence
GROUP BY session_id, token, nickname;

-- A session mapping must be one-to-one in both directions.
CREATE VIEW IF NOT EXISTS identity_mappings AS
WITH candidates AS (SELECT * FROM mapping_candidates)
SELECT c.* FROM candidates c
JOIN (SELECT session_id, token FROM candidates GROUP BY session_id, token HAVING COUNT(*) = 1)
  USING (session_id, token)
JOIN (SELECT session_id, nickname FROM candidates GROUP BY session_id, nickname HAVING COUNT(*) = 1)
  USING (session_id, nickname);
