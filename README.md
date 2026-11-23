# PokerAnalyzer

PokerAnalyzer links PokerCraft winner observations to English GGPoker hand-history exports for offline research. The Chrome extension captures rendered Game History rows. The Python CLI imports CSVs and ZIPs into SQLite, reports identity evidence and conflicts, and exports per-player statistics.

Use synthetic data for the walkthrough below. GGPoker's Security and Ecology Policy restricts third-party import, conversion, and systematic collection. Confirm that your authorization covers the intended PokerCraft access and research use before collecting real data. Keep real exports, captures and databases in a local directory outside this public repository. [GGPoker Security and Ecology Policy, sections 7 and 19](https://legal.ggpoker.com/network/security-ecology-policy/)

## Install and try the CLI

Install Python 3.10 or later. Run these commands from a terminal. The Python standard library supplies everything the CLI uses.

```sh
git clone https://github.com/VietCT04/PokerAnalyzer.git
cd PokerAnalyzer
python -m zipfile -c example.zip examples/table-morning.txt examples/table-evening.txt
python poker_analyzer.py --db example.sqlite import --csv examples/winners.csv --zip example.zip --session-map examples/sessions.json
python poker_analyzer.py --db example.sqlite coverage
python poker_analyzer.py --db example.sqlite stats "Example Alice"
python poker_analyzer.py --db example.sqlite export example-stats.csv
python -m unittest discover -s tests -v
```

The example imports three hands and identifies three of eight appearances. Alice has two observed hands, two preflop decisions, one 3-bet opportunity and net winnings of $7. Casey has one observed hand. Their exports reuse an anonymous token in different sessions. Repeating the import leaves both hand and observation counts unchanged. Choose a fresh output filename each time you export CSV.

## Capture Game History in Chrome

1. Open `chrome://extensions`, turn on **Developer mode**, click **Load unpacked**, and select the checkout's `extension` folder.
2. Open the PokerCraft **Game History** results in a Chrome tab. Copy the origin from the address bar, including `https://` and the hostname.
3. In Chrome DevTools **Elements**, inspect the rendered results. Find a CSS selector for one repeating hand row, and two selectors relative to that row that each match one rendered element. One element must contain the complete hand ID and the other must contain exactly one winner nickname. Inspect several rows, including any multi-winner or empty row, so that the winner selector selects individual nicknames and the extension can skip rows with several matches. The logged-in DOM could not be verified, so the extension ships with blank selectors.
4. Open **PokerCraft Winner Capture** from Chrome's Extensions menu. Enter the origin and the three selectors. Click **Capture rendered rows**, then verify that the usable row count and a sample of exported pairs agree with the page.
5. Move to the next result page, batch, or scroll window and repeat capture after the rows render. The popup shows the total number of unique IDs across captures. **Export CSV** downloads all observations, keeping a row for each distinct hand ID and nickname pair so that the CLI can detect conflicting observations.

Capture runs on demand in the active tab's top-level document. For an embedded results view, inspect which frame owns the rows. Open the Game History document itself in a tab if PokerCraft exposes it as a standalone page. Virtualized lists require capture for each rendered window.

To try the extension on the synthetic fixture, run `python -m http.server 8765 --bind 127.0.0.1 --directory examples`, then open `http://127.0.0.1:8765/game-history.html`. Use origin `http://127.0.0.1:8765`, row selector `tbody > tr`, hand ID selector `.hand-id`, and winner selector `.winner`. The expected count is **2 unique hands**. Capturing twice should leave it at two.

## Import and scope real research data

The ZIP can contain several UTF-8 `.txt` members, optionally with a BOM. The CSV requires `hand_id,winner_nickname` columns. Supply `--csv` and `--zip` together or import them separately in any order. Both options can be repeated. Keep using the same database path as you receive more exports.

```sh
python poker_analyzer.py --db research.sqlite import --csv captures.csv --zip batch1.zip --session accountA-session01
python poker_analyzer.py --db research.sqlite import --zip batch2.zip --session accountA-session01
python poker_analyzer.py --db research.sqlite import --zip batch3.zip --session-map sessions.json
```

Use `--session` when the input contains one independently confirmed anonymous-token scope from one export account. Use `--session-map` for one ZIP with several confirmed scopes. Its keys are exact ZIP member paths and its values are your session labels. Run `python -m zipfile -l batch3.zip` to list the member paths. Use a fresh label for every distinct source account and session. Reuse a label across imports only when the alias namespace is confirmed to be the same. Each label is further scoped by the table name and tournament ID. Leave uncertain members out of the map to use direct, hand-local evidence only. A member containing several uncertain scopes is also best left unscoped.

An exact repeat imported with a confirmed session can upgrade a hand-local scope. The first stored hand is retained when the same ID arrives with different content or a different confirmed session, and the CLI reports the discrepancy. A batch is transactional. SQLite also keeps unknown players' seats, contributions and raw action lines available for later analysis.

The matcher preserves export prefixes. It accepts equivalent decorated IDs such as `Hand ID #HD123`, links the PokerCraft `VH123` variant to `HD123`, and accepts a bare number only while it has a single matching export ID in the database. Use full IDs when available. Only a complete recorded payout with one distinct recipient can establish winner evidence. A token with multiple nicknames, or a nickname with multiple tokens in a confirmed scope, loses all affected mappings. The `mapping_evidence` and `identity_mappings` SQLite views expose the source rows and currently valid mappings. Here, verified means the supplied records agree under these matching rules and the operator's session assignment.

## Read statistics

- **Observed hands** count distinct identified appearances, excluding seats explicitly marked sitting out. Statistics use one row per nickname and currency or chip unit. The overall observed hand count is repeated on each unit row.
- **VPIP and PFR** divide voluntary preflop entries and preflop raises by hands with a recorded preflop decision. Forced blinds and antes do not count as voluntary entries. A forced-only all-in or big-blind walk adds no decision opportunity.
- **3-bet percentage** divides hands with a re-raise after one previous preflop raise by hands with at least one such opportunity. The player must have chips and a legal opportunity to raise against at least one opponent who can contest it. Short all-ins that leave action closed do not create another opportunity.
- **Net winnings** equal collected payouts minus contributions after uncalled returns. They use only hands whose contributions, pot, fees and payouts reconcile. Tournament results are in chips. **bb/100** averages the net result in each qualifying hand's own big blinds and scales it to 100 hands.

Coverage includes counts for identified appearances, recognized winners, usable preflop action, reconciled net and unlinked CSV rows. Empty statistic fields indicate zero qualifying opportunities or records for that metric. The parser targets standard English blinds-based GGPoker histories, including ordinary cash games and tournaments. Unrecognized action or incomplete ledgers can make a hand ineligible for some statistics. The stored raw action text supports inspection of variants such as straddles and special payouts.
