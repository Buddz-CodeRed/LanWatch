// ============================================================================
// FILE: app/static/js/dashboard.js  -  DASHBOARD PAGE LOGIC
// ----------------------------------------------------------------------------
// Page: templates/dashboard.html   Needs: utils.js loaded first
//
// What the user sees, and where it comes from:
//   Scan box + "Run Scan"      -> COMPONENT 1 -> POST /api/scans/, GET /api/scans/<id>
//   Summary cards              -> COMPONENTS 2, 3, 4, 6
//   Active Network Fleet       -> COMPONENT 3 (carousel)  -> GET /api/devices/
//   Discovered devices table   -> COMPONENT 2             -> GET /api/devices/
//   Scan history table         -> COMPONENT 4             -> GET /api/scans/
//   Alerts table               -> COMPONENT 5             -> GET /api/alerts/
//   Wi-Fi badge (header)       -> COMPONENT 7             -> GET /api/network/info
//   Data usage card            -> COMPONENT 6             -> GET /api/usage/total
//   System warning bar         -> COMPONENT 10            -> GET /api/network/system-check
//   Security overview          -> COMPONENT 9             -> GET /api/findings/,
//                                                            GET /api/reports/latest-summary
//   Auto-refresh               -> COMPONENT 8 (speed from System Settings)
// ============================================================================


// ============================================================================
// SETUP: PAGE ELEMENTS AND SETTINGS
// Every element this script updates, found once by its id in dashboard.html.
// ============================================================================

const scanButton = document.getElementById("scanButton");
const scanStatus = document.getElementById("scanStatus");
const networkInput = document.getElementById("networkInput");
const scanTypeSelect = document.getElementById("scanType");     // "quick" or "full"

const deviceTableBody = document.getElementById("deviceTableBody");
const deviceCount = document.getElementById("deviceCount");
const deviceMetric = document.getElementById("deviceMetric");
const lastScanMetric = document.getElementById("lastScanMetric");
const scanDurationMetric = document.getElementById("scanDurationMetric");

const alertTableBody = document.getElementById("alertTableBody");
const alertCount = document.getElementById("alertCount");
const alertMetric = document.getElementById("alertMetric");

const scanHistoryBody = document.getElementById("scanHistoryBody");
const scanCount = document.getElementById("scanCount");

const usageStatus = document.getElementById("usageStatus");
const usageTotal = document.getElementById("usageTotal");
const usageActiveDevices = document.getElementById("usageActiveDevices");

const fleetTrack = document.getElementById("fleetCarouselTrack");
const fleetViewport = document.getElementById("fleetCarouselViewport");
const fleetPrevBtn = document.getElementById("fleetPrev");
const fleetNextBtn = document.getElementById("fleetNext");
const viewAllDeviceCount = document.getElementById("viewAllDeviceCount");

const SCAN_POLL_INTERVAL_MS = 2000;   // check a running scan every 2 seconds
const SCAN_MAX_POLL_ATTEMPTS = 1000;  // stop watching after ~33 minutes (full scans can take 30)
const DEFAULT_REFRESH_MS = 5000;      // used until the Settings value is read
const MAX_TABLE_ROWS = 10;            // scan history + alerts tables show the newest 10
                                      // (the discovered devices table shows every device)
const CARD_GAP = 16;                  // px between carousel cards (Tailwind gap-4)

// NEW_BADGE: marks a device LanWatch hasn't seen before (not yet recognised:
// services/change_detection.py); shown in the devices table and the carousel
const NEW_BADGE = `<span class="ml-2 inline-block rounded-full border border-[#F59E0B]/40 bg-[#F59E0B]/15 px-2 py-0.5 align-middle font-mono text-[10px] font-bold uppercase tracking-wider text-[#FBBF24]" title="A device LanWatch hasn't seen before. Check it on the Devices page.">New</span>`;

// sleep(): pause inside an async function, e.g. await sleep(2000)
const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));


