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
- **New PokerCraft batch**. Reload the unpacked extension, select the sessions in Rush & Cash, open Game History, and start on page 1. Click **Download & import** once and keep the popup open while it captures pages. The extension requests PokerCraft’s ZIP download and switches to the dashboard after a matching ZIP arrives. The importer waits up to three minutes and recognizes a new ZIP that contains a captured hand ID. If Chrome shows no ZIP download in progress, click **Download** on PokerCraft while the dashboard waits. After a timeout, use **Choose files** to import the ZIP. Saved winner records are matched automatically.
- **Statistics**. Click **Sync & analyze** after importing to refresh from SQLite and see new hands, added and removed mappings, and identification via each session. The first run covers all saved hands. Later runs compare against the last analysis, even after restarting the dashboard. Filter by nickname, sort columns, and export the CSV.

For recognized native Rush & Cash exports, each TXT member defines its own session. Winner evidence can identify the same token in other hands in that session, including hands the player lost. The dashboard labels those assignments **inferred**. Reimport the original ZIP to apply this to hands already stored with a hand-local scope. Other formats use direct winner evidence until you supply a session with the CLI.

## Raw archive and recovery

The dashboard and CLI keep the original imported ZIPs and CSVs in a sibling archive. The default is `research-data/poker.raw/`. Its `blobs/` folder holds the original bytes, stored once per SHA-256 checksum. The numbered `batches/` manifests record original filenames, import order, source account labels and explicit session options. The dashboard archives a one-click winner capture as CSV before waiting for the ZIP, so you can finish an interrupted import later with just the ZIP.

Keep both archive folders together and back them up. To rebuild a new SQLite database from the archive, run:

```sh
python poker_analyzer.py --db research-data/recovered.sqlite rebuild --raw research-data/poker.raw
python dashboard.py --db research-data/recovered.sqlite --open
```

Rebuild checks file hashes and replays the batches in order. If you continue importing into the new database, keep its new `.raw` folder with the original. For a later rebuild, pass `--raw` once for each archive, oldest first. Invalid imports leave their raw files and a `.failed.json` manifest for inspection. Rebuild skips those failed manifests. Reimport any pre-archive ZIPs and CSVs you still have to preserve them in the raw archive.

## Offline preflop video pilot

