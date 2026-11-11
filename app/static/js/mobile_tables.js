// ============================================================================
// FILE: app/static/js/mobile_tables.js  -  TABLES AS CARDS ON A PHONE
// ----------------------------------------------------------------------------
// Loaded by base.html on every page.
// A wide table squeezed onto a phone screen is hard to read, so on screens
// narrower than 768 px every table marked class="mobile-cards" is shown as a
// list of small cards instead: one card per row, each value on its own line
// with its column name in front of it (css/style.css, "TABLES AS CARDS").
// On a computer the table looks exactly as before.
//
// This script only copies each column's heading onto that column's cells
// (data-label="Status"), which the CSS shows in front of the value. The rows
// are redrawn by the page scripts (dashboard.js, scans_history.js), so it
// watches each table and labels new rows as they appear.
// ============================================================================

(function () {
    "use strict";

    // labelTable(): give every cell the heading of its column
    function labelTable(table) {
        const headings = [...table.querySelectorAll("thead th")].map(th => th.textContent.trim());
        table.querySelectorAll("tbody tr").forEach(tr => {
            [...tr.children].forEach((td, i) => {
                if (td.colSpan > 1) return;                 // "no data" messages and detail rows
                const label = headings[i] || "";
                if (!label || i === 0) return;              // the first value is the card's heading
                if (td.dataset.label !== label) td.dataset.label = label;
                // put the value in one box, so the name and the value sit side by side
                // (the existing elements are moved, not copied: their click handlers stay)
                if (!td.querySelector(":scope > .mc-value")) {
                    const box = document.createElement("div");
                    box.className = "mc-value";
                    while (td.firstChild) box.appendChild(td.firstChild);
                    td.appendChild(box);
                }
            });
        });
    }

    function watch(table) {
        labelTable(table);
        const body = table.querySelector("tbody");
        if (body) new MutationObserver(() => labelTable(table)).observe(body, { childList: true });
    }

    function start() { document.querySelectorAll("table.mobile-cards").forEach(watch); }
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
    else start();
})();
