# PokerAnalyzer

PokerAnalyzer links PokerCraft winner observations to English GGPoker hand-history exports for offline research. The Chrome extension captures rendered Game History rows. The local dashboard and Python CLI import CSVs and ZIPs into SQLite and report identity coverage and player statistics.

Use synthetic data for the walkthrough below. GGPoker's Security and Ecology Policy restricts third-party import, conversion, and systematic collection. Confirm that your authorization covers the intended PokerCraft access and research use before collecting real data. Keep real exports, captures and databases in a local directory outside this public repository. [GGPoker Security and Ecology Policy, sections 7 and 19](https://legal.ggpoker.com/network/security-ecology-policy/)

## Local dashboard

Install Python 3.10 or later. From the repository, start the dashboard and leave this terminal running.

```sh
python dashboard.py --open
```

The dashboard opens at `http://127.0.0.1:8766/`. It uses `research-data/poker.sqlite` by default. Use `--db PATH` to point it at an existing CLI database, and `--downloads PATH` if Chrome saves PokerCraft ZIPs outside your Downloads folder.

- **Existing downloads**. Choose the winner CSV and any number of hand-history ZIPs in the same file picker, then click **Import files**. You can also import the CSV and ZIP files in separate visits.
- **New PokerCraft batch**. Reload the unpacked extension, select the sessions in Rush & Cash, open Game History, and start on page 1. Click **Download & import** once and keep the popup open while it captures pages. The extension triggers PokerCraft’s original ZIP download and opens the dashboard. The local importer waits up to three minutes for a newly saved ZIP that contains a hand ID from this capture. If Chrome prompts for a save location, choose the watched folder. One click handles one native ZIP export.
- **Statistics**. Filter by nickname, sort columns, and export the CSV. Identity coverage counts appearances. The table displays per-unit stats and the number of opportunities behind each rate.

For recognized native Rush & Cash exports, each TXT member defines its own session. A verified winner mapping also identifies that token in other hands in the same member, including hands the player lost, even when the table changes. Reimport the original ZIP to apply this to hands already stored with a hand-local scope. Other formats use direct winner evidence until you supply a session with the CLI.

## Raw archive and recovery

The dashboard and CLI keep the original imported ZIPs and CSVs in a sibling archive. The default is `research-data/poker.raw/`. Its `blobs/` folder holds the original bytes, stored once per SHA-256 checksum. The numbered `batches/` manifests record original filenames, import order, and explicit session options. The dashboard archives a one-click winner capture as CSV before waiting for the ZIP, so you can finish an interrupted import later with just the ZIP.

Keep both archive folders together and back them up. To rebuild a new SQLite database from the archive, run:

```sh
python poker_analyzer.py --db research-data/recovered.sqlite rebuild --raw research-data/poker.raw
python dashboard.py --db research-data/recovered.sqlite --open
```

Rebuild checks file hashes and replays the batches in order. If you continue importing into the new database, keep its new `.raw` folder with the original. For a later rebuild, pass `--raw` once for each archive, oldest first. Invalid imports leave their raw files and a `.failed.json` manifest for inspection. Rebuild skips those failed manifests. Reimport any pre-archive ZIPs and CSVs you still have to preserve them in the raw archive.

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
2. Open English **Rush & Cash** on `my.pokercraft.com` and select the sessions you want to inspect. Click **Game History** in PokerCraft or **Open Game History** in the extension.
3. Once hands are visible, open the extension and click **Capture & download CSV**. Keep the popup open while it follows the native Next button from the current page. Start on page 1 to capture from the beginning. If a page fails to render, the popup reports the stop and saves the observations already collected.
4. **Download hand histories** triggers PokerCraft’s own enabled Download control for its original export. Use the CSV and ZIP as separate inputs to the Python CLI.

The popup shows the number of unique hand IDs saved across captures. Each CSV contains the accumulated observations, preserving distinct nicknames for the same hand so the CLI can flag conflicts. **Download saved CSV** exports the same data without reading the page again.

