// ============================================================================
// FILE: app/static/js/devices.js  -  DEVICES PAGE LOGIC
// ----------------------------------------------------------------------------
// Page: templates/devices.html   Needs: utils.js loaded first
//
// Flow (viewing):  page opens (optionally /devices?id=5) -> loop() -> refresh()
//                  -> GET /api/devices/, /api/alerts/, /api/scans/ (all at once)
//                  -> fillSelect() + renderDetail() + renderReport() -> repeat
// Flow (choosing): user picks a device in the drop-down -> renderDetail()
// Flow (renaming): user clicks "Rename" -> types a name -> saveName()
//                  -> PUT /api/devices/<id>/name (routes/devices.py rename_device)
//                  (the details panel isn't redrawn while the name is being typed)
//                  (the address bar changes to /devices?id=<n>)
// Links here come from dashboard carousel cards and the Security Alerts page.
// ============================================================================


// ============================================================================
// SETUP: SETTINGS, PAGE ELEMENTS, STATE AND LABELS
// ============================================================================

const DEFAULT_REFRESH_MS = 5000;   // used until the Settings value is read

const detailEl = document.getElementById("deviceDetail");
const selectEl = document.getElementById("deviceSelect");
const reportEl = document.getElementById("reportSummary");

let devices = [];
let alerts = [];
let scans = [];
let selectedId = new URLSearchParams(window.location.search).get("id");
let selectSignature = "";

// Plain-language names for alert severity
const SEVERITY_LABEL = { critical: "Urgent", high: "Important", medium: "Worth a look", low: "For your info" };
const SEVERITY_CLASS = {
    critical: "bg-red-500/20 text-red-300 border-red-500/30",
    high: "bg-orange-500/20 text-orange-300 border-orange-500/30",
    medium: "bg-yellow-500/20 text-yellow-300 border-yellow-500/30",
    low: "bg-blue-500/20 text-blue-300 border-blue-500/30",
};
const SEVERITY_RANK = { critical: 3, high: 2, medium: 1, low: 0 };

// ============================================================================
// GROUP 1: LOADING DATA
// ============================================================================

// getJson(): fetch one API address; on failure keep `fallback` (the data the
// page already has) so one failed request never blanks the page
async function getJson(url, fallback) {
    try {
        const res = await fetch(url);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return await res.json();
    } catch (err) {
        console.error(`Could not load ${url}:`, err);
        return fallback;
    }
}

// refresh(): load devices, alerts and scans together, choose which device to
// show, then redraw both panels
async function refresh() {
    [devices, alerts, scans] = await Promise.all([
        getJson("/api/devices/", devices),
        getJson("/api/alerts/", alerts),
        getJson("/api/scans/", scans),
    ]);

    // No ?id= in the link: show the first device so the page is never blank
    if (!devices.some(d => String(d.device_id) === String(selectedId)) && devices.length) {
        selectedId = String(devices[0].device_id);
    }

    fillSelect();
    if (renamingId === null) renderDetail();      // don't wipe a name being typed
    renderReport();
}

// ============================================================================
// GROUP 2: DEVICE PICKER (drop-down)
// Only rebuilt when the device list changes, so it stays open while used.
// ============================================================================

// deviceLabel(): text shown for a device in the drop-down: its name, plus the
// address when the name doesn't already contain it
function deviceLabel(d) {
    const name = (d.recognised ? "" : "NEW · ") + (d.hostname || "Unknown device");
    return d.device_ip && !name.includes(d.device_ip) ? `${name} (${d.device_ip})` : name;
}

// fillSelect(): rebuild the drop-down options if the device list changed
function fillSelect() {
    const signature = devices.map(d => `${d.device_id}:${deviceLabel(d)}`).join("|");
    if (signature !== selectSignature) {
        selectSignature = signature;
        selectEl.innerHTML = devices.length
            ? devices.map(d => `<option value="${esc(d.device_id)}">${esc(deviceLabel(d))}</option>`).join("")
            : `<option value="">No devices yet</option>`;
    }
    if (selectedId != null) selectEl.value = String(selectedId);
}

// Trigger: user picks another device -> update the address bar and redraw
selectEl.addEventListener("change", () => {
    selectedId = selectEl.value;
    renamingId = null;
    history.replaceState(null, "", `/devices?id=${encodeURIComponent(selectedId)}`);
    renderDetail();
});

