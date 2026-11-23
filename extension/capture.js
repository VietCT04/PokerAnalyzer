"use strict";

// Chrome serializes this function before injecting it. Keep its dependencies inside it.
function collectRows(config) {
  if (location.origin !== config.origin) {
    throw new Error("Open Game History at the configured origin.");
  }

  function rendered(element) {
    const style = getComputedStyle(element);
    return element.getClientRects().length > 0 &&
      style.visibility === "visible" &&
      !element.closest('[hidden], [aria-hidden="true"]');
  }

  function cellText(row, selector) {
    const cells = [...row.querySelectorAll(selector)].filter(rendered);
    return cells.length === 1 ? cells[0].innerText.trim() : "";
  }

  // Validate all selectors even when there are no rows.
  document.querySelector(config.hand);
  document.querySelector(config.winner);
  const rows = [...document.querySelectorAll(config.row)].filter(rendered);
  const records = [];
  for (const row of rows) {
    const id = cellText(row, config.hand).toUpperCase()
      .replace(/^(?:POKER\s+HAND|HAND\s+ID|HAND)(?=[\s:#])\s*:?\s*/, "")
      .replace(/^#\s*/, "");
    const match = /^([A-Z]*)([0-9]+)$/.exec(id);
    const nickname = cellText(row, config.winner);
    if (match && nickname && !/[\x00-\x1f\x7f]/.test(nickname)) {
      records.push([match[1] + (match[2].replace(/^0+/, "") || "0"), nickname]);
    }
  }
  return { records, renderedRows: rows.length };
}
