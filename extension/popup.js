"use strict";

const historyButton = document.querySelector("#history");
const captureButton = document.querySelector("#capture");
const downloadButton = document.querySelector("#download");
const importButton = document.querySelector("#import");
const exportButton = document.querySelector("#export");
const selection = document.querySelector("#selection");
const status = document.querySelector("#status");
const count = document.querySelector("#count");
const dashboardUrl = document.querySelector("#dashboard").href;

async function inspect(action) {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const url = tab?.url && new URL(tab.url);
  if (!url || (url.origin !== "https://my.pokercraft.com" && url.origin !== "http://127.0.0.1:8765")) {
    return { available: false };
  }
  const [{ result }] = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: inspectPokerCraft,
    args: [action ?? null],
  });
  return result || { available: false };
}

function showPage(page) {
  historyButton.disabled = !page.canOpenHistory;
  historyButton.hidden = !!page.canCapture;
  captureButton.disabled = !page.canCapture;
  downloadButton.disabled = !page.canDownload;
  importButton.disabled = !page.canCapture || !page.canDownload;
  if (!page.available) {
    selection.textContent = "Open Rush & Cash on my.pokercraft.com.";
    status.textContent = "";
    return;
  }
  selection.textContent = `${page.selected} of ${page.sessionCount} displayed sessions selected`;
  if (!page.sessionCount) {
    status.textContent = "Load the session list in PokerCraft.";
  } else if (page.canCapture) {
    status.textContent = page.pageNumber
      ? `Captures from page ${page.pageNumber} through the last reachable page.`
      : "Captures the rendered hand table.";
  } else if (page.selected === 0) {
    status.textContent = "Select a session in PokerCraft and open Game History.";
  } else {
    status.textContent = "Open Game History and wait for the hand rows to load.";
  }
}

async function dashboardRequest(path, body) {
  const options = body ? {
    method: "POST",
    headers: {"Content-Type": "application/json", "X-PokerAnalyzer": "1"},
    body: JSON.stringify(body),
  } : undefined;
  let response;
  try {
    response = await fetch(dashboardUrl + path, options);
  } catch {
    throw new Error("Start the local PokerAnalyzer dashboard first.");
  }
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "Dashboard request failed.");
  return result;
}

async function downloadAndImport() {
  const state = await dashboardRequest("api/job");
  if (state.status === "waiting" || state.status === "importing") {
    throw new Error("An export is already pending in the dashboard.");
  }
  const page = await inspect("capture");
  if (!page.records?.length) throw new Error("No usable winner rows were captured.");
  await saveCapture(page.records);
  const job = await dashboardRequest("api/watch", {records: page.records, partial: !!page.stopped});
  const dashboard = await chrome.tabs.create({url: dashboardUrl, active: false});
  const result = await inspect("download");
  if (!result.clicked) {
    await dashboardRequest("api/stop", {id: job.id});
    throw new Error("PokerCraft’s ZIP download is unavailable for the current selection.");
  }
  await chrome.tabs.update(dashboard.id, {active: true});
}

async function run(action) {
  historyButton.disabled = true;
  captureButton.disabled = true;
  downloadButton.disabled = true;
  importButton.disabled = true;
  if (action === "capture" || action === "import") status.textContent = "Capturing rendered hand pages…";
  try {
    if (action === "import") {
      await downloadAndImport();
      window.close();
      return;
    }
    const page = await inspect(action);
    if (page.clicked) {
      window.close();
      return;
    }
    if (page.records) {
      if (page.records.length) {
        downloadCsv(await saveCapture(page.records));
      }
      showPage(await inspect());
      const result = page.records.length
        ? `Captured ${page.records.length}/${page.rowCount} rows on ${page.pages} page(s).`
        : "No usable winner rows were found.";
      status.textContent = `${result} ${page.stopped}`.trim();
    } else {
      showPage(page);
    }
  } catch (error) {
    if (action) {
      try { showPage(await inspect()); }
      catch { /* The tab may have closed. */ }
    }
    status.textContent = error.message;
  }
}

historyButton.addEventListener("click", () => run("history"));
captureButton.addEventListener("click", () => run("capture"));
downloadButton.addEventListener("click", () => run("download"));
importButton.addEventListener("click", () => run("import"));

function showSaved(records) {
  const hands = new Set(records.map(([hand]) => hand));
  count.textContent = hands.size ? `${hands.size} unique hands saved` : "";
  exportButton.hidden = !records.length;
}

async function readSaved() {
  const { records = [] } = await chrome.storage.local.get("records");
  showSaved(records);
  return records;
}

async function saveCapture(captured) {
  return navigator.locks.request("winner-collection", async () => {
    const { records = [] } = await chrome.storage.local.get("records");
    const merged = [...new Map([...records, ...captured].map((item) => [JSON.stringify(item), item])).values()];
    await chrome.storage.local.set({ records: merged });
    showSaved(merged);
    return merged;
  });
}

function downloadCsv(records) {
  const quote = (value) => '"' + value.replaceAll('"', '""') + '"';
  const lines = records.map((record) => record.map(quote).join(","));
  const csv = ["hand_id,winner_nickname", ...lines, ""].join("\r\n");
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = "pokercraft-winners.csv";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

exportButton.addEventListener("click", async () => {
  try {
    const records = await readSaved();
    if (!records.length) return;
    downloadCsv(records);
  } catch (error) {
    status.textContent = error.message;
  }
});

readSaved().catch((error) => { status.textContent = error.message; });
run();