// ============================================================================
// GROUP 3: DEVICE DETAILS PANEL (widget 1)
// ============================================================================

let renamingId = null;     // the device whose name is being typed (null = none)

// nameBlock(): the device's name with a "Rename" button, or the rename box.
// The automatic name (found by scans) is shown underneath when the user has
// chosen their own.
function nameBlock(d, name) {
    const auto = d.auto_name && d.custom_name ? `<p class="truncate text-xs text-slate-500">Found by scans as: ${esc(d.auto_name)}</p>` : "";
    if (String(renamingId) !== String(d.device_id)) {
        return `<div class="flex min-w-0 items-start gap-2">
                    <h4 class="min-w-0 break-words text-xl font-bold leading-tight text-white">${esc(name)}</h4>
                    <button type="button" data-rename class="shrink-0 rounded-lg border border-white/10 px-2 py-1 text-xs text-slate-300 hover:border-[#00F2FE]/40 hover:text-[#00F2FE]" title="Give this device your own name">
                        <span class="material-symbols-outlined align-middle text-sm">edit</span> Rename
                    </button>
                </div>${auto}`;
    }
    return `<form data-rename-form class="flex flex-col gap-2">
                <input data-rename-input type="text" maxlength="40" value="${esc(d.custom_name || name)}" aria-label="Device name"
                    class="w-full rounded-lg border border-white/10 bg-[#090D16] px-3 py-2 text-base font-semibold text-white focus:border-[#00F2FE] focus:outline-none focus:ring-1 focus:ring-[#00F2FE]">
                <div class="flex flex-wrap gap-2">
                    <button type="submit" class="rounded-lg bg-gradient-to-r from-[#00F2FE] to-[#8B5CF6] px-3 py-1.5 text-xs font-bold text-[#090D16]">Save</button>
                    <button type="button" data-rename-cancel class="rounded-lg border border-white/10 px-3 py-1.5 text-xs text-slate-300 hover:border-white/30">Cancel</button>
                    ${d.custom_name ? `<button type="button" data-rename-auto class="rounded-lg border border-white/10 px-3 py-1.5 text-xs text-slate-300 hover:border-white/30">Use the automatic name</button>` : ""}
                </div>
                <p data-rename-msg class="text-xs text-[#FFB4AB]"></p>
            </form>`;
}

// recognitionBlock(): NEW devices get an amber box with "I know this device";
// recognised ones get one quiet line saying who recognised them, with "Mark as unknown"
const RECOGNISED_BY = {
    learning: "during LanWatch's first day (learning period)",
    you: "by you",
    automatic: "automatically, after being seen on several days over a week",
};
function recognitionBlock(d) {
    if (!d.recognised) {
        return `
            <div class="rounded-lg border border-[#F59E0B]/40 bg-[#F59E0B]/10 p-4">
                <p class="font-semibold text-[#FDE68A]">New device</p>
                <p class="mt-1 text-sm text-[#FDE68A]/90">LanWatch first saw it ${esc(fmtTime(d.first_detected))}. If you or someone at home knows it,
                    mark it as recognised. If nobody does, change your Wi-Fi password so it can't connect again.</p>
                <button type="button" data-recognise="true"
                    class="mt-3 rounded-lg border border-[#10B981]/40 bg-[#10B981]/10 px-4 py-2 text-sm text-[#34D399] hover:bg-[#10B981]/20">I know this device</button>
                <p data-recognise-msg class="mt-2 text-xs text-[#FFB4AB]"></p>
            </div>`;
    }
    return `<p class="text-xs text-slate-500">Recognised ${esc(RECOGNISED_BY[d.recognised_by] || "")}${d.recognised_at ? " · " + esc(fmtTime(d.recognised_at)) : ""}
                <button type="button" data-recognise="false" class="ml-1 underline hover:text-slate-300">Mark as unknown</button>
                <span data-recognise-msg class="ml-1 text-[#FFB4AB]"></span></p>`;
}