// ============================================================================
// COMPONENT 1: SUBNET SCAN CONTROL
// Trigger: user picks Quick/Full and clicks "Run Scan", or the page opens
//          while a scan is running.
// Flow: page opens -> resumeActiveScan() -> GET /api/scans/active
//       -> if one is running, pollScanStatus() follows it
// Flow: runScan() -> POST /api/scans/ (routes/scans.py run_scan)
//       -> pollScanStatus() asks GET /api/scans/<id> every 2 s
//       -> on "completed": loadDevices() + loadScanHistory() redraw the tables
// ============================================================================

// scanLabel(): "quick" -> "Quick scan", "full" -> "Full scan"
function scanLabel(scanType) {
    return scanType === "full" ? "Full scan" : "Quick scan";
}

// setScanControlsBusy(): lock (or unlock) the scan box, type and button
function setScanControlsBusy(busy) {
    scanButton.disabled = busy;
    scanButton.textContent = busy ? "Scanning..." : "Run Scan";
    if (scanTypeSelect) scanTypeSelect.disabled = busy;
}

// runScan(): send the typed network range and chosen scan type to the server
async function runScan() {
    const network = networkInput.value.trim();
    const scanType = scanTypeSelect ? scanTypeSelect.value : "quick";

    if (!network) {
        scanStatus.textContent = "Please enter a network range.";
        return;
    }

    setScanControlsBusy(true);
    scanStatus.textContent = `Starting ${scanLabel(scanType).toLowerCase()}...`;

    try {
        const response = await fetch("/api/scans/", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ network: network, scan_type: scanType })
        });
        const data = await response.json();

        // 409 = a scan is already running: follow that one instead of erroring
        if (response.status === 409 && data.scan_id) {
            await resumeActiveScan();
            return;
        }

        // 400 = bad range
        if (!response.ok) throw new Error(data.error || "Failed to start the scan.");

        await pollScanStatus(data.scan_id, network, Date.now(), scanType);

    } catch (error) {
        console.error(error);
        scanStatus.textContent = error.message;
        setScanControlsBusy(false);
    }
}

// resumeActiveScan(): called when the dashboard opens. If a scan is already
// running on the server (started before the user changed page, or by the
// automatic scheduler), lock the button and carry on following it.
// Flow: GET /api/scans/active (routes/scans.py get_active_scan) -> pollScanStatus()
async function resumeActiveScan() {
    try {
        const response = await fetch("/api/scans/active");
        if (!response.ok) return;
        const { scan } = await response.json();
        if (!scan) return;                                   // nothing running: stay "Ready"

        setScanControlsBusy(true);
        if (scan.network_range) networkInput.value = scan.network_range;
        if (scanTypeSelect && scan.scan_type) scanTypeSelect.value = scan.scan_type;

        // count elapsed time from when the scan really started, not from page load
        const startedAt = scan.start_time ? parseUtc(scan.start_time).getTime() : Date.now();
        await pollScanStatus(scan.scan_id, scan.network_range, startedAt, scan.scan_type);
    } catch (error) {
        console.error("Could not check for a running scan:", error);
    }
}

// pollScanStatus(): keep asking the server how scan <scanId> is going,
// show the elapsed time, and show the result when it finishes
async function pollScanStatus(scanId, network, startedAt, scanType) {
    network = network || networkInput.value;
    startedAt = startedAt || Date.now();
    const label = scanLabel(scanType);

    try {
        for (let attempt = 0; attempt < SCAN_MAX_POLL_ATTEMPTS; attempt++) {
            await sleep(SCAN_POLL_INTERVAL_MS);

            const response = await fetch(`/api/scans/${scanId}`);
            if (!response.ok) throw new Error("Lost contact with server.");
            const scan = await response.json();

            if (scan.scan_status === "completed") {
                scanStatus.textContent = `${label} completed in ${scan.duration_seconds}s — ${scan.results_summary}`;
                await loadDevices();
                await loadScanHistory();
                await sleep(5000);                       // leave the result visible
                scanStatus.textContent = "Ready";
                return;
            }

            if (scan.scan_status === "failed") {
                scanStatus.textContent = `${label} failed: ${scan.results_summary}`;
                await loadScanHistory();
                await sleep(5000);
                scanStatus.textContent = "Ready";
                return;
            }

            const elapsedSeconds = Math.round((Date.now() - startedAt) / 1000);
            scanStatus.textContent = `${label} of ${network} (${fmtElapsed(elapsedSeconds)} elapsed)...`;
        }
        scanStatus.textContent = "Still scanning after 30 minutes. Check Scan History later.";
    } catch (err) {
        console.error("Scan error:", err);
        scanStatus.textContent = err.message;
        await sleep(5000);
        scanStatus.textContent = "Ready";
    } finally {
        setScanControlsBusy(false);
    }
}

