// ============================================================================
// FILE: app/static/js/scans_history.js  -  SCAN HISTORY PAGE LOGIC
// ----------------------------------------------------------------------------
// Page: templates/scans_history.html   (self-contained: does not need utils.js)
//
// Flow (loading):   page opens -> init() -> load() -> GET /api/scans/
//                   (routes/scans.py get_scans) -> normalise() each scan
//                   -> renderAll() = renderStats() + renderTable()
//                   -> repeats every 30 s while the tab is visible
// Flow (searching): user types / changes a filter / sort / page size
//                   -> filtered() -> renderTable()
// Flow (details):   user clicks "Details" -> the hidden row under it opens
// Flow (export):    user clicks "Export CSV" -> exportCsv()
//
// Optional globals a template can set before this file loads:
//   window.LANWATCH_SCANS_API     -> endpoint URL (default "/api/scans/")
//   window.LANWATCH_INITIAL_SCANS -> scans rendered by Flask (fallback data)
//
// Wrapped in (function () { ... })() so its variables stay private.
// ============================================================================
(function () {
    "use strict";

    // ========================================================================
    // SETUP: SETTINGS AND PAGE STATE
    // ========================================================================
    const API_URL = window.LANWATCH_SCANS_API || "/api/scans/";
    const INITIAL = Array.isArray(window.LANWATCH_INITIAL_SCANS) ? window.LANWATCH_INITIAL_SCANS : [];
    const AUTO_REFRESH_MS = 30000;

    const state = { all: [], page: 1, timer: null };
    const $ = (id) => document.getElementById(id);   // shortcut: find an element by id

    // ========================================================================
    // GROUP 1: TIDYING SCAN RECORDS
    // Tolerates different field names, so API changes don't break the page.
    // ========================================================================

    // pick(): the first of several possible field names that has a value
    function pick(o, keys, fallback) {
        for (const k of keys) {
            if (o[k] !== undefined && o[k] !== null && o[k] !== "") return o[k];
        }
        return fallback;
    }

    // toDate(): database time text (UTC) or a number -> Date
    function toDate(v) {
        if (v === null || v === undefined || v === "") return null;
        if (typeof v === "string") {
            // Backend stores "YYYY-MM-DD HH:MM:SS" in UTC (see parseUtc in dashboard.js)
            if (/^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/.test(v)) {
                const d = new Date(v.replace(" ", "T") + "Z");
                return isNaN(d) ? null : d;
            }
        }
        // Numbers below 1e12 are treated as Unix seconds, otherwise milliseconds
        const d = new Date(typeof v === "number" && v < 1e12 ? v * 1000 : v);
        return isNaN(d) ? null : d;
    }

    // normalise(): turn one API scan into {id, subnet, status, duration,
    // summary, devices, start, end}; devices is read from the summary text
    // (e.g. "12 devices discovered") when the API doesn't send a count
    function normalise(r) {
        const start = toDate(pick(r, ["start_time", "started_at", "start", "created_at"], null));
        const end = toDate(pick(r, ["end_time", "ended_at", "end", "finished_at"], null));

        let dur = pick(r, ["duration_seconds", "duration", "elapsed"], null);
        if (typeof dur === "string" && /^\d+(\.\d+)?$/.test(dur)) dur = parseFloat(dur);
        if (dur === null && start && end) dur = (end - start) / 1000;

        const summary = pick(r, ["results_summary", "result_summary", "summary", "result", "message"], "");

        let devices = pick(r, ["devices_found", "device_count", "hosts_up"], null);
        if (devices === null) {
            const text = String(summary);
            const m = text.match(/(\d+)\s+(?:active\s+|new\s+)?(?:devices?|hosts?)/i) ||
                      text.match(/(?:found|discovered)\s+(\d+)/i);
            devices = m ? parseInt(m[1], 10) : null;
        }

        return {
            id: pick(r, ["scan_id", "id"], "—"),
            type: pick(r, ["scan_type", "type"], "quick"),
            subnet: pick(r, ["network_range", "subnet", "target", "network", "cidr"], "—"),
            status: String(pick(r, ["scan_status", "status", "state"], "unknown")).toLowerCase(),
            duration: dur,
            summary: summary,
            devices: devices,
            start: start,
            end: end
        };
    }

    // ========================================================================
    // GROUP 2: FORMATTING
    // ========================================================================

    // fmtDur(): seconds -> "41.0s" or "2m 5s"
    function fmtDur(s) {
        if (typeof s === "string") return s || "—";
        if (typeof s !== "number" || isNaN(s)) return "—";
        if (s < 60) return s.toFixed(1) + "s";
        const m = Math.floor(s / 60);
        const sec = Math.round(s % 60);
        return m + "m " + sec + "s";
    }

    // fmtDate(): Date -> local date and time text
    function fmtDate(d) {
        return d ? d.toLocaleString() : "—";
    }

    // esc(): make text safe to put into the page
    function esc(s) {
        return String(s).replace(/[&<>"']/g, function (c) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
        });
    }

    // statusGroup(): sort any status into completed / running / failed / other
    function statusGroup(s) {
        if (/(complete|success|done|finished)/.test(s)) return "completed";
        if (/(run|progress|pending|start)/.test(s)) return "running";
        if (/(fail|error|cancel|timeout|abort)/.test(s)) return "failed";
        return "other";
    }

    // badge(): coloured status pill (running ones get a pulsing dot)
    function badge(s) {
        const g = statusGroup(s);
        const styles = {
            completed: "border-[#10B981]/30 bg-[#10B981]/15 text-[#34D399]",
            running: "border-[#00F2FE]/30 bg-[#00F2FE]/10 text-[#00F2FE]",
            failed: "border-[#EF4444]/40 bg-[#EF4444]/15 text-[#FFB4AB]",
            other: "border-white/10 bg-white/5 text-slate-300"
        }[g];
        const dot = g === "running"
            ? '<span class="mr-1.5 inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-[#00F2FE]"></span>'
            : "";
        return '<span class="inline-flex items-center rounded-full border px-2.5 py-0.5 font-mono text-xs capitalize ' +
            styles + '">' + dot + esc(s) + "</span>";
    }

    // ========================================================================
    // GROUP 3: SEARCH, FILTER AND SORT
    // ========================================================================

    // filtered(): scans matching the search box and status filter, in the
    // chosen order. A number in the search box matches a scan ID exactly.
    function filtered() {
        const q = ($("searchInput") && $("searchInput").value || "").trim().toLowerCase();
        const f = ($("statusFilter") && $("statusFilter").value) || "all";
        const sort = ($("sortSelect") && $("sortSelect").value) || "newest";


        const isNumeric = /^\d+$/.test(q);
        const targetId = isNumeric ? parseInt(q, 10) : null;

        let rows = state.all.filter(function (r) {
            // Status filter
            if (f !== "all") {
                const group = statusGroup(r.status || "");
                if (group !== f) return false;
            }

            // Search
            if (!q) return true;

            if (isNumeric) {
                return Number(r.id) === targetId;
            }

            return [r.id, r.subnet, r.summary, r.status]
                .join(" ")
                .toLowerCase()
                .indexOf(q) !== -1;
        });

        

        // Sorting
        const t = function (r) { return r.start ? r.start.getTime() : 0; };
        rows.sort(function (a, b) {
            if (sort === "oldest")  return t(a) - t(b);
            if (sort === "longest") return (b.duration || 0) - (a.duration || 0);
            if (sort === "devices") return (b.devices || 0) - (a.devices || 0);
            return t(b) - t(a);
        });

        return rows;
    }

    // ========================================================================
    // GROUP 4: DRAWING THE PAGE
    // ========================================================================

    // renderStats(): the four cards: total, success rate, average time, peak devices
    function renderStats() {
        const rows = state.all;
        const done = rows.filter(function (r) { return statusGroup(r.status) === "completed"; });
        const bad = rows.filter(function (r) { return statusGroup(r.status) === "failed"; });
        const finished = done.length + bad.length;

        $("statTotal").textContent = rows.length;
        $("totalBadge").textContent = rows.length + (rows.length === 1 ? " scan" : " scans");
        $("statSuccess").textContent = finished ? Math.round((done.length / finished) * 100) + "%" : "—";
        $("statFailed").textContent = bad.length;

        const durs = done.map(function (r) { return r.duration; })
            .filter(function (d) { return typeof d === "number" && !isNaN(d); });
        $("statAvg").textContent = durs.length
            ? fmtDur(durs.reduce(function (a, b) { return a + b; }, 0) / durs.length)
            : "—";

        const peaks = rows.map(function (r) { return r.devices; })
            .filter(function (d) { return typeof d === "number"; });
        $("statPeak").textContent = peaks.length ? Math.max.apply(null, peaks) : "—";
    }

    // typeBadge(): small "Quick" / "Full" label shown next to the scan ID
    function typeBadge(type) {
        return type === "full"
            ? ' <span class="ml-1 rounded-full border border-[#8B5CF6]/30 bg-[#8B5CF6]/10 px-1.5 py-0.5 text-[10px] text-[#C4ABFF]">Full</span>'
            : ' <span class="ml-1 rounded-full border border-white/10 px-1.5 py-0.5 text-[10px] text-slate-400">Quick</span>';
    }

    // rowHtml(): one table row plus its hidden "Details" row
    function rowHtml(r, idx) {
        return (
            '<tr class="hover:bg-white/5 transition-colors">' +
                '<td class="px-5 py-3.5 font-mono text-slate-300 whitespace-nowrap">#' + esc(r.id) + typeBadge(r.type) + "</td>" +
                '<td class="px-5 py-3.5 font-mono text-[#00F2FE]">' + esc(r.subnet) + "</td>" +
                '<td class="px-5 py-3.5">' + badge(r.status) + "</td>" +
                '<td class="px-5 py-3.5 font-mono text-slate-200">' + fmtDur(r.duration) + "</td>" +
                '<td class="px-5 py-3.5 text-slate-300 max-w-xs truncate" title="' + esc(r.summary) + '">' +
                    (esc(r.summary) || '<span class="text-slate-500">—</span>') + "</td>" +
                '<td class="px-5 py-3.5 font-mono text-xs text-slate-400 whitespace-nowrap">' + fmtDate(r.start) + "</td>" +
                '<td class="px-5 py-3.5 font-mono text-xs text-slate-400 whitespace-nowrap">' + fmtDate(r.end) + "</td>" +
                '<td class="px-5 py-3.5 text-right">' +
                    '<button type="button" data-idx="' + idx + '" aria-expanded="false" ' +
                    'class="detailBtn rounded-lg border border-white/10 px-3 py-1 font-mono text-xs text-[#849495] hover:border-[#00F2FE]/40 hover:text-[#00F2FE] transition-all">Details</button>' +
                "</td>" +
            "</tr>" +
            '<tr class="hidden" data-detail-for="' + idx + '">' +
                '<td colspan="8" class="bg-[#090D16]/60 px-5 py-4">' +
                    '<dl class="grid grid-cols-1 gap-x-8 gap-y-3 font-mono text-xs sm:grid-cols-2 lg:grid-cols-4">' +
                        '<div><dt class="text-slate-500">Scan ID</dt><dd class="text-slate-200">#' + esc(r.id) + "</dd></div>" +
                        '<div><dt class="text-slate-500">Scan type</dt><dd class="text-slate-200">' + (r.type === "full" ? "Full (devices + open ports)" : "Quick (devices)") + "</dd></div>" +
                        '<div><dt class="text-slate-500">Target subnet</dt><dd class="text-[#00F2FE]">' + esc(r.subnet) + "</dd></div>" +
                        '<div><dt class="text-slate-500">Devices found</dt><dd class="text-slate-200">' + (r.devices === null ? "—" : r.devices) + "</dd></div>" +
                        '<div><dt class="text-slate-500">Duration</dt><dd class="text-slate-200">' + fmtDur(r.duration) + "</dd></div>" +
                        '<div class="sm:col-span-2"><dt class="text-slate-500">Started</dt><dd class="text-slate-200">' + fmtDate(r.start) + "</dd></div>" +
                        '<div class="sm:col-span-2"><dt class="text-slate-500">Finished</dt><dd class="text-slate-200">' + fmtDate(r.end) + "</dd></div>" +
                        '<div class="sm:col-span-4"><dt class="text-slate-500">Result summary</dt><dd class="whitespace-pre-wrap text-slate-300">' +
                            (esc(r.summary) || "No summary recorded.") + "</dd></div>" +
                    "</dl>" +
                "</td>" +
            "</tr>"
        );
    }

    // renderTable(): draw the current page of filtered scans and the page controls
    function renderTable() {
        const rows = filtered();
        const size = parseInt($("pageSize").value, 10) || 25;
        const pages = Math.max(1, Math.ceil(rows.length / size));
        state.page = Math.min(Math.max(state.page, 1), pages);

        const startIdx = (state.page - 1) * size;
        const slice = rows.slice(startIdx, startIdx + size);

        $("scanCount").textContent = rows.length + (rows.length === 1 ? " Scan" : " Scans");
        $("pageNum").textContent = state.page + " / " + pages;
        $("pageInfo").textContent = rows.length
            ? "Showing " + (startIdx + 1) + "–" + (startIdx + slice.length) + " of " + rows.length
            : "Showing 0–0 of 0";
        $("prevPage").disabled = state.page <= 1;
        $("nextPage").disabled = state.page >= pages;

        const body = $("scanHistoryBody");
        if (!slice.length) {
            const msg = state.all.length
                ? "No scans match your filters. Clear the search or choose a different status."
                : "No scans recorded yet. Go to the Dashboard and run your first scan.";
            body.innerHTML = '<tr><td colspan="8" class="px-5 py-10 text-center font-mono text-slate-400">' + msg + "</td></tr>";
            return;
        }

        body.innerHTML = slice.map(function (r, i) {
            return rowHtml(r, startIdx + i);
        }).join("");
    }

    // renderAll(): cards + table + "last refreshed" time
    function renderAll() {
        renderStats();
        renderTable();
        $("refreshedAt").textContent = new Date().toLocaleTimeString();
    }

    // showError(): message in the table when the history can't be loaded
    function showError(message) {
        $("scanHistoryBody").innerHTML =
            '<tr><td colspan="8" class="px-5 py-10 text-center font-mono text-[#FFB4AB]">' +
            "Couldn't load scan history (" + esc(message) + "). Check that " + esc(API_URL) +
            " is available, then press Refresh.</td></tr>";
    }

    // ========================================================================
    // GROUP 5: LOADING DATA
    // ========================================================================

    // load(): fetch GET /api/scans/ and redraw, re-opening any "Details" rows
    // that were open so the auto-refresh doesn't collapse them
    async function load() {
        const btn = $("refreshBtn");
        btn.disabled = true;

        // Remember which detail rows are open so auto-refresh doesn't collapse them
        const openIds = Array.prototype.map.call(
            document.querySelectorAll("[data-detail-for]:not(.hidden)"),
            function (el) { return el.getAttribute("data-detail-for"); }
        );

        try {
            const res = await fetch(API_URL, { headers: { Accept: "application/json" }, cache: "no-store" });
            if (!res.ok) throw new Error("HTTP " + res.status);
            const data = await res.json();
            const list = Array.isArray(data) ? data : (data.scans || data.history || data.items || []);
            state.all = list.map(normalise);
        } catch (err) {
            if (!state.all.length && INITIAL.length) {
                state.all = INITIAL.map(normalise);
            } else if (!state.all.length) {
                showError(err.message);
                btn.disabled = false;
                return;
            }
        }

        btn.disabled = false;
        renderAll();

        openIds.forEach(function (id) {
            const row = document.querySelector('[data-detail-for="' + id + '"]');
            const toggle = document.querySelector('.detailBtn[data-idx="' + id + '"]');
            if (row && toggle) {
                row.classList.remove("hidden");
                toggle.setAttribute("aria-expanded", "true");
                toggle.textContent = "Hide";
            }
        });
    }

    // ========================================================================
    // GROUP 6: CSV EXPORT
    // ========================================================================

    // csvCell(): quote one spreadsheet cell
    function csvCell(v) {
        let s = String(v === null || v === undefined ? "" : v);
        // Guard against spreadsheet formula injection
        if (/^[=+\-@]/.test(s)) s = "'" + s;
        return '"' + s.replace(/"/g, '""') + '"';
    }

    // exportCsv(): download the filtered history as a .csv file
    function exportCsv() {
        const rows = filtered();
        const head = ["ID", "Subnet", "Status", "Duration (s)", "Devices", "Summary", "Start", "End"];
        const lines = [head.map(csvCell).join(",")].concat(rows.map(function (r) {
            return [
                r.id, r.subnet, r.status,
                typeof r.duration === "number" ? r.duration.toFixed(1) : "",
                r.devices === null ? "" : r.devices,
                r.summary,
                r.start ? r.start.toISOString() : "",
                r.end ? r.end.toISOString() : ""
            ].map(csvCell).join(",");
        }));

        const blob = new Blob(["\ufeff" + lines.join("\n")], { type: "text/csv;charset=utf-8" });
        const a = document.createElement("a");
        const stamp = new Date().toISOString().slice(0, 10);
        a.href = URL.createObjectURL(blob);
        a.download = "lanwatch-scan-history-" + stamp + ".csv";
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
    }

    // ========================================================================
    // GROUP 7: START-UP AND USER ACTIONS
    // ========================================================================

    // debounce(): wait until the user pauses typing before running `fn`
    function debounce(fn, ms) {
        let t;
        return function () {
            clearTimeout(t);
            t = setTimeout(fn, ms);
        };
    }

    // init(): connect the search box, filters, paging, Refresh, Export and
    // Details buttons, then load and start the 30-second auto-refresh
    function init() {
        // scan_indicator.js fires this when a background scan ends: reload now
        // instead of waiting up to 30 s for the next auto-refresh
        document.addEventListener("lanwatch:scan-finished", load);

        $("searchInput").addEventListener("input", debounce(function () {
            state.page = 1;
            renderTable();
        }, 150));

        ["statusFilter", "sortSelect", "pageSize"].forEach(function (id) {
            $(id).addEventListener("change", function () {
                state.page = 1;
                renderTable();
            });
        });

        $("prevPage").addEventListener("click", function () { state.page--; renderTable(); });
        $("nextPage").addEventListener("click", function () { state.page++; renderTable(); });
        $("refreshBtn").addEventListener("click", load);
        $("exportBtn").addEventListener("click", exportCsv);

        $("scanHistoryBody").addEventListener("click", function (e) {
            const btn = e.target.closest(".detailBtn");
            if (!btn) return;
            const row = document.querySelector('[data-detail-for="' + btn.dataset.idx + '"]');
            if (!row) return;
            const open = row.classList.toggle("hidden") === false;
            btn.setAttribute("aria-expanded", String(open));
            btn.textContent = open ? "Hide" : "Details";
        });

        const clock = $("datetime");
        if (clock) clock.textContent = new Date().toLocaleString();

        load();
        state.timer = setInterval(function () {
            if (document.visibilityState === "visible") load();
        }, AUTO_REFRESH_MS);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();