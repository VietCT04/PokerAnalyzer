CREATE INDEX IF NOT EXISTS hands_account_numeric_id ON hands (source_account, numeric_id);

CREATE VIEW matched_observations AS
SELECT h.id AS hand_id, o.hand_id AS observed_id, o.nickname, o.source, o.source_account
FROM winner_observations o JOIN hands h ON (h.source_account, h.id) = (o.source_account, o.hand_id)
WHERE o.hand_id GLOB '[A-Z]*' AND o.hand_id NOT GLOB 'VH[0-9]*'
UNION ALL
SELECT h.id, o.hand_id, o.nickname, o.source, o.source_account
FROM winner_observations o JOIN hands h ON (h.source_account, h.id) = (o.source_account, 'HD' || SUBSTR(o.hand_id, 3))
WHERE o.hand_id GLOB 'VH[0-9]*'
UNION ALL
SELECT h.id, o.hand_id, o.nickname, o.source, o.source_account
FROM winner_observations o JOIN hands h ON (h.source_account, h.numeric_id) = (o.source_account, o.hand_id)
WHERE o.hand_id GLOB '[0-9]*' AND
      (SELECT COUNT(*) FROM hands x WHERE (x.source_account, x.numeric_id) = (o.source_account, o.hand_id)) = 1;

CREATE VIEW mapping_evidence AS
SELECT h.session_id, h.winner_token AS token, m.nickname, h.id AS hand_id, m.observed_id, m.source,
       m.source_account, h.played_at AS evidence_timestamp
FROM matched_observations m JOIN hands h ON h.id = m.hand_id
WHERE h.winner_token IS NOT NULL;

CREATE VIEW mapping_candidates AS
SELECT session_id, token, nickname, COUNT(DISTINCT hand_id) AS evidence_hands, source_account
FROM mapping_evidence
GROUP BY source_account, session_id, token, nickname;

CREATE VIEW identity_mappings AS
SELECT h.session_id, r.token, r.nickname, COUNT(*) AS evidence_hands, h.source_account
FROM appearance_identities r JOIN hands h ON h.id = r.hand_id
WHERE r.method = 'direct'
GROUP BY h.source_account, h.session_id, r.token, r.nickname;

CREATE VIEW ambiguous_numeric_ids AS
SELECT h.source_account, h.numeric_id, GROUP_CONCAT(h.id) AS matching_hands
FROM hands h WHERE EXISTS (SELECT 1 FROM winner_observations o
                           WHERE (o.source_account, o.hand_id) = (h.source_account, h.numeric_id))
GROUP BY h.source_account, h.numeric_id HAVING COUNT(*) > 1;
