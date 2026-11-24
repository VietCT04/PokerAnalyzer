"use strict";

const byId = (id) => document.getElementById(id);
const number = new Intl.NumberFormat();
const percent = new Intl.NumberFormat(undefined, {maximumFractionDigits: 1});
const headers = [...document.querySelectorAll("th[data-key]")];
const search = byId("search");
const files = byId("files");
const analyze = byId("analyze");
const bdiPanel = byId("bdi-panel");
const bdiScope = byId("bdi-scope");
const bdiGroup = byId("bdi-group");
const tendencyPanel = byId("tendency-panel");
const tendencyScope = byId("tendency-scope");
const tendencyType = byId("tendency-type");
const tendencyGroup = byId("tendency-group");
const signed = new Intl.NumberFormat(undefined, {
  signDisplay: "exceptZero", minimumFractionDigits: 1, maximumFractionDigits: 1,
});
let players = [];
let sortKey = "observed_hands_in_unit";
let ascending = false;
let limit = 30;
let lastJob = "";
let dashboardRequest = 0;
let bdiRequest = 0;
let bdi = null;
let tendencyRequest = 0;
let tendencies = null;

async function request(path, options) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Dashboard request failed.");
  return data;
}

function post(path, body, json = false) {
  const headers = {"X-PokerAnalyzer": "1"};
  if (json) headers["Content-Type"] = "application/json";
  return request(path, {method: "POST", headers, body: json ? JSON.stringify(body) : body});
}

function countLabel(value, noun) {
  return `${number.format(value)} ${noun}${value === 1 ? "" : "s"}`;
}

function describeImport(counts) {
  const results = [];
  if ("added" in counts) {
    results.push(countLabel(counts.added, "new hand"));
    if (counts.duplicate) results.push(countLabel(counts.duplicate, "duplicate hand"));
    if (counts.scoped) results.push(countLabel(counts.scoped, "hand linked to a session"));
    const conflicts = (counts["content conflict"] || 0) + (counts["session conflict"] || 0) + (counts["account conflict"] || 0);
    if (conflicts) results.push(countLabel(conflicts, "skipped conflicting hand"));
  }
  if ("observations" in counts) results.push(countLabel(counts.observations, "new winner observation"));
  return results.join(" · ");
}

function cell(row, value, detail = "", tone = "") {
  const td = row.insertCell();
  td.textContent = value;
  if (tone) td.className = tone;
  if (detail) {
    const small = document.createElement("small");
    small.textContent = detail;
    td.append(small);
  }
}

function bdiCell(row, value, scale, detail = "") {
  cell(row, value === null ? "—" : signed.format(value) + " pp", detail);
  if (value === null) return;
  const track = document.createElement("div");
  track.className = "bdi-track";
  track.setAttribute("aria-hidden", "true");
  const bar = document.createElement("progress");
  bar.className = value < 0 ? "bdi-negative" : "bdi-positive";
  bar.max = scale;
  bar.value = Math.abs(value);
  track.append(bar);
  row.lastElementChild.append(track);
}

function tableHead(id, headings) {
  byId(id).replaceChildren(...headings.map((label) => {
    const th = document.createElement("th");
    th.scope = "col";
    th.textContent = label;
    return th;
  }));
}

function renderBDI() {
  if (!bdi) return;
  const player = Object.hasOwn(bdi.players, bdiScope.value) ? bdi.players[bdiScope.value] : null;
  for (const option of bdiGroup.options) {
    option.hidden = !!player && option.hasAttribute("data-population");
  }
  if (bdiGroup.selectedOptions[0].hidden) bdiGroup.value = "overall";
  const group = bdiGroup.value;
  const distribution = group === "distribution";
  const breakdown = (player || bdi.population)[group];
  const values = breakdown.flatMap((row) => player ? [row.bdi] : [row.bet_weighted, row.player_weighted]);
  const scale = Math.max(10, Math.ceil(Math.max(0, ...values.map((value) => Math.abs(value || 0))) / 10) * 10);
  byId("bdi-scale").textContent = distribution ? "" : `Chart scale ±${scale} pp`;
  const headings = distribution ? ["Overall BDI range (pp)", "Bettors", "Bets"]
    : player ? ["Category", "BDI", "Bets"] : ["Category", "Bet-weighted BDI", "Player-weighted BDI", "Bets"];
  tableHead("bdi-head", headings);
  byId("bdi-rows").replaceChildren(...breakdown.map((item) => {
    const row = document.createElement("tr");
    cell(row, item.label);
    if (distribution) cell(row, number.format(item.players));
    else if (player) bdiCell(row, item.bdi, scale);
    else {
      bdiCell(row, item.bet_weighted, scale);
      bdiCell(row, item.player_weighted, scale, item.players
        ? `${countLabel(item.players, "bettor")} · ${countLabel(item.named_bets, "named bet")}` : "");
    }
    cell(row, number.format(item.bets));
    return row;
  }));
  byId("bdi-note").textContent = player ? "Bets attributed to this nickname."
    : distribution ? "One overall BDI per identified bettor. Bet counts sum across each range."
      : "Bet-weighted includes all qualifying bets. Player-weighted gives each identified bettor equal weight. Nicknames come from winner evidence.";
  byId("bdi-table").hidden = !breakdown.length;
}