// fmtElapsed(): seconds -> "45s" or "12m 05s"
function fmtElapsed(seconds) {
    if (seconds < 60) return `${seconds}s`;
    return `${Math.floor(seconds / 60)}m ${String(seconds % 60).padStart(2, "0")}s`;
}

// scanTypeBadge(): small "Quick" / "Full" label for the scan history table
function scanTypeBadge(scanType) {
    return scanType === "full"
        ? `<span class="ml-2 rounded-full border border-[#8B5CF6]/30 bg-[#8B5CF6]/10 px-1.5 py-0.5 text-[10px] text-[#C4ABFF]">Full</span>`
        : `<span class="ml-2 rounded-full border border-white/10 px-1.5 py-0.5 text-[10px] text-slate-400">Quick</span>`;
}

if (scanButton) {
    scanButton.addEventListener("click", runScan);
    resumeActiveScan();          // page (re)opened: pick up a scan that is still running
}


// ============================================================================
// COMPONENT 2: DISCOVERED DEVICES (table + "Discovered Devices" and
//              "Active Hosts" cards)
// Trigger: page load, every auto-refresh, and after a scan completes.
// Flow: loadDevices() -> GET /api/devices/ (routes/devices.py get_devices)
//       -> renderDeviceTable() + renderFleetCards() (COMPONENT 3)
// ============================================================================

// loadDevices(): fetch every device, then update the table, cards and carousel
async function loadDevices() {
    try {
        const res = await fetch("/api/devices/");
        if (!res.ok) return;
        const devices = await res.json();

        const activeCount = devices.filter(d => d.status === "active").length;
        if (deviceMetric) deviceMetric.textContent = devices.length;
        if (deviceCount) deviceCount.textContent = `${devices.length} Devices`;
        if (usageActiveDevices) usageActiveDevices.textContent = activeCount;

        renderDeviceTable(devices);
        renderFleetCards(devices);
    } catch (err) {
        console.error("Error loading devices:", err);
    }
}

// renderDeviceTable(): one row per device: IP, OS, MAC, status, up time
function renderDeviceTable(devices) {
    if (!deviceTableBody) return;

    if (!devices.length) {
        deviceTableBody.innerHTML = `<tr><td colspan="5" class="px-5 py-8 text-center text-slate-400 font-mono">No devices discovered yet.</td></tr>`;
        return;
    }

    deviceTableBody.innerHTML = devices.map(d => {
        const active = d.status === "active";
        const badge = active
            ? "bg-emerald-500/15 text-emerald-400 border border-emerald-500/30"
            : "bg-slate-700/50 text-slate-400";
        return `
            <tr class="hover:bg-white/[0.02]">
                <td class="px-5 py-4">
                    <a href="/devices?id=${encodeURIComponent(d.device_id)}" class="font-semibold text-slate-100 hover:text-[#00F2FE]">${esc(d.hostname || "Unknown device")}</a>${d.recognised ? "" : NEW_BADGE}
                    <p class="font-mono text-xs text-slate-500">${esc(d.device_ip)}</p>
                </td>
                <td class="px-5 py-4 font-semibold text-slate-100">${esc(d.os_name || "Unknown")}</td>
                <td class="px-5 py-4 font-mono text-xs text-slate-400">${esc(d.device_mac || "—")}</td>
                <td class="px-5 py-4"><span class="px-2.5 py-1 text-xs rounded-full font-mono ${badge}">${esc(d.status)}</span></td>
                <td class="px-5 py-4 font-mono text-xs text-slate-400">${active ? fmtSince(d.connected_since, d.last_detected) : "—"}</td>
            </tr>`;
    }).join("");
}