Use a recording after play ends. Install [Tesseract with English data](https://tesseract-ocr.github.io/tessdoc/Installation.html) and make its executable available on PATH or pass `--tesseract PATH`. These dependencies are only for video extraction. FFmpeg is supplied by `imageio-ffmpeg`.

```powershell
python -m pip install -r requirements-video.txt
python video_preflop.py "G:\vidPoker\recording.mp4" --start 45 --review-frames --output research-data/video-pilot
```

Use a new output directory per run. Set `--start 0` if play is visible from the beginning. The bundled layout is calibrated to one 1280 × 720 three-table, six-max Rush & Cash layout with four-color cards, stacks in BB, and a 0.5/1 BB blind structure. For a different recording layout, adjust a copy of `examples/rush-video-layout.json` against a preflop screenshot and pass `--layout PATH`.

The pilot samples four frames per second, groups nearby frames with the same visible hole cards on the same table, and selects a candidate with an empty board and a pot OCR reading of 1.5 BB. This screen evidence may omit hands. Open `review.html` in your browser after the run. Search by nickname or timestamp, filter by table, and review the seats, stacks, and hole cards together. With `--review-frames`, select a nickname or **Show table frame** to see the source crop. Keep the `frames/` folder next to the report so the image links work. The output also includes structured seat observations in `preflop.csv` and processing metrics in `benchmark.json`.

For clearer nicknames, add the optional CPU recognizer. Download RapidOCR's English PP-OCRv5 mobile recognition model once, then pass its local file with `--nickname-model`. Tesseract still reads pots, stacks and card ranks. [RapidOCR model registry](https://github.com/RapidAI/RapidOCR/blob/main/python/rapidocr/default_models.yaml)

```powershell
python -m pip install onnxruntime
New-Item -ItemType Directory -Force research-data/models
Invoke-WebRequest -Uri "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.10.0/onnx/PP-OCRv5/rec/en_PP-OCRv5_rec_mobile.onnx" -OutFile research-data/models/en_PP-OCRv5_rec_mobile.onnx
Get-FileHash research-data/models/en_PP-OCRv5_rec_mobile.onnx
python video_preflop.py "G:\vidPoker\recording.mp4" --start 45 --nickname-model research-data/models/en_PP-OCRv5_rec_mobile.onnx --review-frames --output research-data/video-pilot-enhanced
```

The model's published SHA-256 is `c3461add59bb4323ecba96a492ab75e06dda42467c9e3d0c18db5d1d21924be8`. With the model enabled, the pilot checks later preflop frames with matching visible hole cards and compatible dealer positions. This can recover a name after a blind-posting message clears. For an unreadable name, it searches a wider strip around the label and requires the wider crop and the detected text bounds to agree. Each candidate includes its source timestamp and how many frames supported it. Blank candidates flag weak or conflicting reads. Repeated reads and high model scores can still share the same OCR error.

The report keeps the displayed stack separate from the estimated beginning stack. Four-digit integer readings receive an extra check when the optional model is available, since a missed decimal can make the stack look ten times larger. The report marks unresolved readings for review. Where the displayed blinds or visible dealer button support the interpretation, the estimator adds back the posted blind. It leaves an empty estimate if it cannot resolve the blind amounts or if the OCR and dealer evidence disagree. The recorded player's hole-card pair belongs to the bottom seat, with rank read by OCR and suit from the four-color deck. Keep the source video, report, CSV and review crops private. The pilot writes local outputs independently of PokerAnalyzer's hand histories and identity database.

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

Use `--account NAME` on CLI imports or when starting the dashboard to designate the source PokerCraft account. Use the same label for that account's CSVs and ZIPs. The default label is `local`, including records from older databases and raw archives. A duplicate hand ID from a different account keeps the previously stored hand and preserves the new ZIP in the raw archive.

The matcher preserves export prefixes. It accepts equivalent decorated IDs such as `Hand ID #HD123`, links the PokerCraft `VH123` variant to `HD123`, and accepts a bare number only while it matches one export ID for that account. Only a complete recorded payout with one distinct recipient can establish winner evidence.

Direct evidence for the appearance takes priority. For other appearances the resolver uses the closest dated evidence for the token within the source account and session, allowing both multiple tokens per nickname and nickname changes over time. Disagreeing direct evidence or equidistant evidence with different nicknames leaves the appearance unresolved. `mapping_evidence` exposes the original observations and their hand timestamps, and `appearance_identities` records the resolution method and evidence hand ID. `identity_mappings` lists pairs backed by agreeing direct evidence.

Player statistics count a nickname once per hand. If several seats resolve to one nickname, the direct seat supplies the metrics when present. If all those seats are inferred, the hand counts once and its action and monetary metrics are omitted for that nickname.

## Read statistics

- **Observed hands** count distinct identified appearances, excluding seats explicitly marked sitting out. Statistics use one row per nickname and currency or chip unit. The overall observed hand count is repeated on each unit row.
- **VPIP and PFR** divide voluntary preflop entries and preflop raises by hands with a recorded preflop decision. Forced blinds and antes do not count as voluntary entries. A forced-only all-in or big-blind walk adds no decision opportunity.
- **3-bet percentage** divides hands with a re-raise after one previous preflop raise by hands with at least one such opportunity. The player must have chips and a legal opportunity to raise against at least one opponent who can contest it. Short all-ins that leave action closed do not create another opportunity.
- **Net winnings** equal collected payouts minus contributions after uncalled returns. They use only hands whose contributions, pot, fees and payouts reconcile. Tournament results are in chips. **bb/100** averages the net result in each qualifying hand's own big blinds and scales it to 100 hands.

Coverage includes counts for identified appearances, recognized winners, usable preflop action, reconciled net and unlinked CSV rows. Empty statistic fields indicate zero qualifying opportunities or records for that metric. The parser targets standard English blinds-based GGPoker histories, including ordinary cash games and tournaments. Unrecognized action or incomplete ledgers can make a hand ineligible for some statistics. The stored raw action text supports inspection of variants such as straddles and special payouts.