function rateText(value) {
  return value === null ? "—" : percent.format(value) + "%";
}

function openTendencyProfile(nickname) {
  tendencyScope.value = nickname;
  if (tendencyPanel.open) loadTendencies();
  else tendencyPanel.open = true;
  tendencyPanel.scrollIntoView({block: "start"});
  tendencyScope.focus({preventScroll: true});
}

function renderTendencies() {
  if (!tendencies) return;
  const type = tendencyType.value;
  for (const option of tendencyGroup.options) {
    option.hidden = type === "frequencies" && option.hasAttribute("data-exposure");
  }
  if (tendencyGroup.selectedOptions[0].hidden) tendencyGroup.value = "overall";
  const group = tendencyGroup.value;
  const rows = tendencies[type][group];
  const outcomes = type === "outcomes";
  const compare = type === "frequencies" && tendencies.nickname !== null;
  const exposure = group === "sizing" || group === "street_size";
  const headings = outcomes ? ["Spot", ...tendencies.outcome_labels, "Bets"]
    : ["Metric", "Spot", "Observed", ...(compare ? ["Matched population", "BFD"] : []), "Sample"];
  tableHead("tendency-head", headings);
  byId("tendency-rows").replaceChildren(...rows.map((item) => {
    const row = document.createElement("tr");
    if (outcomes) {
      cell(row, item.label);
      tendencies.outcome_labels.forEach((label) => {
        const count = item.outcomes[label];
        cell(row, rateText(item.samples ? count * 100 / item.samples : null), number.format(count));
      });
    } else {
      cell(row, item.metric);
      cell(row, item.label);
      cell(row, rateText(item.pct), `${number.format(item.events)} / ${number.format(item.samples)}`);
      if (compare) {
        cell(row, rateText(item.benchmark_pct), countLabel(item.benchmark_samples, "other opportunity"));
        bdiCell(row, item.bfd, 100, item.matched
          ? `${rateText(item.matched_pct)} on ${number.format(item.matched)} matched` : "0 matched");
      }
    }
    cell(row, number.format(item.samples),
      `${countLabel(item.hands, "hand")} · ${number.format(item.named)} named · ${number.format(item.samples - item.named)} unnamed`);
    return row;
  }));
  const notes = {
    frequencies: compare ? "BFD compares matched opportunities against other bettors in the same situations."
      : "Betting frequency uses opening opportunities. The population includes named and unnamed players.",
    outcomes: "Raised takes priority. For unraised called bets, win, loss, and tie come from the final hand result.",
    summary: exposure ? "Size groups use recorded exposure to the opening bet. Hand results count once per group."
      : "WWSF and WTSD use players who saw the flop. W$SD uses showdown participants with a known payout result.",
  };
  byId("tendency-note").textContent = notes[type];
  const bdi = tendencies.bdi;
  const bdiValue = tendencies.nickname === null ? bdi.bet_weighted : bdi.bdi;
  byId("tendency-bdi").textContent = bdiValue === null ? "River BDI · No qualifying called showdowns"
    : `River BDI · ${signed.format(bdiValue)} pp · ${countLabel(bdi.bets, "called showdown")} · Showdown-loss proxy`;
  byId("tendency-table").hidden = !rows.length;
  const status = byId("tendency-status");
  status.textContent = "No observations match this breakdown.";
  status.hidden = !!rows.length;
}

async function loadTendencies() {
  const loading = ++tendencyRequest;
  const status = byId("tendency-status");
  status.hidden = false;
  status.textContent = "Analyzing historical decisions…";
  tendencies = null;
  byId("tendency-table").hidden = true;
  byId("tendency-note").textContent = "";
  byId("tendency-bdi").textContent = "";
  try {
    const query = tendencyScope.value ? "?player=" + encodeURIComponent(tendencyScope.value) : "";
    const data = await request("/api/tendencies" + query);
    if (loading !== tendencyRequest) return;
    tendencies = data;
    byId("tendency-heading").textContent = data.nickname || countLabel(data.audit.imported_hands, "imported hand");
    renderTendencies();
  } catch (error) {
    if (loading === tendencyRequest) status.textContent = error.message;
  }
}