// setRecognised(): Trigger "I know this device" / "Mark as unknown"
// -> PUT /api/devices/<id>/recognised (needs the admin password: admin_lock.js asks for it)
async function setRecognised(deviceId, value) {
    const send = () => fetch(`/api/devices/${encodeURIComponent(deviceId)}/recognised`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ recognised: value })
    });
    const msg = detailEl.querySelector("[data-recognise-msg]");
    try {
        let res = await send();
        if (res.status === 401) {
            const unlocked = window.AdminLock &&
                await AdminLock.unlock(value ? "Marking a device as recognised needs the admin password."
                                             : "Marking a device as unknown needs the admin password.");
            if (!unlocked) return;
            res = await send();
        }
        if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || "Couldn't save that.");
        await refresh();                      // the badge, the alert and the summary all change
    } catch (err) {
        if (msg) msg.textContent = err.message;
    }
}

// saveName(): Trigger "Save" / "Use the automatic name" -> PUT /api/devices/<id>/name
async function saveName(deviceId, name) {
    const msg = detailEl.querySelector("[data-rename-msg]");
    try {
        const res = await fetch(`/api/devices/${encodeURIComponent(deviceId)}/name`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name: name })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || "Couldn't save the name.");
        const d = devices.find(x => String(x.device_id) === String(deviceId));
        if (d) Object.assign(d, data);
        renamingId = null;
        selectSignature = "";              // the drop-down shows the new name too
        fillSelect();
        renderDetail();
    } catch (err) {
        if (msg) msg.textContent = err.message;
    }
}

// the Rename buttons and box (the panel is redrawn often, so one listener on the panel)
detailEl.addEventListener("click", event => {
    const recogniseBtn = event.target.closest("[data-recognise]");
    if (recogniseBtn) {
        setRecognised(selectedId, recogniseBtn.dataset.recognise === "true");
        return;
    }
    if (event.target.closest("[data-rename]")) {
        renamingId = selectedId;
        renderDetail();
        const input = detailEl.querySelector("[data-rename-input]");
        if (input) { input.focus(); input.select(); }
    } else if (event.target.closest("[data-rename-cancel]")) {
        renamingId = null;
        renderDetail();
    } else if (event.target.closest("[data-rename-auto]")) {
        saveName(selectedId, "");
    }
});
detailEl.addEventListener("submit", event => {
    if (!event.target.closest("[data-rename-form]")) return;
    event.preventDefault();
    saveName(selectedId, detailEl.querySelector("[data-rename-input]").value);
});
detailEl.addEventListener("keydown", event => {
    if (event.key === "Escape" && renamingId !== null) { renamingId = null; renderDetail(); }
});

// tile(): one big-number box (label + value); also used by the summary panel
function tile(label, value) {
    return `
        <div class="rounded-lg bg-[#090D16] p-4">
            <p class="text-xs text-slate-400">${label}</p>
            <p class="mt-1 font-mono text-lg font-bold text-white">${value}</p>
        </div>`;
}

// infoRow(): one "label ....... value" line
function infoRow(label, value) {
    return `
        <div class="flex items-start justify-between gap-4 border-b border-white/5 py-2 text-sm last:border-0">
            <span class="text-slate-400">${label}</span>
            <span class="min-w-0 text-right font-mono text-slate-200 [overflow-wrap:anywhere]">${value}</span>
        </div>`;
}

// worstSeverity(): the most serious severity in a list of alerts
function worstSeverity(list) {
    return list.reduce((worst, a) => (SEVERITY_RANK[a.severity] ?? 0) > (SEVERITY_RANK[worst] ?? -1) ? a.severity : worst, "low");
}

// verdict(): the coloured one-line status box (alerts / offline / all normal)
function verdict(d, deviceAlerts) {
    if (deviceAlerts.length) {
        const n = deviceAlerts.length;
        const sev = worstSeverity(deviceAlerts);
        const tone = SEVERITY_RANK[sev] >= 2 ? "bg-red-500/10 border-red-500/30 text-[#FFB4AB]" : "bg-yellow-500/10 border-yellow-500/30 text-yellow-200";
        return `<div class="rounded-lg border px-4 py-3 text-sm ${tone}">
            ${n === 1 ? "1 alert needs" : `${n} alerts need`} your attention on this device.</div>`;
    }
    if (d.status !== "active") {
        return `<div class="rounded-lg border border-white/10 bg-white/5 px-4 py-3 text-sm text-slate-300">
            This device is offline. It was last seen ${esc(fmtTime(d.last_detected))}.</div>`;
    }
    return `<div class="rounded-lg border border-[#10B981]/30 bg-[#10B981]/10 px-4 py-3 text-sm text-[#34D399]">
        Everything looks normal.</div>`;
}

