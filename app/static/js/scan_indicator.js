// ============================================================================
// FILE: app/static/js/scan_indicator.js  -  SHARED SCAN INDICATOR (every page)
// ----------------------------------------------------------------------------
// Scans run in a background thread on the server, so they carry on no matter
// which page is open. This script shows that running scan in the top header
// of EVERY page, so the "Scanning..." state never disappears when you move
// around the app.
//
// Flow: page opens -> startIndicator() -> checkScan() every few seconds
//       -> GET /api/scans/active (routes/scans.py get_active_scan)
//       -> scan running  : show "Scanning <network> · <time>" pill
//       -> scan finished : show the result for a few seconds, then hide,
//                          and fire the "lanwatch:scan-finished" event so the
//                          open page can refresh its own tables
//
// Self-contained (does not need utils.js). Loaded with "defer" in each
// template's <head>, so it runs once the page has been built.
// ============================================================================

(function () {

    // ========================================================================
    // SETUP: SETTINGS AND STATE
    // ========================================================================
    const ACTIVE_POLL_MS = 2000;    // check every 2 s while a scan runs
    const IDLE_POLL_MS = 5000;      // check every 5 s when nothing is running
    const RESULT_SHOW_MS = 6000;    // how long "Scan complete" stays visible

    let pill = null;                // the header element this script draws into
    let watchingScanId = null;      // the scan currently on show (null = none)


    // ========================================================================
    // GROUP 1: DRAWING THE INDICATOR
    // ========================================================================

    // parseUtc(): database time text (UTC) -> Date
    function parseUtc(ts) {
        return new Date(String(ts).replace(" ", "T") + "Z");
    }

    // fmtElapsed(): seconds -> "45s" or "2m 05s"
    function fmtElapsed(seconds) {
        seconds = Math.max(0, Math.round(seconds));
        if (seconds < 60) return `${seconds}s`;
        return `${Math.floor(seconds / 60)}m ${String(seconds % 60).padStart(2, "0")}s`;
    }

    // esc(): make text safe to put into the page
    function esc(text) {
        const div = document.createElement("div");
        div.textContent = text == null ? "" : String(text);
        return div.innerHTML;
    }

    // createPill(): add the (hidden) indicator to the right of the top header.
    // Clicking it opens the dashboard, where the full scan progress is shown.
    function createPill() {
        const header = document.querySelector("header");
        if (!header) return null;

        const el = document.createElement("a");
        el.id = "scanIndicator";
        el.href = "/";
        el.title = "A network scan is running in the background";
        el.style.display = "none";
        el.className = "flex items-center gap-2 rounded-full border px-3 py-1 font-mono text-xs";
        header.appendChild(el);
        return el;
    }

    // showRunning(): pulsing dot + network + elapsed time
    function showRunning(scan) {
        const elapsed = scan.start_time ? (Date.now() - parseUtc(scan.start_time).getTime()) / 1000 : 0;
        pill.className = "flex items-center gap-2 rounded-full border border-[#adc6ff]/30 bg-[#adc6ff]/10 px-3 py-1 font-mono text-xs text-[#adc6ff]";
        pill.innerHTML =
            `<span class="relative flex h-2 w-2">
                <span class="absolute inline-flex h-full w-full animate-ping rounded-full bg-[#adc6ff] opacity-75"></span>
                <span class="relative inline-flex h-2 w-2 rounded-full bg-[#adc6ff]"></span>
             </span>
             <span>${scan.scan_type === "full" ? "Full scan" : "Scanning"} ${esc(scan.network_range || "")} · ${fmtElapsed(elapsed)}</span>`;
        pill.style.display = "";
    }

    // showResult(): green "complete" or red "failed" for a few seconds, then hide
    function showResult(scan) {
        const ok = scan.scan_status === "completed";
        pill.className = ok
            ? "flex items-center gap-2 rounded-full border border-[#10B981]/30 bg-[#10B981]/10 px-3 py-1 font-mono text-xs text-[#10B981]"
            : "flex items-center gap-2 rounded-full border border-[#EF4444]/30 bg-[#EF4444]/10 px-3 py-1 font-mono text-xs text-[#F87171]";
        pill.innerHTML = `<span>${ok ? "Scan complete" : "Scan failed"} · ${esc(scan.results_summary || "")}</span>`;
        pill.style.display = "";
        setTimeout(() => { if (watchingScanId === null) pill.style.display = "none"; }, RESULT_SHOW_MS);
    }


    // ========================================================================
    // GROUP 2: CHECKING THE SERVER
    // ========================================================================

    // checkScan(): ask the server what is running and update the pill.
    // Returns how long to wait before checking again.
    async function checkScan() {
        try {
            const response = await fetch("/api/scans/active");
            if (!response.ok) return IDLE_POLL_MS;
            const { scan } = await response.json();

            if (scan) {
                watchingScanId = scan.scan_id;
                showRunning(scan);
                return ACTIVE_POLL_MS;
            }

            // nothing running now - if we were showing one, it has just ended
            if (watchingScanId !== null) {
                const finishedId = watchingScanId;
                watchingScanId = null;
                const res = await fetch(`/api/scans/${finishedId}`);
                if (res.ok) {
                    const finished = await res.json();
                    showResult(finished);
                    // let the open page refresh its own tables
                    document.dispatchEvent(new CustomEvent("lanwatch:scan-finished", { detail: finished }));
                } else {
                    pill.style.display = "none";
                }
            }
        } catch (error) {
            console.error("Scan indicator check failed:", error);
        }
        return IDLE_POLL_MS;
    }

    // startIndicator(): build the pill and keep checking (paused while the
    // browser tab is hidden, to save work)
    async function startIndicator() {
        pill = createPill();
        if (!pill) return;

        while (true) {
            const waitMs = document.hidden ? IDLE_POLL_MS : await checkScan();
            await new Promise(resolve => setTimeout(resolve, waitMs));
        }
    }

    startIndicator();
})();