async function loadBDI() {
  const loading = ++bdiRequest;
  const status = byId("bdi-status");
  status.hidden = false;
  status.textContent = "Analyzing called river bets…";
  try {
    const data = await request("/api/bdi");
    if (loading !== bdiRequest) return;
    bdi = data;
    const selected = bdiScope.value;
    bdiScope.replaceChildren(new Option("Population", ""), ...Object.keys(data.players)
      .map((name) => new Option(name, name)));
    if (Object.hasOwn(data.players, selected)) bdiScope.value = selected;
    const bets = data.population.overall[0].bets;
    byId("bdi-summary").textContent = countLabel(bets, "qualifying bet");
    status.textContent = "No qualifying bets in the imported hands.";
    status.hidden = !!bets;
    renderBDI();
  } catch (error) {
    if (loading === bdiRequest) status.textContent = error.message;
  }
}

function playerRow(player) {
  const row = document.createElement("tr");
  const mark = (value) => value === "" ? "—" : value + "%";
  const tone = (value) => Number(value) > 0 ? "gain" : Number(value) < 0 ? "loss" : "";
  const identity = `${number.format(player.direct_hands)} direct · ${number.format(player.inferred_hands)} inferred`;
  const handDetail = player.total_observed_hands !== player.observed_hands_in_unit
    ? `${number.format(player.total_observed_hands)} total · ${identity}` : identity;
  cell(row, player.nickname, player.unit);
  const profile = document.createElement("button");
  profile.type = "button";
  profile.className = "profile-link";
  profile.textContent = player.nickname;
  profile.title = "View historical tendencies";
  profile.addEventListener("click", () => openTendencyProfile(player.nickname));
  row.cells[0].firstChild.replaceWith(profile);
  cell(row, number.format(player.observed_hands_in_unit), handDetail);
  cell(row, mark(player.vpip_pct), countLabel(player.preflop_opportunities, "decision"));
  cell(row, mark(player.pfr_pct));
  cell(row, mark(player.three_bet_pct), countLabel(player.three_bet_opportunities, "chance"));
  cell(row, player.net_winnings || "—", countLabel(player.net_hands, "hand"), tone(player.net_winnings));
  cell(row, player.bb_per_100 || "—", countLabel(player.bb_hands, "hand"), tone(player.bb_per_100));
  return row;
}

function renderPlayers() {
  const query = search.value.trim().toLowerCase();
  const matching = players.filter((player) => player.nickname.toLowerCase().includes(query));
  matching.sort((a, b) => {
    const left = a[sortKey];
    const right = b[sortKey];
    if (left === "" || right === "") return Number(left === "") - Number(right === "");
    const cmp = sortKey === "nickname" ? left.localeCompare(right) : Number(left) - Number(right);
    return (ascending ? cmp : -cmp) || a.nickname.localeCompare(b.nickname);
  });
  for (const th of headers) {
    if (th.dataset.key === sortKey) th.setAttribute("aria-sort", ascending ? "ascending" : "descending");
    else th.removeAttribute("aria-sort");
  }
  byId("players").replaceChildren(...matching.slice(0, limit).map(playerRow));
  byId("player-count").textContent = countLabel(new Set(matching.map((row) => row.nickname)).size, "identified nickname");
  byId("empty").hidden = !!matching.length;
  byId("empty").textContent = players.length ? "No nicknames match your search." : "Player stats appear after a winner CSV matches imported hands.";
  byId("more").hidden = matching.length <= limit;
}

function renderDashboard(data) {
  const coverage = data.appearances ? data.identified * 100 / data.appearances : 0;
  byId("hands").textContent = number.format(data.hands);
  byId("net").textContent = countLabel(data.ledgers, "reconciled hand");
  byId("coverage").textContent = percent.format(coverage) + "%";
  byId("coverage-bar").value = coverage;
  byId("appearances").textContent = `${number.format(data.identified)} of ${number.format(data.appearances)} appearances`;
  byId("coverage-methods").textContent = `${number.format(data.direct)} direct · ${number.format(data.inferred)} inferred`;
  byId("mappings").textContent = number.format(data.mappings);
  byId("conflicts").textContent = number.format(data.conflicts);
  const pending = byId("pending-histories");
  pending.hidden = !data.unlinked;
  pending.textContent = countLabel(data.unlinked, "winner record") + " awaiting matching hand histories.";
  players = data.players;
  const selectedPlayer = tendencyScope.value;
  const names = [...new Set(players.map((player) => player.nickname))].sort((a, b) => a.localeCompare(b));
  tendencyScope.replaceChildren(new Option("Population", ""), ...names.map((name) => new Option(name, name)));
  if (names.includes(selectedPlayer)) tendencyScope.value = selectedPlayer;
  renderPlayers();
  if (tendencyPanel.open) loadTendencies();
  if (bdiPanel.open) loadBDI();
  if (data.analysis) renderAnalysis(data.analysis);
}

async function loadDashboard() {
  const loading = ++dashboardRequest;
  const data = await request("/api/dashboard");
  if (loading === dashboardRequest) renderDashboard(data);
}

