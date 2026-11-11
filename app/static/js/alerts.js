// ============================================================================
// FILE: app/static/js/alerts.js  -  SECURITY ALERTS PAGE LOGIC
// ----------------------------------------------------------------------------
// Page: templates/alerts.html   Needs: utils.js loaded first
//
// Flow (viewing):   page opens -> loop() -> refresh() -> GET /api/alerts/
//                   (routes/alerts.py get_alerts) -> render() draws the filter
//                   buttons + one card per alert -> repeat on a timer
// Flow (filtering): user clicks a filter button -> activeFilter -> render()
// Flow (resolving): user clicks "Mark as resolved"
//                   -> POST /api/alerts/<id>/resolve (routes/alerts.py resolve_alert)
//                   -> if locked: admin_lock.js asks for the admin password, then retries
//                   -> the card is removed
// Flow (explaining): user clicks "Explain this" on a card -> ai_explain.js
//                   -> POST /api/ai/explain {"kind": "alert"} (routes/ai.py)
// Alerts themselves are created by services/network_monitor.py.
// ============================================================================


// ============================================================================
// SETUP: SETTINGS, PAGE ELEMENTS AND LABELS
// ============================================================================

const DEFAULT_REFRESH_MS = 5000;   // used until the Settings value is read
const MAX_SHOWN = 50;

const listEl = document.getElementById("alertList");
const filtersEl = document.getElementById("alertFilters");
const countEl = document.getElementById("alertCount");

let alerts = [];
let activeFilter = "all";
let lastSignature = "";

// Plain-language severity names, badge colours and filter buttons
const SEVERITY_LABEL = { critical: "Urgent", high: "Important", medium: "Worth a look", low: "For your info" };
const SEVERITY_CLASS = {
    critical: "bg-red-500/20 text-red-300 border-red-500/30",
    high: "bg-orange-500/20 text-orange-300 border-orange-500/30",
    medium: "bg-yellow-500/20 text-yellow-300 border-yellow-500/30",
    low: "bg-blue-500/20 text-blue-300 border-blue-500/30",
};
const FILTERS = [
    { key: "all", label: "All" },
    { key: "critical", label: "Urgent" },
    { key: "high", label: "Important" },
    { key: "medium", label: "Worth a look" },
];

// ============================================================================
// GROUP 1: PLAIN-LANGUAGE WORDING
// ============================================================================

// describe(): turn a technical alert (e.g. bandwidth_spike_out) into a title and
// a short explanation. Unknown alert types fall back to the raw message.
function describe(a) {
    const type = a.alert_type || "";
    const msg = a.message || "";
    const outbound = type.endsWith("_out") || /outbound/i.test(msg);
    const inbound = type.endsWith("_in") || /inbound/i.test(msg);

    if (type === "new_device") {
        return {
            title: "New device joined your network",
            meaning: "LanWatch hasn't seen this device before. If you recognise it, open it and choose \"I know this device\". If nobody at home does, change your Wi-Fi password.",
        };
    }
    if (type === "new_open_port") {
        return {
            title: "New open port on a device",
            meaning: "This device started accepting connections on a port it didn't use before. Check whether someone changed its settings or installed something new, and see Security Advice.",
        };
    }
    if (type.startsWith("packet_spike")) {
        return {
            title: "Unusually busy network activity",
            meaning: "This device is sending or receiving far more small messages than normal. Software updates can cause this, but it can also point to a problem.",
        };
    }
    if (type.startsWith("bandwidth_spike") || /traffic spike/i.test(msg)) {
        if (outbound) {
            return {
                title: "Sending much more data than usual",
                meaning: "This device is uploading far more than it normally does. If you aren't backing up files or uploading something big, it is worth checking.",
            };
        }
        if (inbound) {
            return {
                title: "Receiving much more data than usual",
                meaning: "This device is downloading far more than it normally does. A large download or update is the usual reason.",
            };
        }
    }
    return { title: msg || "Unusual activity detected", meaning: "" };
}

// timeAgo(): "just now", "5 min ago", "3 h ago", "2 d ago"
function timeAgo(ts) {
    if (!ts) return "—";
    const seconds = (Date.now() - parseUtc(ts).getTime()) / 1000;
    if (isNaN(seconds)) return "—";
    if (seconds < 60) return "just now";
    if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
    return `${Math.floor(seconds / 86400)} d ago`;
}

// ============================================================================
// GROUP 2: DRAWING THE PAGE
// ============================================================================

// renderFilters(): the All / Urgent / Important / Worth a look buttons, with counts
function renderFilters() {
    const counts = { all: alerts.length, critical: 0, high: 0, medium: 0 };
    alerts.forEach(a => { if (a.severity in counts) counts[a.severity]++; });

    filtersEl.innerHTML = FILTERS.map(f => {
        const on = f.key === activeFilter;
        const cls = on
            ? "border-[#00F2FE]/50 bg-[#00F2FE]/10 text-[#00F2FE]"
            : "border-white/10 text-slate-400 hover:text-white";
        return `<button type="button" data-filter="${f.key}" class="rounded-full border px-4 py-1.5 text-sm transition-colors ${cls}">
            ${f.label} <span class="font-mono text-xs">${counts[f.key]}</span></button>`;
    }).join("");
}