// renderDetail(): draw everything about the selected device: name, online
// time, verdict, speed/data tiles, details, history and its newest alerts
function renderDetail() {
    const d = devices.find(x => String(x.device_id) === String(selectedId));

    if (!d) {
        detailEl.innerHTML = `
            <div class="py-10 text-center">
                <span class="material-symbols-outlined text-3xl text-[#414755]">devices</span>
                <p class="mt-2 text-sm text-slate-300">No devices found yet.</p>
                <p class="text-xs text-slate-500">Go to the dashboard and run a scan to find devices on your network.</p>
            </div>`;
        return;
    }

    const online = d.status === "active";
    const deviceAlerts = alerts.filter(a => String(a.device_id) === String(d.device_id));
    const name = d.hostname || "Unknown device";

    const alertList = deviceAlerts.length
        ? deviceAlerts.slice(0, 5).map(a => `
            <li class="flex items-start gap-3 rounded-lg bg-[#090D16] p-3">
                <span class="mt-0.5 inline-block shrink-0 rounded-full border px-2 py-0.5 text-xs ${SEVERITY_CLASS[a.severity] || SEVERITY_CLASS.low}">
                    ${esc(SEVERITY_LABEL[a.severity] || a.severity)}
                </span>
                <div class="min-w-0">
                    <p class="text-sm text-[#dae2fd]">${esc(a.message)}</p>
                    <p class="mt-0.5 font-mono text-xs text-slate-500">${esc(fmtTime(a.created_at))}</p>
                </div>
            </li>`).join("")
        : `<li class="rounded-lg bg-[#090D16] p-3 text-sm text-slate-400">No alerts for this device.</li>`;

    detailEl.innerHTML = `
        <div class="flex flex-col gap-5">
            <div class="flex items-center gap-4">
                <div class="flex h-14 w-14 shrink-0 items-center justify-center rounded-xl bg-[#00F2FE]/10 text-[#00F2FE]">
                    <span class="material-symbols-outlined text-3xl">${getDeviceIcon(d.hostname)}</span>
                </div>
                <div class="min-w-0 flex-1">
                    ${nameBlock(d, name)}
                    <p class="mt-1 flex items-center gap-2 text-sm">
                        <span class="inline-block h-2.5 w-2.5 rounded-full ${online ? "bg-[#10B981]" : "bg-slate-500"}"></span>
                        <span class="${online ? "text-[#34D399]" : "text-slate-400"}">
                            ${online ? `Online, connected for ${esc(fmtSince(d.connected_since, d.last_detected))}` : "Offline"}
                        </span>
                    </p>
                </div>
            </div>

            ${recognitionBlock(d)}

            ${verdict(d, deviceAlerts)}

            <div class="grid grid-cols-1 gap-3 sm:grid-cols-3">
                ${tile("Current speed", online ? esc(formatThroughput(d.throughput_bytes)) : "—")}
                ${tile("Total data used", esc(formatBytes(d.total_bytes ?? 0)))}
                ${tile("Packets sent and received", esc((d.total_packets ?? 0).toLocaleString()))}
            </div>

            <div class="grid grid-cols-1 gap-6 md:grid-cols-2">
                <div>
                    <h5 class="mb-1 text-sm font-semibold text-white">About this device</h5>
                    ${infoRow("Device type", esc(d.os_name || "Unknown"))}
                    ${infoRow("Manufacturer", esc(d.device_mac_vendor || "Unknown"))}
                    ${infoRow("Network address (IP)", esc(d.device_ip || "—"))}
                    ${infoRow("Device ID (MAC)", esc(d.device_mac || "—"))}
                </div>
                <div>
                    <h5 class="mb-1 text-sm font-semibold text-white">History</h5>
                    ${infoRow("First seen", esc(fmtTime(d.first_detected)))}
                    ${infoRow("Last seen", esc(fmtTime(d.last_detected)))}
                    ${infoRow("Connected since", online ? esc(fmtTime(d.connected_since)) : "—")}
                </div>
            </div>

            ${openPortsSection(d)}

            <div>
                <h5 class="mb-2 text-sm font-semibold text-white">Alerts for this device</h5>
                <ul class="flex flex-col gap-2">${alertList}</ul>
            </div>
        </div>`;
}

