"use strict";

// Chrome serializes this function before injecting it.
async function inspectPokerCraft(action) {
  const isFixture = location.origin === "http://127.0.0.1:8765" &&
    location.pathname === "/game-history.html";
  if ((location.origin !== "https://my.pokercraft.com" ||
       location.pathname !== "/rush-and-cash") && !isFixture) {
    return { available: false };
  }

  const page = document.querySelector("app-game-rush-and-cash");
  if (!page) return { available: false };

  const rendered = (element) => element && element.getClientRects().length &&
    getComputedStyle(element).visibility === "visible" &&
    !element.closest('[hidden], [aria-hidden="true"]');
  const rows = [...page.querySelectorAll("app-sessions .table-wrapper > table > tbody > tr.mat-mdc-row")]
    .filter(rendered);
  const selected = rows.filter((row) => {
    const input = row.querySelector('td.mat-column-Checkbox input[type="checkbox"]');
    return input && (input.checked || input.classList.contains("mdc-checkbox--selected"));
  }).length;
  const history = page.querySelector('app-game-session-detail > nav > button[kind="Hands"]');
  const download = page.querySelector("app-download-button-game-session-hand > button.btn-download");
  const canOpenHistory = selected > 0 && rendered(history) && !history.disabled &&
    history.getAttribute("active") !== "true";
  const canDownload = selected > 0 && rendered(download) && !download.disabled;

  if (action === "history" && canOpenHistory) {
    history.click();
    return { clicked: true };
  } else if (action === "download" && canDownload) {
    download.click();
    return { clicked: true };
  }

  function readHandPage() {
    const hands = page.querySelector("app-game-session-detail-hands");
    const container = hands?.querySelector(":scope > .table-container");
    const table = container?.querySelector(":scope > table");
    if (!rendered(table) || container.getAttribute("progress") !== "false") return null;

    const headers = [...(table.tHead?.rows[0]?.cells || [])].map((cell) => cell.innerText.trim());
    const handColumn = headers.indexOf("Hand ID");
    const winnerColumn = headers.indexOf("Winner");
    if (handColumn < 0 || winnerColumn < 0) return null;

    const handRows = [...table.tBodies].flatMap((body) => [...body.rows]).filter(rendered);
    const ids = [];
    const records = [];
    for (const row of handRows) {
      const cell = row.cells[handColumn];
      const winner = row.cells[winnerColumn];
      const match = rendered(cell) && /^([A-Z]+)([0-9]+)$/.exec(cell.innerText.trim());
      if (!match) continue;
      const id = match[1] + (match[2].replace(/^0+/, "") || "0");
      ids.push(id);
      const nickname = rendered(winner) && !winner.children.length && winner.innerText.trim();
      if (nickname && !/[\x00-\x1f\x7f]/.test(nickname)) records.push([id, nickname]);
    }

    const pagination = hands.querySelector(":scope > .pagination");
    const pageNumber = Number(pagination?.querySelector(".current-page")?.textContent);
    const next = pagination?.querySelector("button > i.fa-angle-right")?.parentElement;
    return {
      ids, records, rowCount: handRows.length,
      pageNumber: Number.isSafeInteger(pageNumber) && pageNumber > 0 ? pageNumber : null,
      next, hasPagination: !!pagination,
    };
  }

  let current = readHandPage();
  const summary = {
    available: true, selected, sessionCount: rows.length, canOpenHistory, canDownload,
    canCapture: !!current?.ids.length,
    pageNumber: current?.pageNumber,
  };
  if (action !== "capture" || !summary.canCapture) return summary;

  const records = [...current.records];
  let rowCount = current.rowCount;
  let pages = 1;
  let stopped = "";
  const seenIds = new Set(current.ids);

  while (current.hasPagination) {
    if (!current.next || !current.pageNumber) {
      stopped = "PokerCraft’s pagination controls changed.";
      break;
    }
    if (current.next.disabled) break;
    current.next.click();

    const deadline = Date.now() + 8000;
    let loaded = null;
    while (Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 150));
      const candidate = readHandPage();
      if (candidate?.pageNumber === current.pageNumber + 1 &&
          candidate.ids.some((id) => !seenIds.has(id))) {
        loaded = candidate;
        break;
      }
    }
    if (!loaded) {
      stopped = "The next page did not finish rendering.";
      break;
    }
    current = loaded;
    for (const id of current.ids) seenIds.add(id);
    for (const record of current.records) records.push(record);
    rowCount += current.rowCount;
    pages += 1;
  }
  return { ...summary, records, rowCount, pages, stopped };
}