// alertCard(): HTML for one alert: severity, title, device link, time,
// explanation, technical details and the "Mark as resolved" button
function alertCard(a) {
    const info = describe(a);
    const sev = SEVERITY_CLASS[a.severity] ? a.severity : "low";
    const deviceName = a.hostname || a.device_ip || "Unknown device";
    const deviceLink = a.device_id != null
        ? `<a href="/devices?id=${encodeURIComponent(a.device_id)}" class="text-[#00F2FE] hover:underline">${esc(deviceName)}</a>`
        : esc(deviceName);

    return `
    <article class="rounded-lg border border-white/10 bg-[#090D16] p-4">
        <div class="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div class="flex min-w-0 flex-col items-start gap-2 sm:flex-row sm:gap-3">
                <span class="mt-0.5 inline-block shrink-0 rounded-full border px-2.5 py-0.5 text-xs ${SEVERITY_CLASS[sev]}">
                    ${esc(SEVERITY_LABEL[sev])}
                </span>
                <div class="min-w-0">
                    <h4 class="font-semibold text-white">${esc(info.title)}</h4>
                    <p class="mt-0.5 text-sm text-slate-400">
                        ${deviceLink}
                        <span class="text-slate-600"> · </span>
                        <span title="${esc(fmtTime(a.created_at))}">${esc(timeAgo(a.created_at))}</span>
                    </p>
                    ${info.meaning ? `<p class="mt-2 text-sm text-slate-300">${esc(info.meaning)}</p>` : ""}
                    <details class="mt-2 text-xs text-slate-500">
                        <summary class="cursor-pointer hover:text-slate-300">Technical details</summary>
                        <p class="mt-1 font-mono break-words">${esc(a.message)}</p>
                        <p class="mt-1 font-mono">${esc(a.device_ip || "")} ${esc(a.device_mac || "")}</p>
                    </details>
                    ${window.AiExplain ? AiExplain.slot("alert", a.alert_id, null, "Explain this") : ""}
                </div>
            </div>
            <button type="button" data-resolve="${esc(a.alert_id)}"
                class="shrink-0 rounded-lg border border-white/10 px-4 py-2 text-sm text-slate-200 transition-colors hover:border-[#10B981]/50 hover:text-[#34D399] disabled:cursor-not-allowed disabled:opacity-50">
                Mark as resolved
            </button>
        </div>
    </article>`;
}

// render(): redraw the list for the current filter. Skips the redraw when
// nothing changed (unless force) so open "Technical details" stay open.
function render(force = false) {
    countEl.textContent = `${alerts.length} alert${alerts.length === 1 ? "" : "s"}`;

    // Skip the redraw when nothing changed, so open "Technical details" stay open
    const signature = alerts.map(a => a.alert_id).join(",") + "|" + activeFilter;
    if (!force && signature === lastSignature) return;
    lastSignature = signature;

    renderFilters();

    const visible = alerts.filter(a => activeFilter === "all" || a.severity === activeFilter);

    if (!alerts.length) {
        listEl.innerHTML = `
            <div class="py-10 text-center">
                <span class="material-symbols-outlined text-3xl text-[#10B981]">check_circle</span>
                <p class="mt-2 text-sm text-slate-200">No alerts. Everything on your network looks normal.</p>
            </div>`;
        return;
    }
    if (!visible.length) {
        listEl.innerHTML = `<p class="py-8 text-center text-sm text-slate-400">No alerts in this group.</p>`;
        return;
    }

    listEl.innerHTML = visible.slice(0, MAX_SHOWN).map(alertCard).join("")
        + (visible.length > MAX_SHOWN
            ? `<p class="pt-1 text-center font-mono text-xs text-slate-500">Showing the newest ${MAX_SHOWN} of ${visible.length} alerts.</p>`
            : "");
}

// ============================================================================
// GROUP 3: USER ACTIONS
// ============================================================================

// Trigger: user clicks a filter button -> show only that severity
filtersEl.addEventListener("click", event => {
    const btn = event.target.closest("[data-filter]");
    if (!btn) return;
    activeFilter = btn.dataset.filter;
    render(true);
});

// Trigger: user clicks "Mark as resolved"
// -> POST /api/alerts/<id>/resolve -> remove the card (or show "Try again")
listEl.addEventListener("click", async event => {
    const btn = event.target.closest("[data-resolve]");
    if (!btn) return;

    const id = btn.dataset.resolve;
    btn.disabled = true;
    btn.textContent = "Saving…";

    try {
        let res = await fetch(`/api/alerts/${encodeURIComponent(id)}/resolve`, { method: "POST" });

        // locked: ask for the admin password (admin_lock.js), then try once more
        if (res.status === 401) {
            const unlocked = window.AdminLock &&
                await AdminLock.unlock("Marking an alert as resolved needs the admin password.");
            if (!unlocked) {
                btn.disabled = false;
                btn.textContent = "Mark as resolved";
                return;
            }
            res = await fetch(`/api/alerts/${encodeURIComponent(id)}/resolve`, { method: "POST" });
        }
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        alerts = alerts.filter(a => String(a.alert_id) !== String(id));
        render(true);
    } catch (err) {
        console.error("Could not resolve alert:", err);
        btn.disabled = false;
        btn.textContent = "Try again";
    }
});

// ============================================================================
// GROUP 4: LOADING AND AUTO-REFRESH
// ============================================================================

// refresh(): fetch unresolved alerts (GET /api/alerts/) and redraw
async function refresh() {
    try {
        const res = await fetch("/api/alerts/");
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        alerts = await res.json();
        render();
    } catch (err) {
        console.error("Could not load alerts:", err);
        if (!alerts.length) {
            listEl.innerHTML = `<p class="py-8 text-center text-sm text-slate-400">Could not load alerts. Check that LanWatch is running, then refresh the page.</p>`;
        }
    }
}

// loop(): refresh, then wait for the "Dashboard refresh" time from System
// Settings (utils.js getRefreshMs) and repeat. Waits for each round to finish.
async function loop() {
    await refresh();
    setTimeout(loop, await getRefreshMs(DEFAULT_REFRESH_MS));
}
loop();