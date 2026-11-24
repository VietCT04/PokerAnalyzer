"""Build a local, hand-by-hand review page for the video extractor."""

from collections import defaultdict
from html import escape
from string import Template
from urllib.parse import quote


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Preflop review · PokerAnalyzer</title>
<style>
  :root {
    color-scheme: light;
    --paper: #f4f6f4;
    --panel: #fff;
    --ink: #182622;
    --muted: #52665f;
    --line: #dbe4df;
    --green: #0a604f;
    --pale: #e3f2eb;
    --amber: #85531b;
    font: 15px/1.45 system-ui, "Segoe UI", sans-serif;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--paper); color: var(--ink); }
  [hidden] { display: none !important; }
  a { color: var(--green); }
  .page { max-width: 1440px; margin: auto; padding: 32px clamp(16px, 3vw, 44px) 52px; }
  .intro { display: flex; align-items: end; justify-content: space-between; gap: 22px; margin-bottom: 24px; }
  .project { color: var(--green); font-size: .92rem; font-weight: 700; }
  h1 { font-size: clamp(2rem, 4vw, 3rem); line-height: 1.05; margin: 5px 0 8px; letter-spacing: -.045em; }
  .source { color: var(--muted); margin: 0; overflow-wrap: anywhere; }
  .metrics { display: flex; flex-wrap: wrap; gap: 10px; margin: 0; }
  .metrics div { min-width: 125px; padding: 10px 14px; border: 1px solid var(--line); background: var(--panel); border-radius: 8px; }
  .metrics dt { color: var(--muted); font-size: .84rem; }
  .metrics dd { margin: 0; font-size: 1.44rem; font-weight: 720; font-variant-numeric: tabular-nums; }
  .tools {
    display: grid; grid-template-columns: minmax(160px, 1fr) minmax(125px, 180px) minmax(149px, 190px) auto;
    gap: 12px; align-items: end; padding: 12px; background: var(--panel); border: 1px solid var(--line);
    border-radius: 10px; margin-bottom: 18px;
  }
  label { font-weight: 650; font-size: .83rem; display: grid; gap: 4px; }
  input, select {
    font: inherit; font-size: .95rem; width: 100%; min-height: 38px; background: var(--paper); color: var(--ink);
    border: 1px solid var(--line); border-radius: 6px; padding: 6px 9px;
  }
  :focus-visible { outline: 2px solid var(--green); outline-offset: 2px; }
  #count { color: var(--muted); margin: 0 2px 8px; white-space: nowrap; }
  #hands { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 475px), 1fr)); gap: 16px; align-items: start; }
  .hand { border: 1px solid var(--line); border-radius: 10px; overflow: hidden; background: var(--panel); }
  .hand-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; background: var(--pale); padding: 10px 14px; }
  h2 { font-size: 1rem; margin: 0; }
  .stamp { display: block; color: var(--muted); font-size: .86rem; font-variant-numeric: tabular-nums; }
  .cards { display: flex; align-items: center; gap: 5px; white-space: nowrap; }
  .card { padding: 3px 6px; border: 1px solid var(--line); border-radius: 5px; background: #fff; font-weight: 720; font-size: 1.13rem; }
  .hearts { color: #b22d39; } .diamonds { color: #1755a6; } .clubs { color: #157351; }
  .no-cards { font-size: .83rem; color: var(--muted); }
  table { width: 100%; border-collapse: collapse; table-layout: fixed; }
  caption { text-align: left; }
  th, td { padding: 7px 9px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
  thead th { font-size: .77rem; color: var(--muted); font-weight: 640; }
  th.seat { width: 25%; } th.stack { width: 17%; }
  tbody th { font-size: .88rem; font-weight: 600; }
  .number { text-align: right; font-variant-numeric: tabular-nums; }
  .hero { background: #f1f8f4; }
  .tag { display: block; width: max-content; color: var(--green); font-size: .74rem; font-weight: 750; }
  .missing { color: var(--amber); }
  .name { font-weight: 670; overflow-wrap: anywhere; }
  .proof summary, .frame summary { cursor: pointer; }
  .proof summary { color: var(--green); }
  .proof summary.missing { color: var(--amber); }
  .proof img { display: block; max-width: 100%; max-height: 70px; object-fit: contain; object-position: left; margin: 5px 0; image-rendering: pixelated; }
  .proof a { cursor: zoom-in; }
  #crop-zoom { width: min(90vw, 720px); max-height: 85vh; padding: 16px; border: 1px solid var(--line); border-radius: 10px; color: var(--ink); }
  #crop-zoom::backdrop { background: #101a19b3; }
  .zoom-header { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 12px; }
  .zoom-header button { font: inherit; cursor: pointer; padding: 4px 12px; border: 1px solid var(--line); border-radius: 6px; color: var(--ink); background: var(--paper); }
  #zoom-image { display: block; width: 100%; max-height: 62vh; object-fit: contain; image-rendering: pixelated; }
  .ocr-meta { color: var(--muted); font-size: .82rem; margin: 5px 0 2px; }
  .frame { padding: 8px 12px; color: var(--green); font-size: .86rem; }
  .frame img { display: block; width: min(100%, 478px); height: auto; margin: 8px 0 2px; border-radius: 5px; }
  .foot { color: var(--muted); font-size: .87rem; margin: 18px 0 0; }
  .visually-hidden { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
  @media (max-width: 700px) {
    .intro { align-items: start; flex-direction: column; }
    .tools { grid-template-columns: 1fr 1fr; }
    .tools label:first-child { grid-column: 1 / -1; }
    #count { grid-column: 1 / -1; margin: 0; }
    th, td { padding-left: 7px; padding-right: 7px; }
  }
</style>
</head>
<body>
<main class="page">
<header class="intro">
  <div><div class="project">PokerAnalyzer</div><h1>Preflop review</h1><p class="source">$source</p></div>
  <dl class="metrics">
    <div><dt>Snapshots</dt><dd>$hands</dd></div>
    <div><dt>Nickname candidates</dt><dd>$names</dd></div>
    <div><dt>Hole-card pairs</dt><dd>$pairs</dd></div>
  </dl>
</header>
<div class="tools">
  <label>Find a nickname or time<input id="query" type="search" placeholder="e.g. 03:10"></label>
  <label>Table<select id="table"><option value="">All tables</option>$tables</select></label>
  <label>Show<select id="focus"><option value="">All snapshots</option><option value="names">Unresolved nicknames</option><option value="cards">Unreadable hole cards</option></select></label>
  <p id="count" role="status">$hands snapshots</p>
</div>
<div id="hands">$sections</div>
<p id="empty" hidden>No snapshots match these filters.</p>
<p class="foot">All stacks are in big blinds. Starting stacks are estimates from visible blind evidence. Select a nickname or unresolved seat, then click the crop to enlarge it.</p>
</main>
<dialog id="crop-zoom" aria-labelledby="zoom-title">
  <form method="dialog" class="zoom-header">
    <h2 id="zoom-title">Nickname crop</h2>
    <button>Close</button>
  </form>
  <img id="zoom-image" alt="">
</dialog>
<script>
  const query = document.querySelector('#query');
  const table = document.querySelector('#table');
  const focus = document.querySelector('#focus');
  const hands = Array.from(document.querySelectorAll('.hand'));
  const count = document.querySelector('#count');
  const empty = document.querySelector('#empty');
  const zoom = document.querySelector('#crop-zoom');
  const zoomImage = document.querySelector('#zoom-image');
  document.querySelector('#hands').addEventListener('click', event => {
    const link = event.target.closest('.proof a');
    if (link && zoom.showModal && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey) {
      event.preventDefault();
      zoomImage.src = link.href;
      zoomImage.alt = link.querySelector('img').alt;
      zoom.showModal();
    }
  });
  function filter() {
    const term = query.value.trim().toLocaleLowerCase();
    let shown = 0;
    for (const hand of hands) {
      const matches = (!table.value || hand.dataset.table === table.value)
        && (!term || hand.dataset.search.includes(term))
        && (!focus.value || hand.dataset[focus.value] === 'true');
      hand.hidden = !matches;
      shown += Number(matches);
    }
    count.textContent = shown === 1 ? '1 snapshot' : shown + ' snapshots';
    empty.hidden = shown !== 0;
  }
  query.addEventListener('input', filter);
  table.addEventListener('change', filter);
  focus.addEventListener('change', filter);
  filter();
</script>
</body></html>
"""


def timecode(seconds):
    milliseconds = round(float(seconds) * 1000)
    total_seconds, fractional = divmod(milliseconds, 1000)
    minutes, second = divmod(total_seconds, 60)
    hour, minute = divmod(minutes, 60)
    prefix = f"{hour}:" if hour else ""
    suffix = f".{fractional:03d}".rstrip("0") if fractional else ""
    return f"{prefix}{minute:02d}:{second:02d}{suffix}"


def display(value):
    return escape(str(value)) if value is not None and value != "" else "—"


def card_markup(value):
    if not value:
        return '<span class="no-cards">Hole cards unreadable</span>'
    suits = {"h": ("♥", "hearts"), "d": ("♦", "diamonds"), "c": ("♣", "clubs"), "s": ("♠", "spades")}
    cards = []
    for card in value.split():
        suit = suits.get(card[-1])
        if len(card) == 2 and suit:
            symbol, label = suit
            rank = "10" if card[0] == "T" else escape(card[0])
            cards.append(f'<span class="card {label}" aria-label="{rank} of {label}">{rank}{symbol}</span>')
    return '<div class="cards" aria-label="Recorded player’s hole cards">' + "".join(cards) + "</div>"


def image_link(output, filename, alt):
    if not (output / "frames" / filename).is_file():
        return ""
    url = f"frames/{quote(filename)}"
    return f'<a href="{url}"><img loading="lazy" src="{url}" alt="{escape(alt)}"></a>'


def nickname_markup(row, output):
    label = row["nickname_candidate"]
    status = row["nickname_status"]
    missing = {"disagrees": "Conflicting reads", "unreadable": "Unresolved"}
    title = escape(label) if label else missing.get(status, "Unresolved")
    css = "name" if label else "name missing"
    seat = row["visual_seat"].replace("-", " ")
    filename = f'{int(row["snapshot_id"]):04}-{row["visual_seat"]}.png'
    image = image_link(output, filename, f"Source name label in the {seat} seat")
    evidence = []
    timestamp = row["nickname_video_seconds"]
    if timestamp is not None:
        evidence.append("Frame " + timecode(timestamp))
    if label:
        support = int(row["nickname_support"])
        evidence.append(f"{support} supporting frame" + ("s" if support != 1 else ""))
        if status == "wide_label_ocr":
            evidence.append("Wider label crop")
        if status == "delayed_name_ocr":
            evidence.append("Later frame from this hand")
        if row["nickname_confidence"] is not None:
            evidence.append(f'OCR score {row["nickname_confidence"]}')
    else:
        evidence.append("No nickname accepted")
        if row["nickname_ocr"]:
            evidence.append(f'OCR read {row["nickname_ocr"]}')
    if not image and not evidence:
        return f'<span class="{css}">{title}</span>'
    meta = f'<p class="ocr-meta">{escape(" · ".join(evidence))}</p>' if evidence else ""
    return f'<details class="proof"><summary class="{css}">{title}</summary>{image}{meta}</details>'


def render_hand(rows, output):
    first = rows[0]
    snapshot_id = int(first["snapshot_id"])
    table = first["table"]
    timestamp = timecode(first["video_seconds"])
    table_label = escape(table.replace("-", " ").title())
    hole_cards = next((row["recorded_player_cards"] for row in rows if row["recorded_player_cards"]), "")
    missing_names = any(not row["nickname_candidate"] for row in rows)
    search = " ".join([str(snapshot_id), timestamp, table, *(row["nickname_candidate"] for row in rows)]).lower()
    seats = []
    for row in rows:
        seat = row["visual_seat"]
        hero = seat == "bottom"
        marker = '<span class="tag">Recorded player</span>' if hero else ""
        if seat == row["dealer_visual_seat"]:
            marker += '<span class="tag" title="Visible dealer button">BTN</span>'
        row_class = ' class="hero"' if hero else ""
        shown_stack = display(row["visible_stack_bb"])
        if row.get("stack_ocr_status") == "review":
            shown_stack = '<span class="missing" title="Check the number in the table frame">Review</span>'
        seats.append(f"""<tr{row_class}>
<th scope="row">{escape(seat.replace("-", " ").title())}{marker}</th>
<td>{nickname_markup(row, output)}</td>
<td class="number">{display(row["starting_stack_bb"])}</td>
<td class="number">{shown_stack}</td></tr>""")
    image = image_link(output, f"{snapshot_id:04}.jpg", f"{table_label} table at {timestamp}")
    frame = f'<details class="frame"><summary>Show table frame</summary>{image}</details>' if image else ""
    return f"""<article class="hand" data-table="{escape(table)}" data-search="{escape(search)}"
 data-names="{str(missing_names).lower()}" data-cards="{str(not hole_cards).lower()}">
<header class="hand-head"><div><h2>Snapshot {snapshot_id}</h2><span class="stamp">{timestamp} · {table_label}</span></div>
{card_markup(hole_cards)}</header>
<table><caption class="visually-hidden">Seats in snapshot {snapshot_id}</caption>
<thead><tr><th scope="col" class="seat">Seat</th><th scope="col">Nickname candidate</th>
<th scope="col" class="stack number">Start BB</th><th scope="col" class="stack number">Shown BB</th></tr></thead>
<tbody>{"".join(seats)}</tbody></table>{frame}</article>"""


def write_report(output, rows, summary):
    groups = defaultdict(list)
    for row in rows:
        groups[row["snapshot_id"]].append(row)
    tables = dict.fromkeys(row["table"] for row in rows)
    content = Template(PAGE).substitute(
        source=escape(summary["source"]),
        hands=f"{len(groups):,}",
        names=f'{summary["nickname_candidates"]:,}',
        pairs=f'{summary["hole_card_pairs_read"]:,}',
        tables="".join(f'<option value="{escape(table)}">{escape(table.replace("-", " ").title())}</option>'
                       for table in tables),
        sections="".join(render_hand(group, output) for group in groups.values()),
    )
    (output / "review.html").write_text(content, encoding="utf-8")
