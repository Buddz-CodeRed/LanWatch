// ============================================================================
// FILE: app/static/js/reports.js  -  REPORTS PAGE LOGIC
// ----------------------------------------------------------------------------
// Page: templates/reports.html   (self-contained: does not need utils.js)
//
// Flow (summary):  page opens -> init() -> loadData()
//                  -> GET /api/devices/, /api/alerts/, /api/scans/, /api/usage/total
//                  -> generate() -> reportOverview() -> render() draws 4 cards
//                  user changes the period -> generate() redraws instantly
// Flow (Generate): user clicks "Generate" -> loadData() -> generate()
//                  -> generateAiReport() (AI report: PLANNED FEATURE, shows a
//                     placeholder until AI_ENABLED is true and the server has
//                     POST /api/reports/generate)
// Flow (export):   "CSV" -> exportCsv()   "Print" -> printWidget()
//
// Wrapped in (function () { ... })() so its variables stay private.
// ============================================================================

(function () {
    "use strict";

    // ========================================================================
    // SETUP: API ADDRESSES, TIME PERIODS AND PAGE STATE
    // ========================================================================

    const ENDPOINTS = {
        devices: "/api/devices/",
        alerts: "/api/alerts/",
        scans: "/api/scans/",
        usage: "/api/usage/total"
    };

    const PERIODS = {
        "24h": { ms: 24 * 3600 * 1000, label: "Last 24 hours" },
        "7d": { ms: 7 * 24 * 3600 * 1000, label: "Last 7 days" },
        "30d": { ms: 30 * 24 * 3600 * 1000, label: "Last 30 days" },
        "all": { ms: null, label: "All time" }
    };

    const data = { devices: [], alerts: [], scans: [], usage: null };
    let current = null; // the last generated report (used for CSV export)

    const $ = (id) => document.getElementById(id);

    // ========================================================================
    // GROUP 1: HELPERS
    // ========================================================================

    // esc(): make text safe to put into the page
    function esc(s) {
        return String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, function (c) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
        });
    }

    // toDate(): database time text (UTC) -> Date.
    // Backend timestamps look like "YYYY-MM-DD HH:MM:SS" and are stored in UTC
    function toDate(v) {
        if (v === null || v === undefined || v === "") return null;
        if (typeof v === "string" && /^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/.test(v)) {
            const d = new Date(v.replace(" ", "T") + "Z");
            return isNaN(d) ? null : d;
        }
        const d = new Date(typeof v === "number" && v < 1e12 ? v * 1000 : v);
        return isNaN(d) ? null : d;
    }

    // fmtBytes(): 1536000 -> "1.5 MB"
    function fmtBytes(b) {
        if (b === null || b === undefined || isNaN(b)) return "—";
        if (b >= 1073741824) return (b / 1073741824).toFixed(2) + " GB";
        if (b >= 1048576) return (b / 1048576).toFixed(1) + " MB";
        if (b >= 1024) return (b / 1024).toFixed(1) + " KB";
        return b + " B";
    }

    // fmtRate(): bytes per second -> "1.5 MB/s"
    function fmtRate(b) {
        if (!b) return "0 B/s";
        return fmtBytes(b) + "/s";
    }

    // statusGroup(): sort any scan status into completed / running / failed / other
    function statusGroup(s) {
        s = String(s || "").toLowerCase();
        if (/(complete|success|done|finished)/.test(s)) return "completed";
        if (/(run|progress|pending|start)/.test(s)) return "running";
        if (/(fail|error|cancel|timeout|abort)/.test(s)) return "failed";
        return "other";
    }

    // inPeriod(): is `date` inside the chosen period (24h / 7d / 30d / all)?
    function inPeriod(date, periodKey) {
        const p = PERIODS[periodKey];
        if (!p || p.ms === null) return true;
        if (!date) return false;
        return date.getTime() >= Date.now() - p.ms;
    }

    // deviceTraffic(): all data a device has used (total_bytes from /api/devices/)
    function deviceTraffic(d) {
        return typeof d.total_bytes === "number" ? d.total_bytes : 0;
    }

    // ========================================================================
    // GROUP 2: BUILDING THE SUMMARY
    // ========================================================================
    // The summary returns: { title, cards: [{label, value, note, color, icon}] }

    // reportOverview(): count devices, alerts, scans and live traffic for the
    // chosen period and return them as four summary cards
    function reportOverview(period) {
        const devices = data.devices.filter(function (d) { return inPeriod(toDate(d.last_detected), period); });
        const alerts = data.alerts.filter(function (a) { return inPeriod(toDate(a.created_at), period); });
        const scans = data.scans.filter(function (s) { return inPeriod(toDate(s.start_time), period); });

        const active = devices.filter(function (d) { return String(d.status).toLowerCase() === "active"; });
        const failed = scans.filter(function (s) { return statusGroup(s.scan_status) === "failed"; });
        const critical = alerts.filter(function (a) { return String(a.severity).toLowerCase() === "critical"; });

        const cards = [
            { label: "Devices seen", value: devices.length, note: active.length + " active now", color: "cyan", icon: "devices" },
            { label: "Security alerts", value: alerts.length, note: critical.length + " critical", color: "red", icon: "warning" },
            { label: "Scans run", value: scans.length, note: failed.length + " failed", color: "violet", icon: "manage_search" },
            {
                label: "Live traffic",
                value: data.usage && typeof data.usage.live_throughput_bytes === "number" ? fmtRate(data.usage.live_throughput_bytes) : "—",
                note: "Right now, whole network", color: "emerald", icon: "monitoring"
            }
        ];

        return {
            title: "Overall Network Summary",
            cards: cards
        };
    }

    // ========================================================================
    // GROUP 3: DRAWING THE SUMMARY
    // ========================================================================

    // colour themes for the summary cards
    const CARD_COLORS = {
        cyan: { text: "text-[#00F2FE]", value: "text-white", hover: "hover:border-[#00F2FE]/40" },
        emerald: { text: "text-[#10B981]", value: "text-[#34D399]", hover: "hover:border-[#10B981]/40" },
        violet: { text: "text-[#8B5CF6]", value: "text-[#C4ABFF]", hover: "hover:border-[#8B5CF6]/40" },
        red: { text: "text-[#EF4444]", value: "text-[#FFB4AB]", hover: "hover:border-[#EF4444]/40" }
    };

    // cardHtml(): HTML for one summary card
    function cardHtml(c) {
        const col = CARD_COLORS[c.color] || CARD_COLORS.cyan;
        return (
            '<div class="rounded-xl border border-white/10 bg-[#0F172A]/70 backdrop-blur-md p-5 transition-all ' + col.hover + '">' +
                '<div class="mb-2 flex items-center justify-between text-slate-400">' +
                    '<span class="font-mono text-xs uppercase tracking-wider">' + esc(c.label) + "</span>" +
                    '<span class="material-symbols-outlined text-lg ' + col.text + '">' + esc(c.icon) + "</span>" +
                "</div>" +
                '<p class="font-mono text-3xl font-bold ' + col.value + '">' + esc(c.value) + "</p>" +
                '<p class="mt-2 border-t border-white/5 pt-2 font-mono text-xs text-slate-400">' + esc(c.note) + "</p>" +
            "</div>"
        );
    }

    // render(): show the cards and the "period · generated at" subtitle,
    // and remember the report for CSV export
    function render(report, periodKey) {
        const generated = new Date();
        report.meta = { periodLabel: PERIODS[periodKey].label, generated: generated };

        $("reportSubtitle").textContent = report.meta.periodLabel + " · Generated " + generated.toLocaleString();
        $("reportBody").innerHTML =
            '<div class="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">' + report.cards.map(cardHtml).join("") + "</div>";

        current = report;
    }

    // currentPeriod(): the period chosen in the drop-down (default 7 days)
    function currentPeriod() {
        return PERIODS[$("periodSelect").value] ? $("periodSelect").value : "7d";
    }

    // generate(): rebuild and redraw the summary from the loaded data
    function generate() {
        const period = currentPeriod();
        render(reportOverview(period), period);
    }

    // ========================================================================
    // GROUP 4: LOADING DATA
    // ========================================================================

    // getJson(): fetch one API address, throwing on an error status
    async function getJson(url) {
        const res = await fetch(url, { headers: { Accept: "application/json" }, cache: "no-store" });
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
    }

    // loadData(): fetch all four APIs at once; show a warning bar listing any
    // that failed (their part of the report is left empty)
    async function loadData() {
        const keys = ["devices", "alerts", "scans", "usage"];
        const labels = { devices: "devices", alerts: "alerts", scans: "scan history", usage: "traffic totals" };
        const results = await Promise.allSettled(keys.map(function (k) { return getJson(ENDPOINTS[k]); }));

        const failed = [];
        results.forEach(function (r, i) {
            const k = keys[i];
            if (r.status === "fulfilled") {
                data[k] = k === "usage" ? r.value : (Array.isArray(r.value) ? r.value : []);
            } else {
                data[k] = k === "usage" ? null : [];
                failed.push(labels[k]);
            }
        });

        const warn = $("reportWarning");
        if (failed.length) {
            warn.textContent = "Couldn't load " + failed.join(", ") + ". Those parts of the report will be empty. Check that the server is running, then press Generate to try again.";
            warn.classList.remove("hidden");
        } else {
            warn.classList.add("hidden");
        }

        $("loadedAt").textContent = new Date().toLocaleTimeString();
    }

    // ========================================================================
    // GROUP 5: CSV EXPORT
    // ========================================================================

    // csvCell(): quote one spreadsheet cell, defusing cells that start with = + - @
    function csvCell(v) {
        let s = String(v === null || v === undefined ? "" : v);
        if (/^[=+\-@]/.test(s)) s = "'" + s; // guard against spreadsheet formula injection
        return '"' + s.replace(/"/g, '""') + '"';
    }

    // exportCsv(): download the current summary as a .csv file
    function exportCsv() {
        if (!current) return;
        const lines = [
            [csvCell("LanWatch: " + current.title)],
            [csvCell("Period"), csvCell(current.meta.periodLabel)],
            [csvCell("Generated"), csvCell(current.meta.generated.toLocaleString())],
            []
        ].map(function (l) { return l.join(","); });

        current.cards.forEach(function (c) {
            lines.push([csvCell(c.label), csvCell(c.value), csvCell(c.note)].join(","));
        });


        const blob = new Blob(["\ufeff" + lines.join("\n")], { type: "text/csv;charset=utf-8" });
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = "lanwatch-network-summary-" + new Date().toISOString().slice(0, 10) + ".csv";
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
    }

    // ========================================================================
    // GROUP 6: AI REPORT (local AI)
    // loadAiStatus() asks GET /api/ai/status (routes/ai.py) whether the AI is
    // switched on and installed. If so, "Generate" also sends the report data
    // to POST /api/reports/generate (routes/ai.py generate_report), which
    // answers {"report": "..."} written by services/ai_translator.py.
    // ========================================================================

    let AI_ENABLED = false;   // set by loadAiStatus()
    let aiOffReason = "";     // why not, shown in the placeholder
    const AI_ENDPOINT = "/api/reports/generate";
    const AI_TIMEOUT_MS = 120000;
    const AI_MAX_ROWS = 100; // keep the request small and the report focused
    const AI_TYPES = {
        overview: "Overall Network Summary",
        devices: "Device Summary",
        security: "Security Report",
        history: "History Report"
    };
    let aiText = "";

    // slimDevice() / slimAlert() / slimScan(): trimmed copies of each record,
    // so the AI request stays small and only contains what the report needs
    function slimDevice(d) {
        const a = toDate(d.connected_since), b = toDate(d.last_detected);
        return {
            ip: d.device_ip, hostname: d.hostname || null, os: d.os_name || null, mac: d.device_mac,
            status: d.status,
            connected_for_minutes: String(d.status).toLowerCase() === "active" && a && b ? Math.max(0, Math.round((b - a) / 60000)) : null,
            last_seen: b ? b.toISOString() : null,
            total_bytes: typeof d.total_bytes === "number" ? d.total_bytes : null,
            current_bytes_per_sec: typeof d.throughput_bytes === "number" ? Math.round(d.throughput_bytes) : null
        };
    }
    function slimAlert(a) {
        const t = toDate(a.created_at);
        return { severity: a.severity, hostname: a.hostname || null, ip: a.device_ip || null, mac: a.device_mac || null, message: a.message, detected: t ? t.toISOString() : null };
    }
    function slimScan(s) {
        const t = toDate(s.start_time);
        return { id: s.scan_id, subnet: s.network_range, status: s.scan_status, duration_seconds: s.duration_seconds, summary: s.results_summary || null, started: t ? t.toISOString() : null };
    }

    // buildPayload(): the data sent to the AI for the chosen report type and period
    function buildPayload(type, period) {
        const devices = data.devices.filter(function (d) { return inPeriod(toDate(d.last_detected), period); });
        const alerts = data.alerts.filter(function (a) { return inPeriod(toDate(a.created_at), period); })
            .sort(function (a, b) { return (toDate(b.created_at) || 0) - (toDate(a.created_at) || 0); });
        const scans = data.scans.filter(function (s) { return inPeriod(toDate(s.start_time), period); })
            .sort(function (a, b) { return (toDate(b.start_time) || 0) - (toDate(a.start_time) || 0); });

        const bySeverity = {};
        alerts.forEach(function (a) {
            const k = String(a.severity || "unknown").toLowerCase();
            bySeverity[k] = (bySeverity[k] || 0) + 1;
        });

        const payload = {
            report_type: type,
            period: PERIODS[period].label,
            generated_at: new Date().toISOString(),
            totals: {
                devices_seen: devices.length,
                devices_active: devices.filter(function (d) { return String(d.status).toLowerCase() === "active"; }).length,
                alerts: alerts.length,
                alerts_by_severity: bySeverity,
                scans: scans.length,
                scans_failed: scans.filter(function (s) { return statusGroup(s.scan_status) === "failed"; }).length,
                live_throughput_bytes_per_sec: data.usage && typeof data.usage.live_throughput_bytes === "number" ? data.usage.live_throughput_bytes : null
            }
        };

        const byTraffic = devices.slice().sort(function (a, b) { return deviceTraffic(b) - deviceTraffic(a); });
        if (type === "devices") {
            payload.devices = devices.slice(0, AI_MAX_ROWS).map(slimDevice);
        } else if (type === "security") {
            payload.alerts = alerts.slice(0, AI_MAX_ROWS).map(slimAlert);
            payload.devices_with_traffic = byTraffic.slice(0, 10).map(slimDevice);
        } else if (type === "history") {
            payload.scans = scans.slice(0, AI_MAX_ROWS).map(slimScan);
        } else {
            payload.busiest_devices = byTraffic.slice(0, 10).map(slimDevice);
            payload.recent_alerts = alerts.slice(0, 10).map(slimAlert);
            payload.recent_scans = scans.slice(0, 10).map(slimScan);
        }
        return payload;
    }

    // mdToHtml(): turn the AI's reply into safe HTML.
    // Minimal, safe markdown: headings, bullets, numbered lists, **bold**, `code`. Text is escaped first.
    function mdToHtml(src) {
        const lines = String(src).replace(/\r/g, "").split("\n");
        let html = "", list = null, para = [];
        const inline = function (t) {
            return esc(t)
                .replace(/\*\*(.+?)\*\*/g, '<strong class="font-semibold text-white">$1</strong>')
                .replace(/`([^`]+)`/g, '<code class="rounded bg-white/10 px-1 font-mono text-xs">$1</code>');
        };
        function flushPara() {
            if (para.length) { html += '<p class="text-sm leading-relaxed text-slate-300">' + inline(para.join(" ")) + "</p>"; para = []; }
        }
        function closeList() { if (list) { html += "</" + list + ">"; list = null; } }
        function openList(tag) {
            if (list !== tag) {
                closeList();
                html += "<" + tag + ' class="' + (tag === "ul" ? "list-disc" : "list-decimal") + ' space-y-1.5 pl-5 text-sm leading-relaxed text-slate-300">';
                list = tag;
            }
        }
        lines.forEach(function (raw) {
            const t = raw.trim();
            if (!t) { flushPara(); closeList(); return; }
            const h = t.match(/^(#{1,4})\s+(.*)$/);
            if (h) {
                flushPara(); closeList();
                const big = h[1].length <= 2;
                html += "<h4 class=\"" + (big ? "mt-2 text-base font-bold text-white" : "mt-1 text-sm font-semibold text-[#00F2FE]") + "\">" + inline(h[2]) + "</h4>";
                return;
            }
            const ul = t.match(/^[-*\u2022]\s+(.*)$/);
            const ol = t.match(/^\d+[.)]\s+(.*)$/);
            if (ul || ol) { flushPara(); openList(ul ? "ul" : "ol"); html += "<li>" + inline((ul || ol)[1]) + "</li>"; return; }
            closeList();
            para.push(t);
        });
        flushPara(); closeList();
        return '<div class="flex flex-col gap-3">' + html + "</div>";
    }

    // setAiActions(): show/hide the Copy and Print buttons
    function setAiActions(show) {
        const el = $("aiActions");
        if (show) { el.classList.remove("hidden"); el.classList.add("flex"); }
        else { el.classList.remove("flex"); el.classList.add("hidden"); }
    }

    // showAiLoading() / showAiError() / showAiReport() / showAiPlaceholder():
    // the four states of the AI panel
    function showAiLoading(typeLabel) {
        aiText = "";
        setAiActions(false);
        $("aiTitle").textContent = "AI " + typeLabel;
        $("aiSubtitle").textContent = "Writing your report…";
        $("aiReportBody").innerHTML =
            '<div class="flex flex-col items-center gap-3 py-10 text-center">' +
                '<span class="material-symbols-outlined animate-spin text-3xl text-[#8B5CF6]">progress_activity</span>' +
                '<p class="text-sm text-slate-300">The AI is reading your network data. This can take up to a minute.</p>' +
            "</div>";
    }

    function showAiError(message) {
        aiText = "";
        setAiActions(false);
        $("aiSubtitle").textContent = "The report couldn't be generated";
        $("aiReportBody").innerHTML =
            '<div class="flex flex-col items-center gap-3 py-8 text-center">' +
                '<span class="material-symbols-outlined text-3xl text-[#EF4444]">error</span>' +
                '<p class="max-w-xl text-sm text-[#FFB4AB]">' + esc(message) + "</p>" +
                '<p class="text-xs text-slate-400">Press Generate to try again.</p>' +
            "</div>";
    }

    function showAiReport(text, typeLabel, periodLabel) {
        aiText = text;
        $("aiTitle").textContent = "AI " + typeLabel;
        $("aiSubtitle").textContent = periodLabel + " · Generated " + new Date().toLocaleString();
        $("aiReportBody").innerHTML = mdToHtml(text) +
            '<p class="mt-6 border-t border-white/5 pt-3 font-mono text-xs text-slate-500">Written by AI from the data above. Check important findings before acting on them.</p>';
        setAiActions(true);
    }

    // loadAiStatus(): GET /api/ai/status -> AI_ENABLED and the badge
    async function loadAiStatus() {
        try {
            const res = await fetch("/api/ai/status");
            const status = res.ok ? await res.json() : {};
            AI_ENABLED = !!status.ready;
            aiOffReason = !status.enabled ? "The AI assistant is switched off in System Settings."
                : !status.available ? "The AI model isn't installed on this computer." : "";
        } catch (e) {
            AI_ENABLED = false;
            aiOffReason = "Couldn't reach LanWatch to check the AI.";
        }
        if ($("aiBadge")) $("aiBadge").classList.toggle("hidden", AI_ENABLED);
    }

    function showAiPlaceholder(typeLabel) {
        aiText = "";
        setAiActions(false);
        $("aiTitle").textContent = "AI Network Report";
        $("aiSubtitle").textContent = "Not available";
        $("aiReportBody").innerHTML =
            '<div class="flex flex-col items-center gap-3 py-8 text-center">' +
                '<span class="material-symbols-outlined text-4xl text-[#8B5CF6]/60">smart_toy</span>' +
                '<p class="max-w-lg text-sm text-slate-300">' + esc(aiOffReason || "The AI isn't available.") +
                ' When it is, your <strong class="font-semibold text-white">' + esc(typeLabel) + "</strong> will be written here.</p>" +
                '<p class="text-xs text-slate-500">The summary above is up to date and doesn\'t need the AI.</p>' +
            "</div>";
    }

    // generateAiReport(): send the payload to the server and show the reply
    // (or the "not connected yet" placeholder while AI_ENABLED is false)
    async function generateAiReport() {
        const type = AI_TYPES[$("reportType").value] ? $("reportType").value : "overview";
        const period = currentPeriod();
        if (!AI_ENABLED) { showAiPlaceholder(AI_TYPES[type]); return; }
        showAiLoading(AI_TYPES[type]);

        const controller = new AbortController();
        const timer = setTimeout(function () { controller.abort(); }, AI_TIMEOUT_MS);
        try {
            const res = await fetch(AI_ENDPOINT, {
                method: "POST",
                headers: { "Content-Type": "application/json", Accept: "application/json" },
                body: JSON.stringify({ report_type: type, period: period, payload: buildPayload(type, period) }),
                signal: controller.signal
            });
            let body = null;
            try { body = await res.json(); } catch (e) { /* non-JSON response */ }

            if (res.status === 404) throw new Error("The AI report service wasn't found (" + AI_ENDPOINT + "). It needs to be added to the server.");
            if (!res.ok) throw new Error((body && body.error) || "The server returned an error (HTTP " + res.status + ").");

            const text = body && (body.report || body.text);
            if (!text) throw new Error("The AI returned an empty report.");
            showAiReport(String(text), AI_TYPES[type], PERIODS[period].label);
        } catch (err) {
            if (err && err.name === "AbortError") showAiError("The report took too long and was stopped. Try a shorter period.");
            else if (err instanceof TypeError) showAiError("Couldn't reach the server. Check that LanWatch is running.");
            else showAiError(err.message || "Something went wrong.");
        } finally {
            clearTimeout(timer);
        }
    }

    // copyAiReport(): copy the AI report text to the clipboard
    function copyAiReport() {
        if (!aiText) return;
        const btn = $("aiCopyBtn");
        const done = function () {
            const old = btn.innerHTML;
            btn.innerHTML = '<span class="material-symbols-outlined text-base">check</span> Copied';
            setTimeout(function () { btn.innerHTML = old; }, 1500);
        };
        if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(aiText).then(done, function () {});
    }

    // printWidget(): print only one panel ("summary" or "ai"), using print CSS
    function printWidget(which) {
        const cls = "print-" + which;
        const cleanup = function () { document.body.classList.remove(cls); window.removeEventListener("afterprint", cleanup); };
        document.body.classList.add(cls);
        window.addEventListener("afterprint", cleanup);
        window.print();
    }

    // ========================================================================
    // GROUP 7: START-UP AND BUTTONS
    // ========================================================================

    // setBusy(): disable the Generate button while working
    function setBusy(busy) {
        const btn = $("generateBtn");
        btn.disabled = busy;
        btn.textContent = busy ? "Generating…" : "Generate";
    }

    // init(): connect every button, then load data and draw the first summary
    function init() {
        const clock = $("datetime");
        if (clock) clock.textContent = new Date().toLocaleString();

        // Generate: refresh data, update the summary, then ask the AI for the chosen report
        $("generateBtn").addEventListener("click", async function () {
            setBusy(true);
            try {
                await loadData();
                generate();
                await loadAiStatus();       // the AI may have been switched on/off meanwhile
                await generateAiReport();
            } finally {
                setBusy(false);
            }
        });

        // Changing the period refreshes the summary; the AI report only changes when Generate is pressed
        $("periodSelect").addEventListener("change", generate);

        $("csvBtn").addEventListener("click", exportCsv);
        $("printBtn").addEventListener("click", function () { printWidget("summary"); });
        $("aiPrintBtn").addEventListener("click", function () { printWidget("ai"); });
        $("aiCopyBtn").addEventListener("click", copyAiReport);

        loadAiStatus();
        loadData().then(generate);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();