// openPortsSection(): the "Open ports" list from the device's latest FULL scan.
// d.open_ports comes from GET /api/devices/ (routes/devices.py _latest_open_ports):
//   null = no full scan has checked this device yet, [] = none open (good)
function openPortsSection(d) {
    const heading = `<h5 class="mb-2 text-sm font-semibold text-white">Open ports and services</h5>`;

    if (d.open_ports == null) {
        return `<div>${heading}
            <p class="rounded-lg bg-[#090D16] p-3 text-sm text-slate-400">
                No full scan has checked this device yet. Choose <b>Full</b> next to <b>Run Scan</b> on the dashboard to see its open ports.
            </p></div>`;
    }

    const checked = `<p class="mt-2 font-mono text-xs text-slate-500">From the full scan finished ${esc(fmtTime(d.last_full_scan))}</p>`;

    if (!d.open_ports.length) {
        return `<div>${heading}
            <p class="rounded-lg bg-[#10B981]/10 p-3 text-sm text-[#34D399]">
                No open ports found. Nothing on this device is accepting connections from the network, which is good.
            </p>${checked}</div>`;
    }

    // riskiest first, so anything worth acting on is at the top
    const ports = d.open_ports.slice().sort((a, b) =>
        (PORT_RISK[b.risk]?.rank ?? 0) - (PORT_RISK[a.risk]?.rank ?? 0) || a.port - b.port);

    const cards = ports.map(p => {
        const risk = PORT_RISK[p.risk] || PORT_RISK.low;
        const program = [p.product, p.version].filter(Boolean).join(" ");
        return `
            <li class="rounded-lg bg-[#090D16] p-3">
                <div class="flex flex-wrap items-center gap-2">
                    <span class="font-semibold text-white">${esc(p.name || p.service || "Unknown service")}</span>
                    <span class="font-mono text-xs text-[#00F2FE]">port ${esc(p.port)}/${esc(p.protocol)}</span>
                    <span class="rounded-full border px-2 py-0.5 text-[11px] ${risk.cls}">${risk.label}</span>
                    ${p.is_new ? `<span class="rounded-full border border-[#F59E0B]/40 bg-[#F59E0B]/15 px-2 py-0.5 font-mono text-[10px] font-bold uppercase tracking-wider text-[#FBBF24]" title="Open now, but not in this device's earlier full scans">New</span>` : ""}
                </div>
                ${p.what ? `<p class="mt-1 text-sm text-slate-300">${esc(p.what)}</p>` : ""}
                ${p.why ? `<p class="mt-1 text-sm text-slate-400"><span class="text-slate-500">Why it matters:</span> ${esc(p.why)}</p>` : ""}
                ${program ? `<p class="mt-1 font-mono text-xs text-slate-500">Program reported by the scan: ${esc(program)}</p>` : ""}
                ${window.AiExplain ? AiExplain.slot("port", d.device_id, p.port, "Explain more") : ""}
            </li>`;
    }).join("");

    const concerns = ports.filter(p => p.risk === "medium" || p.risk === "high").length;
    return `<div>${heading}
        <p class="mb-2 text-xs text-slate-400">${ports.length} open port${ports.length === 1 ? "" : "s"}: each one is a "door" that other devices on your network can connect to.
            ${concerns ? `<span class="text-yellow-300">${concerns} worth a look.</span> See <a href="/advice" class="text-[#00F2FE] hover:underline">Security Advice</a> for what to do.` : "Nothing here needs attention."}</p>
        <ul class="flex flex-col gap-2">${cards}</ul>
        ${checked}</div>`;
}

