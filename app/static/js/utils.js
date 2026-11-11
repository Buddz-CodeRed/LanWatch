// ============================================================================
// FILE: app/static/js/utils.js  -  SHARED HELPERS
// ----------------------------------------------------------------------------
// Small tools used by the Dashboard, Devices and Security Alerts pages.
// Loaded BEFORE each page's own script in the HTML template.
//
// Written as plain "function" declarations (not const) so a page that also
// defines a helper with the same name never causes an "already declared" error.
// ============================================================================


// ============================================================================
// GROUP 1: SAFETY
// ============================================================================

// esc(): make text safe before putting it into the page with innerHTML.
// Device names come from the network, so a booby-trapped name could otherwise
// inject code into the page.
function esc(v) {
    const div = document.createElement("div");
    div.textContent = v ?? "";
    return div.innerHTML.replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}


// ============================================================================
// GROUP 2: NUMBER FORMATTING
// ============================================================================

// formatBytes(): 1536000 -> "1.5 MB"
function formatBytes(bytes) {
    if (bytes === null || bytes === undefined) return "—";
    if (bytes === 0) return "0 B";
    const sizes = ["B", "KB", "MB", "GB", "TB"];
    const i = Math.min(sizes.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
    return parseFloat((bytes / Math.pow(1024, i)).toFixed(1)) + " " + sizes[i];
}

// formatThroughput(): bytes per second -> "340.5 KB/s"
function formatThroughput(bytesPerSec) {
    if (!bytesPerSec) return "0 B/s";
    if (bytesPerSec >= 1048576) return `${(bytesPerSec / 1048576).toFixed(1)} MB/s`;
    if (bytesPerSec >= 1024) return `${(bytesPerSec / 1024).toFixed(1)} KB/s`;
    return `${Math.round(bytesPerSec)} B/s`;
}


// ============================================================================
// GROUP 3: DATES AND DURATIONS
// SQLite stores times in UTC as "YYYY-MM-DD HH:MM:SS" with no timezone marker.
// ============================================================================

// parseUtc(): database time text -> JavaScript Date (in UTC)
function parseUtc(ts) {
    return new Date(String(ts).replace(" ", "T") + "Z");
}

// fmtTime(): database time text -> the viewer's local date and time
function fmtTime(ts) {
    if (!ts) return "—";
    const d = parseUtc(ts);
    return isNaN(d) ? String(ts) : d.toLocaleString();
}

// fmtHM(): a number of seconds -> "3d 4h 10m" / "2h 15m" / "5m"
function fmtHM(totalSeconds) {
    if (totalSeconds == null || isNaN(totalSeconds) || totalSeconds < 0) return "—";
    const totalMinutes = Math.floor(totalSeconds / 60);
    const d = Math.floor(totalMinutes / 1440);
    const h = Math.floor((totalMinutes % 1440) / 60);
    const m = totalMinutes % 60;
    if (d) return `${d}d ${h}h ${m}m`;
    if (h) return `${h}h ${m}m`;
    return `${m}m`;
}

// fmtSince(): time between two database timestamps, e.g. how long connected
function fmtSince(startTs, endTs) {
    if (typeof startTs !== "string" || typeof endTs !== "string") return "—";
    return fmtHM((parseUtc(endTs) - parseUtc(startTs)) / 1000);
}


// ============================================================================
// GROUP 4: DEVICE ICONS
// ============================================================================

// getDeviceIcon(): choose a Material Symbols icon name from the device's name
function getDeviceIcon(hostname) {
    const name = (hostname || "").toLowerCase();
    if (name.includes("nas") || name.includes("synology") || name.includes("storage")) return "dns";
    if (name.includes("unifi") || name.includes("router") || name.includes("gateway") || name.includes("dream")) return "router";
    if (name.includes("alienware") || name.includes("asus") || name.includes("msi") || name.includes("predator")) return "sports_esports";
    if (name.includes("macbook") || name.includes("mac") || name.includes("laptop")) return "laptop_mac";
    if (name.includes("playstation") || name.includes("ps5") || name.includes("xbox") || name.includes("nintendo")) return "videogame_asset";
    if (name.includes("sony") || name.includes("bravia") || name.includes("tv") || name.includes("samsung") || name.includes("lg")) return "tv";
    if (name.includes("phone") || name.includes("iphone") || name.includes("android") || name.includes("pixel")) return "smartphone";
    if (name.includes("printer")) return "print";
    return "devices";
}


// ============================================================================
// GROUP 5: REFRESH SPEED (System Settings -> "Dashboard refresh")
// ============================================================================

// getRefreshMs(): how often live pages should refresh, in milliseconds.
// Reads dashboard_refresh_seconds from GET /api/settings/refresh (routes/settings.py),
// the one setting pages may read without unlocking the Settings page.
// Pages call this before each refresh, so a change on the Settings page
// applies without reloading. Falls back to `fallbackMs` if it can't be read.
async function getRefreshMs(fallbackMs) {
    try {
        const res = await fetch("/api/settings/refresh", { cache: "no-store" });
        if (!res.ok) return fallbackMs;
        const seconds = Number((await res.json()).dashboard_refresh_seconds);
        return seconds > 0 ? seconds * 1000 : fallbackMs;
    } catch (err) {
        return fallbackMs;
    }
}