// ============================================================================
// COMPONENT 3: ACTIVE NETWORK FLEET (carousel of active devices)
// Trigger: drawn by loadDevices(); user clicks ← / → to slide it.
//          On phones it is swiped with a finger instead (css/style.css) and
//          updateFleetPosition() shows "2 / 9".
// ============================================================================

let carouselOffset = 0;   // how far (px) the strip is slid to the left

// Colour themes the cards alternate between
const ACCENTS = [
    { text: "text-[#00F2FE]", bg: "bg-[#00F2FE]/10", bar: "from-[#00F2FE] to-[#06B6D4]" },
    { text: "text-[#8B5CF6]", bg: "bg-[#8B5CF6]/10", bar: "from-[#8B5CF6] to-[#6366F1]" },
];

// deviceCard(): HTML for one carousel card (icon, name, IP, live speed bar)
function deviceCard(d, i) {
    const a = ACCENTS[i % ACCENTS.length];
    const bps = d.throughput_bytes || 0;
    const hasTraffic = bps > 0;
    const pct = hasTraffic ? Math.min(100, Math.round((bps / 1048576) * 100)) : 0;   // full bar = 1 MB/s

    return `
    <div class="fleet-card group flex-shrink-0 w-64 p-4 rounded-xl border border-white/10 ${a.bg} flex flex-col justify-between
                transition duration-200 ease-in hover:-translate-y-1 hover:border-white/30 hover:shadow-lg hover:shadow-black/40">
        <div class="flex justify-between items-center gap-2 mb-3">
            <span class="min-w-0 truncate text-xs font-mono font-medium ${a.text}" title="${esc(d.os_name || "Device")}">${esc(d.os_name || "Device")}</span>
            <span class="shrink-0 text-xs font-mono text-emerald-400">Active</span>
        </div>
        <div class="flex items-center gap-3 mb-4">
            <div class="w-10 h-10 shrink-0 rounded-lg ${a.bg} flex items-center justify-center ${a.text}
                        transition-transform duration-200 group-hover:scale-110">
                <span class="material-symbols-outlined text-2xl">${getDeviceIcon(d.hostname)}</span>
            </div>
            <div class="min-w-0">
                <h4 class="font-semibold text-slate-100 truncate">${esc(d.hostname || "Unknown device")}</h4>
                ${d.recognised ? "" : NEW_BADGE}
                <p class="text-xs font-mono text-slate-400">${esc(d.device_ip)}</p>
            </div>
        </div>
        <div class="space-y-1 mb-3">
            <div class="flex justify-between text-xs font-mono">
                <span class="text-slate-400">Live Feed</span>
                <span class="${a.text} font-bold">${hasTraffic ? formatThroughput(bps) : "—"}</span>
            </div>
            <div class="w-full bg-slate-800 rounded-full h-1.5 overflow-hidden">
                <div class="bg-gradient-to-r ${a.bar} h-1.5 rounded-full" style="width: ${pct}%"></div>
            </div>
        </div>
        <a href="/devices?id=${encodeURIComponent(d.device_id)}" class="text-xs ${a.text} hover:underline font-mono inline-block">
            Inspect on /devices →
        </a>
    </div>`;
}

// renderFleetCards(): draw a card for every active device
function renderFleetCards(devices) {
    if (!fleetTrack) return;
    const active = devices.filter(d => (d.status || "").toLowerCase() === "active");
    if (viewAllDeviceCount) viewAllDeviceCount.textContent = `${active.length} Active`;

    fleetTrack.innerHTML = active.length
        ? active.map((d, i) => deviceCard(d, i)).join("")
        : `<div class="p-6 text-center text-slate-400 font-mono"><p>No active devices found.</p></div>`;

    updateCarousel();   // keep the current position (clamped if devices disappeared)
}

// carouselStep(): one card width + gap = how far one click slides
function carouselStep() {
    const card = fleetTrack ? fleetTrack.querySelector(".fleet-card") : null;
    return card ? card.offsetWidth + CARD_GAP : 0;
}

// carouselMaxOffset(): furthest the strip can slide before running out of cards
function carouselMaxOffset() {
    if (!fleetTrack || !fleetViewport) return 0;
    return Math.max(0, fleetTrack.scrollWidth - fleetViewport.clientWidth);
}