function renderAnalysis(summary) {
  byId("analysis-panel").hidden = false;
  const time = new Date(summary.completed_at).toLocaleString();
  byId("analysis-time").textContent = (summary.initial ? "First analysis · " : "Since previous analysis · ") + time;
  byId("analysis-hands").textContent = number.format(summary.new_hands);
  byId("analysis-added").textContent = number.format(summary.new_mappings);
  byId("analysis-removed").textContent = number.format(summary.removed_mappings);
  byId("analysis-propagated").textContent = number.format(summary.propagated);
  byId("analysis-evidence").textContent = [
    number.format(summary.direct) + " directly identified from winner evidence",
    countLabel(summary.conflicts, "conflicting match"),
    countLabel(summary.unlinked, "unlinked winner record"),
  ].join(" · ");
}

function showImport(message, error = false) {
  const status = byId("import-status");
  status.hidden = false;
  status.dataset.error = error;
  status.textContent = message;
}

async function pollJob() {
  try {
    const job = await request("/api/job");
    const recorded = job.observations ? countLabel(job.observations, "winner record") + " saved. " : "";
    const messages = {
      waiting: recorded + "Waiting for the hand-history ZIP. If Chrome has no download in progress, click Download on PokerCraft.",
      importing: "Importing " + job.file + "…",
      complete: "Import complete. " + describeImport(job.counts || {}) +
        (job.partial ? ". Capture stopped before a later page rendered." : ""),
      timeout: recorded + "The matching ZIP did not arrive in time. Download it from PokerCraft and import the ZIP here.",
      stopped: "Stopped waiting for a ZIP.",
      error: job.message,
    };
    const panel = byId("job");
    panel.hidden = job.status === "idle";
    panel.dataset.error = ["error", "timeout"].includes(job.status);
    byId("job-status").textContent = messages[job.status] || "";
    const stop = byId("stop");
    stop.hidden = job.status !== "waiting";
    stop.onclick = async () => {
      try { await post("/api/stop", {id: job.id}, true); }
      catch (error) { showImport(error.message, true); }
    };
    const key = job.id + ":" + job.status;
    if (key !== lastJob && (job.status === "waiting" || job.status === "complete")) await loadDashboard();
    lastJob = key;
  } catch (error) {
    showImport(error.message, true);
  } finally {
    setTimeout(pollJob, 1500);
  }
}

files.addEventListener("change", () => {
  byId("import").disabled = !files.files.length;
  byId("file-summary").textContent = files.files.length ? countLabel(files.files.length, "file") + " selected" : "ZIP and CSV files";
});

analyze.addEventListener("click", async () => {
  const status = byId("analysis-status");
  const analyzing = ++dashboardRequest;
  analyze.disabled = true;
  status.hidden = false;
  status.dataset.error = false;
  status.textContent = "Analyzing saved hands and winner mappings…";
  try {
    const data = await post("/api/analyze", {}, true);
    if (analyzing === dashboardRequest) renderDashboard(data);
    else await loadDashboard();
    status.hidden = true;
  } catch (error) {
    status.dataset.error = true;
    status.textContent = error.message;
  } finally {
    analyze.disabled = false;
  }
});

byId("import-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = byId("import");
  button.disabled = true;
  files.disabled = true;
  showImport("Importing selected files…");
  try {
    const body = new FormData();
    for (const file of files.files) body.append("files", file);
    const result = await post("/api/import", body);
    showImport("Import complete. " + describeImport(result.counts));
    files.value = "";
    files.dispatchEvent(new Event("change"));
    await loadDashboard();
  } catch (error) {
    showImport(error.message, true);
  } finally {
    files.disabled = false;
    button.disabled = !files.files.length;
  }
});

search.addEventListener("input", () => {
  limit = 30;
  renderPlayers();
});
bdiPanel.addEventListener("toggle", () => { if (bdiPanel.open) loadBDI(); });
bdiScope.addEventListener("change", renderBDI);
bdiGroup.addEventListener("change", renderBDI);
tendencyPanel.addEventListener("toggle", () => { if (tendencyPanel.open) loadTendencies(); });
tendencyScope.addEventListener("change", loadTendencies);
tendencyType.addEventListener("change", () => {
  tendencyGroup.value = tendencyType.value === "outcomes" ? "street_size" : "overall";
  renderTendencies();
});
tendencyGroup.addEventListener("change", renderTendencies);
byId("more").addEventListener("click", () => {
  limit += 30;
  renderPlayers();
});
for (const th of headers) {
  th.querySelector("button").addEventListener("click", () => {
    const key = th.dataset.key;
    ascending = sortKey === key ? !ascending : key === "nickname";
    sortKey = key;
    renderPlayers();
  });
}

loadDashboard().catch((error) => showImport(error.message, true));
pollJob();
