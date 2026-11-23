"use strict";

const form = document.querySelector("#capture");
const capture = form.querySelector('[type="submit"]');
const exportButton = document.querySelector("#export");
const status = document.querySelector("#status");
const count = document.querySelector("#count");
const fields = ["origin", "row", "hand", "winner"];

function show(records) {
  const hands = new Set(records.map(([hand]) => hand));
  count.textContent = `${hands.size} unique hands captured`;
  exportButton.disabled = !records.length;
}

async function initialize() {
  const { config, records = [] } = await chrome.storage.local.get(["config", "records"]);
  for (const field of fields) {
    form.elements[field].value = config?.[field] || "";
  }
  show(records);
  capture.disabled = false;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  capture.disabled = true;
  status.textContent = "Reading the open page…";
  try {
    const config = Object.fromEntries(fields.map((field) => [field, form.elements[field].value.trim()]));
    const origin = new URL(config.origin);
    const loopback = ["localhost", "127.0.0.1"].includes(origin.hostname);
    if ((origin.protocol !== "https:" && !(loopback && origin.protocol === "http:")) ||
        ![origin.origin, origin.origin + "/"].includes(config.origin)) {
      throw new Error("Enter the HTTPS origin only, for example the scheme and host without a page path.");
    }
    config.origin = origin.origin;
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.url || new URL(tab.url).origin !== config.origin) {
      throw new Error("Open Game History at the configured origin.");
    }
    const [{ result }] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: collectRows,
      args: [config],
    });
    if (!result) throw new Error("The page could not be read.");

    await navigator.locks.request("winner-collection", async () => {
      const { records = [] } = await chrome.storage.local.get("records");
      const merged = [...new Map([...records, ...result.records].map((record) =>
        [JSON.stringify(record), record])).values()];
      await chrome.storage.local.set({ config, records: merged });
      show(merged);
      const added = merged.length - records.length;
      status.textContent = `${result.records.length}/${result.renderedRows} rendered rows usable. ${added} new observations.`;
      if (!result.renderedRows) {
        status.textContent += " Verify the row selector and open the Game History results.";
      }
    });
  } catch (error) {
    status.textContent = error.message;
  } finally {
    capture.disabled = false;
  }
});

exportButton.addEventListener("click", async () => {
  try {
    const { records = [] } = await chrome.storage.local.get("records");
    const quote = (value) => '"' + value.replaceAll('"', '""') + '"';
    const lines = records.map((record) => record.map(quote).join(","));
    const csv = ["hand_id,winner_nickname", ...lines, ""].join("\r\n");
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = "pokercraft-winners.csv";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    status.textContent = `Exported ${records.length} observations.`;
  } catch (error) {
    status.textContent = error.message;
  }
});

initialize().catch((error) => { status.textContent = error.message; });