// isPhoneWidth(): below 768 px the strip is swiped, not moved by ← → (css/style.css)
const isPhoneWidth = () => window.matchMedia("(max-width: 767.98px)").matches;

// updateFleetPosition(): the "2 / 9" counter shown on phones
function updateFleetPosition() {
    const label = document.getElementById("fleetPosition");
    if (!label || !fleetTrack || !fleetViewport) return;
    const cards = fleetTrack.querySelectorAll(".fleet-card");
    if (cards.length < 2) { label.textContent = ""; return; }
    const step = cards[0].offsetWidth + CARD_GAP;
    const atEnd = fleetViewport.scrollLeft >= fleetViewport.scrollWidth - fleetViewport.clientWidth - 2;
    const index = atEnd ? cards.length : Math.min(cards.length, Math.round(fleetViewport.scrollLeft / step) + 1);
    label.textContent = `${index} / ${cards.length}  ·  swipe`;
}

// updateCarousel(): move the strip to carouselOffset and enable/disable ← →
function updateCarousel() {
    if (!fleetTrack) return;
    updateFleetPosition();
    if (isPhoneWidth()) return;                          // swiped instead
    if (fleetViewport) fleetViewport.scrollLeft = 0;     // (in case it was swiped on a narrow window)
    carouselOffset = Math.min(Math.max(carouselOffset, 0), carouselMaxOffset());
    fleetTrack.style.transform = `translateX(-${carouselOffset}px)`;
    if (fleetPrevBtn) fleetPrevBtn.disabled = carouselOffset <= 0;
    if (fleetNextBtn) fleetNextBtn.disabled = carouselOffset >= carouselMaxOffset() - 1;
}

if (fleetPrevBtn) fleetPrevBtn.addEventListener("click", () => { carouselOffset -= carouselStep(); updateCarousel(); });
if (fleetNextBtn) fleetNextBtn.addEventListener("click", () => { carouselOffset += carouselStep(); updateCarousel(); });
window.addEventListener("resize", updateCarousel);
if (fleetViewport) fleetViewport.addEventListener("scroll", updateFleetPosition, { passive: true });


// ============================================================================
// COMPONENT 4: SCAN HISTORY (table + "last scan / duration" on the first card)
// Trigger: page load and after a scan finishes.
// Flow: loadScanHistory() -> GET /api/scans/ (routes/scans.py get_scans)
//       -> displayScanHistory()
// ============================================================================

// loadScanHistory(): fetch every scan, newest first
async function loadScanHistory() {
    try {
        const response = await fetch("/api/scans/");
        if (!response.ok) throw new Error("Failed to load scan history.");
        displayScanHistory(await response.json());
    } catch (error) {
        console.error("Scan history load error:", error);
    }
}