To try the synthetic fixture, run `python -m http.server 8765 --bind 127.0.0.1 --directory examples`, open `http://127.0.0.1:8765/game-history.html`, and click **Capture & download CSV**. A fresh extension should save **2 unique hands**. Capturing twice should leave that count at two.

## Import and scope real research data

The ZIP can contain several UTF-8 `.txt` members, optionally with a BOM. The CSV requires `hand_id,winner_nickname` columns. Supply `--csv` and `--zip` together or import them separately in any order. Both options can be repeated. Keep using the same database path as you receive more exports.

```sh
python poker_analyzer.py --db research.sqlite import --csv captures.csv --zip batch1.zip --session accountA-session01
python poker_analyzer.py --db research.sqlite import --zip batch2.zip --session accountA-session01
python poker_analyzer.py --db research.sqlite import --zip batch3.zip --session-map sessions.json
```

Recognized native Rush & Cash members are scoped automatically when you omit session flags. The matcher checks the member name, its first hand’s table, and the game, stake and format of all hands before grouping. An identical export can be reimported under a different ZIP name. Separate members and batches with a different export anchor keep separate scopes.

Use `--session` when the input contains one independently confirmed anonymous-token scope from one export account. Use `--session-map` for one ZIP with several confirmed scopes. Its keys are exact ZIP member paths and its values are your session labels. Run `python -m zipfile -l batch3.zip` to list the member paths. Use a fresh label for every distinct source account and session. Reuse a label across imports only when the alias namespace is confirmed to be the same. Each explicit label is further scoped by the table name and tournament ID. Leave uncertain members out of the map to use direct, hand-local evidence only. A member containing several uncertain scopes is also best left unscoped.

An exact repeat imported with a confirmed session can upgrade a hand-local scope. The first stored hand is retained when the same ID arrives with different content or a different confirmed session, and the CLI reports the discrepancy. A batch is transactional. SQLite also keeps unknown players' seats, contributions and raw action lines available for later analysis.

The matcher preserves export prefixes. It accepts equivalent decorated IDs such as `Hand ID #HD123`, links the PokerCraft `VH123` variant to `HD123`, and accepts a bare number only while it has a single matching export ID in the database. Use full IDs when available. Only a complete recorded payout with one distinct recipient can establish winner evidence. A token with multiple nicknames, or a nickname with multiple tokens in a confirmed scope, loses all affected mappings. The `mapping_evidence` and `identity_mappings` SQLite views expose the source rows and currently valid mappings. Here, verified means the supplied records agree under these matching rules and the operator's session assignment.

## Read statistics

- **Observed hands** count distinct identified appearances, excluding seats explicitly marked sitting out. Statistics use one row per nickname and currency or chip unit. The overall observed hand count is repeated on each unit row.
- **VPIP and PFR** divide voluntary preflop entries and preflop raises by hands with a recorded preflop decision. Forced blinds and antes do not count as voluntary entries. A forced-only all-in or big-blind walk adds no decision opportunity.
- **3-bet percentage** divides hands with a re-raise after one previous preflop raise by hands with at least one such opportunity. The player must have chips and a legal opportunity to raise against at least one opponent who can contest it. Short all-ins that leave action closed do not create another opportunity.
- **Net winnings** equal collected payouts minus contributions after uncalled returns. They use only hands whose contributions, pot, fees and payouts reconcile. Tournament results are in chips. **bb/100** averages the net result in each qualifying hand's own big blinds and scales it to 100 hands.

Coverage includes counts for identified appearances, recognized winners, usable preflop action, reconciled net and unlinked CSV rows. Empty statistic fields indicate zero qualifying opportunities or records for that metric. The parser targets standard English blinds-based GGPoker histories, including ordinary cash games and tournaments. Unrecognized action or incomplete ledgers can make a hand ineligible for some statistics. The stored raw action text supports inspection of variants such as straddles and special payouts.