// how each port risk level (from the knowledge base) is shown
const PORT_RISK = {
    high:   { rank: 3, label: "High risk", cls: "bg-orange-500/20 text-orange-300 border-orange-500/30" },
    medium: { rank: 2, label: "Worth checking", cls: "bg-yellow-500/20 text-yellow-300 border-yellow-500/30" },
    low:    { rank: 1, label: "Low risk", cls: "bg-blue-500/20 text-blue-300 border-blue-500/30" },
    info:   { rank: 0, label: "Normal", cls: "bg-[#10B981]/15 text-[#34D399] border-[#10B981]/30" },
};

// ============================================================================
// GROUP 4: NETWORK SUMMARY PANEL (widget 2)
// All numbers come from the same three API calls as the details panel.
// ============================================================================

// renderReport(): totals, highlights (busiest / longest online) and latest scan
function renderReport() {
    if (!devices.length) {
        reportEl.innerHTML = `<p class="py-8 text-center text-sm text-slate-400">Nothing to report yet. Run a scan from the dashboard first.</p>`;
        return;
    }

    const online = devices.filter(d => d.status === "active");
    const offline = devices.length - online.length;
    const dayAgo = Date.now() - 24 * 60 * 60 * 1000;
    const newToday = devices.filter(d => d.first_detected && parseUtc(d.first_detected).getTime() >= dayAgo);
    const urgent = alerts.filter(a => (SEVERITY_RANK[a.severity] ?? 0) >= 2).length;
    const totalBytes = devices.reduce((sum, d) => sum + (d.total_bytes || 0), 0);
    const totalPackets = devices.reduce((sum, d) => sum + (d.total_packets || 0), 0);

    const busiest = devices.reduce((top, d) => (d.total_bytes || 0) > (top?.total_bytes || 0) ? d : top, null);
    const longest = online
        .filter(d => typeof d.connected_since === "string")
        .sort((a, b) => parseUtc(a.connected_since) - parseUtc(b.connected_since))[0];
    const lastScan = scans[0];

    const headline = alerts.length
        ? `${online.length} of ${devices.length} devices are online, and ${alerts.length === 1 ? "1 alert needs" : `${alerts.length} alerts need`} your attention.`
        : `${online.length} of ${devices.length} devices are online. No unusual activity has been detected.`;

    reportEl.innerHTML = `
        <div class="flex flex-col gap-5">
            <p class="text-sm text-slate-200">${esc(headline)}</p>

            <div class="grid grid-cols-2 gap-3 lg:grid-cols-4">
                ${tile("Devices found", esc(devices.length))}
                ${tile("Online now", esc(online.length))}
                ${tile("Offline", esc(offline))}
                ${tile("New in the last 24 hours", esc(newToday.length))}
                ${tile("Open alerts", esc(alerts.length))}
                ${tile("Urgent or important", esc(urgent))}
                ${tile("Total data used", esc(formatBytes(totalBytes)))}
                ${tile("Packets handled", esc(totalPackets.toLocaleString()))}
            </div>

            <div class="grid grid-cols-1 gap-6 md:grid-cols-2">
                <div>
                    <h5 class="mb-1 text-sm font-semibold text-white">Highlights</h5>
                    ${infoRow("Most data used by", busiest && busiest.total_bytes ? esc(`${busiest.hostname || busiest.device_ip} (${formatBytes(busiest.total_bytes)})`) : "—")}
                    ${infoRow("Online the longest", longest ? esc(`${longest.hostname || longest.device_ip} (${fmtSince(longest.connected_since, longest.last_detected)})`) : "—")}
                </div>
                <div>
                    <h5 class="mb-1 text-sm font-semibold text-white">Latest scan</h5>
                    ${infoRow("Network scanned", lastScan ? esc(lastScan.network_range || "—") : "—")}
                    ${infoRow("Finished", lastScan ? esc(fmtTime(lastScan.end_time)) : "—")}
                    ${infoRow("Result", lastScan ? esc(lastScan.results_summary || "—") : "—")}
                </div>
            </div>
        </div>`;
}

// ============================================================================
// GROUP 5: START-UP AND AUTO-REFRESH
// ============================================================================

// loop(): refresh, then wait for the "Dashboard refresh" time from System
// Settings (utils.js getRefreshMs) and repeat. Waits for each round to finish.
async function loop() {
    await refresh();
    setTimeout(loop, await getRefreshMs(DEFAULT_REFRESH_MS));
}
loop();