// displayScanHistory(): fill the history table and the latest-scan numbers
function displayScanHistory(scans) {
    if (!scanHistoryBody || !scanCount) return;

    scanCount.textContent = `${scans.length} Scans`;

    if (scans.length) {
        const latest = scans[0];
        if (lastScanMetric) {
            lastScanMetric.textContent = latest.start_time
                ? parseUtc(latest.start_time).toLocaleTimeString()
                : "—";
        }
        if (scanDurationMetric) {
            scanDurationMetric.textContent = latest.duration_seconds != null ? `${latest.duration_seconds}s` : "—";
        }
    }

    if (!scans.length) {
        scanHistoryBody.innerHTML = `
            <tr>
                <td colspan="7" class="px-5 py-10 text-center text-sm text-[#8b90a0]">No scans recorded yet.</td>
            </tr>`;
        return;
    }

    const STATUS_CLASS = {
        completed: "bg-[#4edea3]/15 text-[#4edea3]",
        running: "bg-[#adc6ff]/15 text-[#adc6ff]",
        failed: "bg-[#ff5449]/15 text-[#ff5449]",
    };

    // newest MAX_TABLE_ROWS only; the Scan History page has them all
    let rows = scans.slice(0, MAX_TABLE_ROWS).map(scan => {
        const status = (scan.scan_status || "").toLowerCase();
        const statusClass = STATUS_CLASS[status] || "bg-[#414755]/30 text-[#8b90a0]";
        return `
            <tr class="border-b border-[#414755]/50 hover:bg-[#2d3449]/30">
                <td class="mono px-5 py-3 text-xs text-[#8b90a0] whitespace-nowrap">#${esc(scan.scan_id)}${scanTypeBadge(scan.scan_type)}</td>
                <td class="mono px-5 py-3">${esc(scan.network_range)}</td>
                <td class="px-5 py-3"><span class="rounded-full px-2 py-0.5 text-xs ${statusClass}">${esc(scan.scan_status)}</span></td>
                <td class="mono px-5 py-3 text-xs">${scan.duration_seconds != null ? esc(scan.duration_seconds) + "s" : "—"}</td>
                <td class="px-5 py-3 text-xs">${esc(scan.results_summary || "—")}</td>
                <td class="mono px-5 py-3 text-xs text-[#8b90a0]">${esc(fmtTime(scan.start_time))}</td>
                <td class="mono px-5 py-3 text-xs text-[#8b90a0]">${esc(fmtTime(scan.end_time))}</td>
            </tr>`;
    }).join("");

    if (scans.length > MAX_TABLE_ROWS) {
        rows += `
            <tr>
                <td colspan="7" class="px-5 py-3 text-center font-mono text-xs text-slate-400">
                    Showing ${MAX_TABLE_ROWS} of ${scans.length} scans. <a href="/scans-history" class="text-[#00F2FE] hover:underline">See all</a>
                </td>
            </tr>`;
    }
    scanHistoryBody.innerHTML = rows;
}


// ============================================================================
// COMPONENT 5: SECURITY & BANDWIDTH ALERTS (table + "Active Alerts" card)
// Trigger: page load and every auto-refresh.
// Flow: loadAlerts() -> GET /api/alerts/ (routes/alerts.py get_alerts)
//       -> displayAlerts() shows the newest MAX_TABLE_ROWS
//       (the full list, with "Mark as resolved", is on the Security Alerts page)
// ============================================================================

// loadAlerts(): fetch unresolved alerts
async function loadAlerts() {
    try {
        const response = await fetch("/api/alerts/");
        if (!response.ok) throw new Error("Failed to load security alerts.");
        displayAlerts(await response.json());
    } catch (error) {
        console.error("Alerts load error:", error);
    }
}

// displayAlerts(): fill the alerts table with a coloured severity badge per row
function displayAlerts(alerts) {
    if (!alertTableBody || !alertCount || !alertMetric) return;

    alertCount.textContent = `${alerts.length} Alert${alerts.length === 1 ? "" : "s"}`;
    alertMetric.textContent = alerts.length;

    if (!alerts.length) {
        alertTableBody.innerHTML = `
            <tr>
                <td colspan="5" class="px-5 py-8 text-center text-sm text-[#8b90a0]">No active traffic alerts detected.</td>
            </tr>`;
        return;
    }

    const SEVERITY_CLASS = {
        critical: "bg-red-500/20 text-red-300 border-red-500/30 font-semibold",
        high: "bg-orange-500/20 text-orange-300 border-orange-500/30",
        medium: "bg-yellow-500/20 text-yellow-300 border-yellow-500/30",
        low: "bg-blue-500/20 text-blue-300 border-blue-500/30",
    };

    let rows = alerts.slice(0, MAX_TABLE_ROWS).map(alert => `
        <tr class="border-b border-[#414755]/50 hover:bg-[#2d3449]/40">
            <td class="px-5 py-3">
                <span class="inline-block rounded-full border px-2.5 py-0.5 text-xs uppercase ${SEVERITY_CLASS[alert.severity] || SEVERITY_CLASS.low}">
                    ${esc(alert.severity)}
                </span>
            </td>
            <td class="px-5 py-3 font-medium">${esc(alert.hostname || "Unknown")}</td>
            <td class="mono px-5 py-3 text-xs text-[#c1c6d7]">${esc(alert.device_ip || "—")} <br><span class="text-[10px] text-[#8b90a0]">${esc(alert.device_mac || "")}</span></td>
            <td class="px-5 py-3 text-sm text-[#dae2fd]">${esc(alert.message)}</td>
            <td class="mono px-5 py-3 text-xs text-[#8b90a0]">${alert.created_at ? esc(parseUtc(alert.created_at).toLocaleTimeString()) : "—"}</td>
        </tr>`).join("");

    if (alerts.length > MAX_TABLE_ROWS) {
        rows += `
            <tr>
                <td colspan="5" class="px-5 py-3 text-center font-mono text-xs text-slate-400">
                    Showing ${MAX_TABLE_ROWS} of ${alerts.length} alerts. <a href="/alerts" class="text-[#00F2FE] hover:underline">See all</a>
                </td>
            </tr>`;
    }
    alertTableBody.innerHTML = rows;
}


