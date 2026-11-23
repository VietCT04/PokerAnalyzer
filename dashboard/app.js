"use strict";

const byId = (id) => document.getElementById(id);
const number = new Intl.NumberFormat();
const percent = new Intl.NumberFormat(undefined, {maximumFractionDigits: 1});
const headers = [...document.querySelectorAll("th[data-key]")];
const search = byId("search");
const files = byId("files");
const analyze = byId("analyze");
let players = [];
let sortKey = "observed_hands_in_unit";
let ascending = false;
let limit = 30;
let lastJob = "";
let dashboardRequest = 0;

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
    const conflicts = (counts["content conflict"] || 0) + (counts["session conflict"] || 0);
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

function playerRow(player) {
  const row = document.createElement("tr");
  const mark = (value) => value === "" ? "—" : value + "%";
  const tone = (value) => Number(value) > 0 ? "gain" : Number(value) < 0 ? "loss" : "";
  cell(row, player.nickname, player.unit);
  cell(row, number.format(player.observed_hands_in_unit),
    player.total_observed_hands !== player.observed_hands_in_unit ? `${number.format(player.total_observed_hands)} total` : "");
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
  byId("player-count").textContent = countLabel(new Set(matching.map((row) => row.nickname)).size, "verified nickname");
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
  byId("mappings").textContent = number.format(data.mappings);
  byId("conflicts").textContent = number.format(data.conflicts);
  players = data.players;
  renderPlayers();
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
    const messages = {
      waiting: "Winner capture saved. Waiting for the matching PokerCraft ZIP in your downloads folder.",
      importing: "Importing " + job.file + "…",
      complete: "Import complete. " + describeImport(job.counts || {}) +
        (job.partial ? ". Capture stopped before a later page rendered." : ""),
      timeout: "Winner capture saved. If the ZIP downloaded elsewhere, choose it above to finish the import.",
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
    if (key !== lastJob && job.status === "complete") await loadDashboard();
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