// ============================================================================
// COMPONENT 6: TOTAL NETWORK TRAFFIC ("Traffic Monitoring" card)
// Trigger: page load and every auto-refresh.
// Flow: loadTotalTraffic() -> GET /api/usage/total (routes/usage.py)
//       -> live speed + "Live"/"Idle" label
// ============================================================================

// loadTotalTraffic(): show the whole network's current speed
async function loadTotalTraffic() {
    try {
        const response = await fetch("/api/usage/total");
        if (!response.ok) return;
        const data = await response.json();

        if (usageTotal) usageTotal.textContent = formatThroughput(data.live_throughput_bytes);

        if (usageStatus) {
            const hasTraffic = (data.live_throughput_bytes || 0) > 0;
            usageStatus.textContent = hasTraffic ? "Live" : "Idle";
            usageStatus.className = `rounded-full px-2 py-0.5 text-xs ${hasTraffic ? "bg-[#4edea3]/15 text-[#4edea3]" : "bg-[#414755]/30 text-[#8b90a0]"}`;
        }
    } catch (error) {
        console.error("Traffic total load error:", error);
    }
}


// ============================================================================
// COMPONENT 7: WI-FI BADGE (header: network name + signal strength)
// Trigger: page load and every auto-refresh.
// Flow: loadNetworkInfo() -> GET /api/network/info (routes/local_network.py
//       -> services/network_info.py) -> show or hide the badge
// ============================================================================

// loadNetworkInfo(): show the Wi-Fi name and a green/amber/red signal badge
async function loadNetworkInfo() {
    const badge = document.getElementById("networkBadge");
    const ssid = document.getElementById("networkSsid");
    const signal = document.getElementById("networkSignal");
    if (!badge || !ssid) return;

    try {
        const response = await fetch("/api/network/info");
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const info = await response.json();

        // no Wi-Fi (cable connection, or details unavailable): hide the badge
        if (!info.platform_supported || !info.wifi) {
            badge.classList.remove("flex");
            badge.classList.add("hidden");
            return;
        }

        badge.classList.remove("hidden");
        badge.classList.add("flex");
        ssid.textContent = info.wifi.ssid;

        if (signal) {
            const pct = info.wifi.signal_percent;
            signal.textContent = pct != null ? `${pct}%` : "";
            signal.className = "font-mono rounded-full px-2 py-0.5 text-xs";
            if (pct == null) return;
            if (pct >= 60) signal.className += " bg-[#10B981]/15 border border-[#10B981]/30 text-[#34D399]";      // good
            else if (pct >= 30) signal.className += " bg-[#F59E0B]/15 border border-[#F59E0B]/30 text-[#FBBF24]"; // fair
            else signal.className += " bg-[#EF4444]/15 border border-[#EF4444]/30 text-[#F87171]";                 // poor
        }
    } catch (error) {
        console.error("Network info load error:", error);
    }
}


// ============================================================================
// COMPONENT 8: START-UP AND AUTO-REFRESH
// Loads everything once, then refreshes the live parts on a timer whose speed
// comes from System Settings -> "Dashboard refresh" (utils.js getRefreshMs).
// ============================================================================

// refreshLiveData(): reload the parts of the page that change by themselves
async function refreshLiveData() {
    await Promise.all([loadAlerts(), loadDevices(), loadTotalTraffic(), loadNetworkInfo(),
                       loadSecurityOverview()]);
}

// autoRefreshLoop(): wait the configured time, refresh, repeat.
// Waits for each round to finish, so slow answers never pile up.
async function autoRefreshLoop() {
    const refreshMs = await getRefreshMs(DEFAULT_REFRESH_MS);
    await sleep(refreshMs);
    await refreshLiveData();
    autoRefreshLoop();
}

loadSystemCheck();
loadScanHistory();
refreshLiveData().then(autoRefreshLoop);


// ============================================================================
// COMPONENT 9: SECURITY OVERVIEW (advice to review + latest network summary)
// Trigger: page load and every auto-refresh (COMPONENT 8).
// Flow: loadSecurityOverview() -> GET /api/findings/ (routes/findings.py)
//                              + GET /api/reports/latest-summary (routes/ai.py)
//       -> the "Security Advice" card and the "Latest Network Summary" panel
// The summary is written after each full scan by services/scan_summary.py:
// the AI's plain-English version if the AI is on, otherwise the plain facts.
// ============================================================================

// loadSecurityOverview(): fetch both, then draw them
async function loadSecurityOverview() {
    try {
        const [findingsRes, summaryRes] = await Promise.all([
            fetch("/api/findings/"), fetch("/api/reports/latest-summary")]);
        if (findingsRes.ok) displayAdviceCount(await findingsRes.json());
        if (summaryRes.ok) displaySummary((await summaryRes.json()).summary);
    } catch (error) {
        console.error("Security overview load error:", error);
    }
}

// displayAdviceCount(): "3" + "recommendations to review (1 high)"
function displayAdviceCount(data) {
    const metric = document.getElementById("adviceMetric");
    const note = document.getElementById("adviceMetricNote");
    if (!metric || !note) return;

    const open = (data.findings || []).filter(f => f.status === "open");
    const total = open.length + (data.counts?.done || 0) + (data.counts?.dismissed || 0);
    if (!total) {
        metric.textContent = "—";
        note.textContent = "Run a full scan to get security advice.";
        return;
    }
    const serious = open.filter(f => f.severity === "high" || f.severity === "critical").length;
    metric.textContent = String(open.length);
    metric.className = `text-3xl font-bold ${serious ? "text-orange-300" : open.length ? "text-yellow-300" : "text-[#34D399]"}`;
    note.textContent = open.length
        ? `recommendation${open.length === 1 ? "" : "s"} to review${serious ? ` (${serious} high priority)` : ""}`
        : "Nothing to review. Everything has been dealt with.";
}

// displaySummary(): AI text (labelled) or the plain facts, plus when and which network
function displaySummary(summary) {
    const textEl = document.getElementById("summaryText");
    const badge = document.getElementById("summaryBadge");
    const meta = document.getElementById("summaryMeta");
    if (!textEl || !summary) return;

    const aiText = summary.ai_translation_summary;
    textEl.textContent = aiText || summary.tech_summary;
    badge.classList.toggle("hidden", !aiText);
    badge.classList.toggle("inline-flex", !!aiText);
    meta.textContent = `After the full scan of ${summary.network_range || "your network"}, ${fmtTime(summary.created_at)}` +
        (aiText ? ". Written by the AI on this computer; it may contain mistakes. The facts: " + summary.tech_summary : "");
}


// ============================================================================
// COMPONENT 10: SYSTEM WARNING (is Nmap / Npcap / Administrator missing?)
// Trigger: page load (once).
// Flow: loadSystemCheck() -> GET /api/network/system-check
//       (routes/local_network.py -> services/system_check.py)
//       -> shows the orange bar listing what's missing, or keeps it hidden
// ============================================================================

// loadSystemCheck(): fetch the checks and list any that failed
async function loadSystemCheck() {
    const box = document.getElementById("systemWarning");
    const list = document.getElementById("systemWarningList");
    if (!box || !list) return;
    try {
        const response = await fetch("/api/network/system-check");
        if (!response.ok) return;
        const result = await response.json();
        const missing = (result.checks || []).filter(c => !c.ok);
        list.innerHTML = missing.map(c =>
            `<li><span class="font-semibold">${esc(c.title)}:</span> ${esc(c.detail)}</li>`).join("");
        box.classList.toggle("hidden", missing.length === 0);
    } catch (error) {
        console.error("System check error:", error);
    }
}

