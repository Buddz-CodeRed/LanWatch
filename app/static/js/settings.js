// ============================================================================
// FILE: app/static/js/settings.js  -  SYSTEM SETTINGS PAGE LOGIC
// ----------------------------------------------------------------------------
// Page: templates/settings.html   (self-contained: does not need utils.js)
//
// Flow (opening):  init() -> checkLock() -> GET /api/auth/status     [GROUP 0]
//                  locked? -> password dialog -> submitUnlock() -> POST /api/auth/unlock
//                  unlocked -> startPage():
//                    loadSettings() -> GET /api/settings/
//                         -> applySettings() fills Scanning, Traffic and AI sections
//                    loadAccount()  -> GET /api/account/
//                         -> showAccount() or showSetup() (first run: no password yet)
//                    + event "lanwatch:settings-unlocked" -> phone_settings.js
//                      (Phones and tablets section, its own file)
// Flow (locking):  "Lock settings" -> lockNow() -> POST /api/auth/lock
//                  15 min unused -> the next request answers 401 -> dialog again
// Flow (typing):   every input re-checks its value, updates the status line
//                  and lights up "Save changes" only when something changed
// Flow (saving):   "Save changes" (Scanning) -> saveScan()    -> PUT /api/settings/
//                  "Save changes" (Traffic)  -> saveTraffic() -> PUT /api/settings/
//                  "Create/Update account"   -> saveAccount() -> POST or PUT /api/account/
// Server side: app/routes/settings.py. Who uses each saved value:
//   quick scan interval + default network -> services/scan_scheduler.py
//   traffic sampling interval             -> services/network_monitor.py
//   dashboard refresh                     -> utils.js getRefreshMs()
//
// Errors come back as { "error": "message" } and are shown next to the Save button.
// Wrapped in (function () { ... })() so its variables stay private.
// ============================================================================
(function () {
    "use strict";

    // ========================================================================
    // SETUP: API ADDRESSES, LIMITS AND PAGE STATE
    // ========================================================================

    const SETTINGS_URL = "/api/settings/";
    const ACCOUNT_URL = "/api/account/";

    // These limits match app/routes/settings.py. The server checks them again.
    const INTERVAL_MIN = 5;         // quick scans
    const FULL_INTERVAL_MIN = 60;   // full scans take up to 30 minutes
    const INTERVAL_MAX = 10080; // one week, in minutes
    const SAMPLE_MIN = 1, SAMPLE_MAX = 300;     // traffic measurement interval, seconds
    const REFRESH_MIN = 2, REFRESH_MAX = 300;   // dashboard refresh interval, seconds
    const PASSWORD_MIN = 8;
    const USERNAME_RE = /^[A-Za-z0-9_.-]{3,32}$/;

    const $ = (id) => document.getElementById(id);

    let savedSettings = null;   // what the server last gave us
    let trafficSupported = true; // false if the server doesn't send the traffic settings yet
    let savedUsername = "";
    let setupMode = false;      // true while no account exists yet (first run)

    // ========================================================================
    // GROUP 1: SHARED HELPERS
    // ========================================================================

    // toDate(): database time text -> Date.
    // Backend timestamps look like "YYYY-MM-DD HH:MM:SS" and are stored in UTC.
    function toDate(v) {
        if (!v) return null;
        const d = new Date(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}/.test(v) ? v.replace(" ", "T") + "Z" : v);
        return isNaN(d) ? null : d;
    }

    // plural(): "1 minute" / "5 minutes"
    function plural(n, word) { return n + " " + word + (n === 1 ? "" : "s"); }

    // fmtInterval(): minutes -> "Off", "30 minutes", "2 hours", "1 day"
    function fmtInterval(min) {
        if (!min) return "Off";
        if (min < 60) return plural(min, "minute");
        const h = Math.floor(min / 60), m = min % 60;
        if (h >= 24 && m === 0 && h % 24 === 0) return plural(h / 24, "day");
        return m ? h + "h " + m + "m" : plural(h, "hour");
    }

    // setMsg(): show a message under a section (kind: ok / error / normal)
    function setMsg(el, text, kind) {
        el.textContent = text || "";
        el.className = "min-h-[1.25rem] font-mono text-xs " +
            (kind === "ok" ? "text-[#34D399]" : kind === "error" ? "text-[#FFB4AB]" : "text-slate-400");
    }

    // request(): call the API and return its JSON; any error becomes a
    // readable Error (with .status and .data) for setMsg() to show
    // (skipLockCheck: the lock's own requests must not re-open the dialog)
    async function request(url, method, body, skipLockCheck) {
        const opts = { method: method, headers: { Accept: "application/json" }, cache: "no-store" };
        if (body !== undefined) {
            opts.headers["Content-Type"] = "application/json";
            opts.body = JSON.stringify(body);
        }
        let res;
        try {
            res = await fetch(url, opts);
        } catch (e) {
            const err = new Error("Couldn't reach the server. Check that LanWatch is running.");
            err.network = true;
            throw err;
        }
        let data = null;
        try { data = await res.json(); } catch (e) { /* not JSON */ }
        if (res.status === 401 && data && data.locked && !skipLockCheck) {
            // the unlock expired while the page was open: ask for the password again
            showLockDialog("Settings locked after " + $("lockIdle").textContent + " minutes without use. Enter the password to continue.");
        }
        if (!res.ok) {
            const err = new Error((data && data.error) || "The server returned an error (HTTP " + res.status + ").");
            err.status = res.status;
            err.data = data;
            throw err;
        }
        return data;
    }

    // ========================================================================
    // GROUP 0: ADMIN PASSWORD LOCK
    // The page starts covered by a password dialog. checkLock() asks
    // GET /api/auth/status: if unlocked (or no admin account exists yet) the
    // settings load; otherwise the dialog asks for the admin password and
    // submitUnlock() sends POST /api/auth/unlock. If the unlock expires while
    // the page is open (15 minutes unused), the next request answers 401
    // "locked" and request() shows the dialog again. The server enforces all
    // of this (app/auth.py); the dialog is only the friendly front.
    // ========================================================================

    let pageStarted = false;      // settings loaded at least once?

    // showLockDialog(): cover the settings and ask for the password
    function showLockDialog(message) {
        $("lockOverlay").classList.remove("hidden");
        $("lockOverlay").classList.add("flex");
        $("lockBar").classList.add("hidden");
        $("lockBar").classList.remove("flex");
        $("lockMsg").textContent = message || "";
        $("lockPassword").value = "";
        $("lockSubmit").disabled = false;
        setTimeout(function () { $("lockPassword").focus(); }, 50);
    }

    // hideLockDialog(): show the settings, plus the "Lock settings" button if
    // there is a password to lock with
    function hideLockDialog(status) {
        $("lockOverlay").classList.add("hidden");
        $("lockOverlay").classList.remove("flex");
        const canLock = !!(status && status.account_exists);
        $("lockBar").classList.toggle("hidden", !canLock);
        $("lockBar").classList.toggle("flex", canLock);
        $("lockState").textContent = canLock
            ? "Unlocked · locks after " + status.idle_minutes + " min unused" : "";
    }

    // startPage(): load everything (only once the page is unlocked)
    function startPage() {
        pageStarted = true;
        loadSettings();
        loadAccount();
        loadAiStatus();
        announceUnlocked();
    }

    // announceUnlocked(): tell other sections' scripts (phone_settings.js) to load
    function announceUnlocked() {
        document.dispatchEvent(new CustomEvent("lanwatch:settings-unlocked"));
    }

    // checkLock(): GET /api/auth/status -> show the dialog or the settings
    async function checkLock() {
        try {
            const status = await request("/api/auth/status", "GET", undefined, true);
            $("lockIdle").textContent = status.idle_minutes;
            if (status.unlocked) {
                hideLockDialog(status);
                startPage();
            } else {
                showLockDialog("");
            }
        } catch (err) {
            showLockDialog(err.message);
        }
    }

    // submitUnlock(): Trigger "Unlock" -> POST /api/auth/unlock
    async function submitUnlock(event) {
        event.preventDefault();
        const password = $("lockPassword").value;
        if (!password) { $("lockMsg").textContent = "Enter the admin password."; return; }
        $("lockSubmit").disabled = true;
        $("lockMsg").textContent = "";
        try {
            await request("/api/auth/unlock", "POST", { password: password }, true);
            const status = await request("/api/auth/status", "GET", undefined, true);
            hideLockDialog(status);
            if (pageStarted) { loadSettings(); loadAccount(); loadAiStatus(); announceUnlocked(); } else { startPage(); }
        } catch (err) {
            $("lockMsg").textContent = err.message;
            $("lockPassword").value = "";
            // after too many attempts, keep the button off for the pause
            const wait = err.data && err.data.wait_seconds;
            if (wait) {
                setTimeout(function () { $("lockSubmit").disabled = false; }, wait * 1000);
            } else {
                $("lockSubmit").disabled = false;
                $("lockPassword").focus();
            }
        }
    }

    // lockNow(): Trigger "Lock settings" -> POST /api/auth/lock
    async function lockNow() {
        try { await request("/api/auth/lock", "POST", {}, true); } catch (e) { /* lock locally anyway */ }
        showLockDialog("Settings locked.");
        $("lockMsg").className = "mt-2 min-h-[1.25rem] font-mono text-xs text-[#34D399]";
    }

    // initLock(): connect the dialog and the Lock button
    function initLock() {
        $("lockForm").addEventListener("submit", submitUnlock);
        $("lockPassword").addEventListener("input", function () {
            $("lockMsg").className = "mt-2 min-h-[1.25rem] font-mono text-xs text-[#FFB4AB]";
        });
        $("lockButton").addEventListener("click", lockNow);
    }

    // ========================================================================
    // GROUP 2: SCANNING SECTION
    // Quick and full scan intervals (used by the automatic scan scheduler) and
    // the default network (used by the scheduler and the dashboard scan box).
    // ========================================================================

    // parseInterval(): check a scan interval box: 0 (off) or min..10080 minutes
    // (min is 5 for quick scans, 60 for full scans)
    function parseInterval(raw, min) {
        min = min || INTERVAL_MIN;
        const text = String(raw).trim();
        if (!/^\d+$/.test(text)) return { error: "Enter a whole number of minutes, or 0 to turn it off." };
        const n = parseInt(text, 10);
        if (n !== 0 && (n < min || n > INTERVAL_MAX)) {
            return { error: "Use 0 (off) or a number between " + min + " and " + INTERVAL_MAX + " minutes." };
        }
        return { value: n };
    }

    // parseSubnet(): check the default network box (empty = auto-detect)
    function parseSubnet(raw) {
        const text = String(raw).trim();
        if (!text) return { value: "" };
        const m = text.match(/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\/(\d{1,2})$/);
        if (!m || m.slice(1, 5).some(function (o) { return parseInt(o, 10) > 255; }) || parseInt(m[5], 10) > 32) {
            return { error: "The network should look like 192.168.1.0/24." };
        }
        if (parseInt(m[5], 10) < 16) return { error: "That network is too large to scan. Use /16 or smaller." };
        return { value: text };
    }

    // fullAvailable(): has the server unlocked full scans?
    function fullAvailable() {
        return !!(savedSettings && savedSettings.full_scan_available);
    }

    // currentScanForm(): all Scanning inputs, checked
    function currentScanForm() {
        return {
            interval: parseInterval($("quickInterval").value),
            full: fullAvailable() ? parseInterval($("fullInterval").value, FULL_INTERVAL_MIN) : { value: null },
            subnet: parseSubnet($("defaultSubnet").value)
        };
    }

    // updateQuickStatus(): the line under the box, e.g. "A quick scan will run every 2 hours."
    function updateQuickStatus() {
        const r = parseInterval($("quickInterval").value);
        const el = $("quickStatus");
        if (r.error) {
            el.textContent = r.error;
            el.className = "mt-2 font-mono text-xs text-[#FFB4AB]";
        } else if (r.value === 0) {
            el.textContent = "Automatic quick scans are off.";
            el.className = "mt-2 font-mono text-xs text-slate-400";
        } else {
            el.textContent = "A quick scan will run every " + (r.value === 60 ? "hour" : fmtInterval(r.value)) + ".";
            el.className = "mt-2 font-mono text-xs text-[#34D399]";
        }
    }

    // updateFullStatus(): the line under the full scan box
    function updateFullStatus() {
        const el = $("fullStatus");
        if (!fullAvailable()) {
            el.textContent = "Full scans aren't available.";
            el.className = "mt-2 font-mono text-xs text-slate-400";
            return;
        }
        const r = parseInterval($("fullInterval").value, FULL_INTERVAL_MIN);
        if (r.error) {
            el.textContent = r.error;
            el.className = "mt-2 font-mono text-xs text-[#FFB4AB]";
        } else if (r.value === 0) {
            el.textContent = "Automatic full scans are off.";
            el.className = "mt-2 font-mono text-xs text-slate-400";
        } else {
            el.textContent = "A full scan will run every " + (r.value === 60 ? "hour" : fmtInterval(r.value)) + ".";
            el.className = "mt-2 font-mono text-xs text-[#34D399]";
        }
    }

    // isScanDirty(): has anything changed since the last save?
    function isScanDirty() {
        if (!savedSettings) return false;
        const f = currentScanForm();
        const interval = f.interval.error ? $("quickInterval").value.trim() : f.interval.value;
        const subnet = f.subnet.error ? $("defaultSubnet").value.trim() : f.subnet.value;
        const fullChanged = fullAvailable() &&
            String(f.full.error ? $("fullInterval").value.trim() : f.full.value) !==
            String(savedSettings.full_scan_interval_minutes);
        return String(interval) !== String(savedSettings.quick_scan_interval_minutes) ||
               subnet !== (savedSettings.default_subnet || "") || fullChanged;
    }

    // refreshScanButtons(): enable Save/Discard only when there are changes
    function refreshScanButtons() {
        const dirty = isScanDirty();
        $("scanSave").disabled = !dirty;
        if (!savedSettings) {
            // Settings never loaded: the left button becomes a retry button
            $("scanReset").textContent = "Reload settings";
            $("scanReset").disabled = false;
        } else {
            $("scanReset").textContent = "Discard changes";
            $("scanReset").disabled = !dirty;
        }
    }

    // applyScan(): fills the Scanning section from `s`. It never touches savedSettings,
    // so saving one section can't wipe unsaved edits in another.
    function applyScan(s) {
        $("quickInterval").value = s.quick_scan_interval_minutes;
        $("fullInterval").value = s.full_scan_interval_minutes;
        $("defaultSubnet").value = s.default_subnet || "";

        // Full scans stay locked if the server says they aren't available
        const fullOn = !!s.full_scan_available;
        $("fullInterval").disabled = !fullOn;
        Array.prototype.forEach.call(document.querySelectorAll(".fullPresetBtn"), function (b) { b.disabled = !fullOn; });
        $("fullBadge").classList.toggle("hidden", fullOn);

        updateQuickStatus();
        updateFullStatus();
        refreshScanButtons();
    }

    // applySettings(): remember the saved settings and fill both sections
    function applySettings(s) {
        savedSettings = s;
        applyScan(s);
        applyTraffic(s);
        applyAi(s);
    }

    // saveScan(): Trigger "Save changes" (Scanning) -> PUT /api/settings/
    async function saveScan() {
        const f = currentScanForm();
        if (f.interval.error) { setMsg($("scanMsg"), f.interval.error, "error"); $("quickInterval").focus(); return; }
        if (f.full.error) { setMsg($("scanMsg"), f.full.error, "error"); $("fullInterval").focus(); return; }
        if (f.subnet.error) { setMsg($("scanMsg"), f.subnet.error, "error"); $("defaultSubnet").focus(); return; }

        $("scanSave").disabled = true;
        $("scanReset").disabled = true;
        setMsg($("scanMsg"), "Saving…");
        try {
            const body = {
                quick_scan_interval_minutes: f.interval.value,
                default_subnet: f.subnet.value
            };
            if (fullAvailable()) body.full_scan_interval_minutes = f.full.value;
            const updated = await request(SETTINGS_URL, "PUT", body);
            savedSettings = Object.assign({}, savedSettings, {
                quick_scan_interval_minutes: updated.quick_scan_interval_minutes,
                full_scan_interval_minutes: updated.full_scan_interval_minutes,
                default_subnet: updated.default_subnet,
                full_scan_available: updated.full_scan_available
            });
            applyScan(savedSettings);
            setMsg($("scanMsg"), "Settings saved.", "ok");
        } catch (err) {
            setMsg($("scanMsg"), err.message, "error");
            refreshScanButtons();
        }
    }

    // resetScan(): Trigger "Discard changes" -> put the saved values back
    function resetScan() {
        if (!savedSettings) { loadSettings(); return; }
        applyScan(savedSettings);
        setMsg($("scanMsg"), "");
    }

    // initScan(): connect the Scanning inputs, preset buttons and buttons
    function initScan() {
        $("quickInterval").addEventListener("input", function () {
            updateQuickStatus();
            refreshScanButtons();
            setMsg($("scanMsg"), "");
        });
        $("fullInterval").addEventListener("input", function () {
            updateFullStatus();
            refreshScanButtons();
            setMsg($("scanMsg"), "");
        });
        $("defaultSubnet").addEventListener("input", function () {
            refreshScanButtons();
            setMsg($("scanMsg"), "");
        });
        Array.prototype.forEach.call(document.querySelectorAll(".fullPresetBtn"), function (btn) {
            btn.addEventListener("click", function () {
                $("fullInterval").value = btn.getAttribute("data-preset");
                updateFullStatus();
                refreshScanButtons();
                setMsg($("scanMsg"), "");
            });
        });
        Array.prototype.forEach.call(document.querySelectorAll(".presetBtn"), function (btn) {
            btn.addEventListener("click", function () {
                $("quickInterval").value = btn.getAttribute("data-preset");
                updateQuickStatus();
                refreshScanButtons();
                setMsg($("scanMsg"), "");
            });
        });
        $("scanSave").addEventListener("click", saveScan);
        $("scanReset").addEventListener("click", resetScan);
    }

    // loadSettings(): GET /api/settings/ and fill the page (or offer a retry)
    async function loadSettings() {
        setSettingsBusy(true);
        setMsg($("scanMsg"), "Loading…");
        setMsg($("trafficMsg"), "");
        try {
            applySettings(await request(SETTINGS_URL, "GET"));
            setMsg($("scanMsg"), "");
        } catch (err) {
            $("quickStatus").textContent = "Couldn't load settings.";
            $("fullStatus").textContent = "Couldn't load settings.";
            $("sampleStatus").textContent = "Couldn't load settings.";
            $("refreshStatus").textContent = "Couldn't load settings.";
            setMsg($("scanMsg"), err.message + " Press Reload settings to try again.", "error");
            setMsg($("trafficMsg"), "Press Reload settings to try again.", "error");
            refreshScanButtons();
            refreshTrafficButtons();
        } finally {
            setSettingsBusy(false);
        }
    }

    // setSettingsBusy(): lock the settings inputs while loading
    function setSettingsBusy(busy) {
        ["quickInterval", "defaultSubnet"].forEach(function (id) { $(id).disabled = busy; });
        Array.prototype.forEach.call(document.querySelectorAll(".presetBtn"), function (b) { b.disabled = busy; });
        // the full scan box only unlocks if the server says full scans are available
        const fullOff = busy || !fullAvailable();
        $("fullInterval").disabled = fullOff;
        Array.prototype.forEach.call(document.querySelectorAll(".fullPresetBtn"), function (b) { b.disabled = fullOff; });
        // A locked traffic section stays locked
        const trafficOff = busy || !trafficSupported;
        ["sampleInterval", "refreshInterval"].forEach(function (id) { $(id).disabled = trafficOff; });
        Array.prototype.forEach.call(document.querySelectorAll(".trafficPreset"), function (b) { b.disabled = trafficOff; });
    }

    // ========================================================================
    // GROUP 3: TRAFFIC MONITORING SECTION
    // Sampling interval (services/network_monitor.py) and dashboard refresh
    // (utils.js getRefreshMs, used by the Dashboard, Devices and Alerts pages).
    // ========================================================================

    // parseSeconds(): check a seconds box against its limits
    function parseSeconds(raw, min, max) {
        const text = String(raw).trim();
        if (!/^\d+$/.test(text)) return { error: "Enter a whole number of seconds." };
        const n = parseInt(text, 10);
        if (n < min || n > max) return { error: "Use a number between " + min + " and " + max + " seconds." };
        return { value: n };
    }

    // currentTrafficForm(): both Traffic inputs, checked
    function currentTrafficForm() {
        return {
            sample: parseSeconds($("sampleInterval").value, SAMPLE_MIN, SAMPLE_MAX),
            refresh: parseSeconds($("refreshInterval").value, REFRESH_MIN, REFRESH_MAX)
        };
    }

    // setStatus(): coloured line under an input
    function setStatus(el, text, tone) {
        el.textContent = text;
        el.className = "mt-2 font-mono text-xs " +
            (tone === "error" ? "text-[#FFB4AB]" : tone === "warn" ? "text-[#FBBF24]" : tone === "ok" ? "text-[#34D399]" : "text-slate-400");
    }

    // updateTrafficStatus(): explain each value in words (readings per day, etc.)
    function updateTrafficStatus() {
        const f = currentTrafficForm();

        if (f.sample.error) {
            setStatus($("sampleStatus"), f.sample.error, "error");
        } else {
            const n = f.sample.value;
            const perDay = Math.round(86400 / n).toLocaleString();
            if (n <= 2) {
                setStatus($("sampleStatus"), "Every " + plural(n, "second") + " is about " + perDay + " readings per device each day. Expect more processing use and a fast-growing database.", "warn");
            } else {
                setStatus($("sampleStatus"), "Traffic is measured every " + plural(n, "second") + " (about " + perDay + " readings per device each day).", "ok");
            }
        }

        if (f.refresh.error) {
            setStatus($("refreshStatus"), f.refresh.error, "error");
        } else if (!f.sample.error && f.refresh.value < f.sample.value) {
            setStatus($("refreshStatus"), "That's faster than traffic is measured (every " + plural(f.sample.value, "second") +
                "), so some updates will show the same numbers.", "warn");
        } else {
            setStatus($("refreshStatus"), "The dashboard will update every " + plural(f.refresh.value, "second") + ".", "ok");
        }
    }

    // isTrafficDirty(): has anything changed since the last save?
    function isTrafficDirty() {
        if (!savedSettings) return false;
        const f = currentTrafficForm();
        const sample = f.sample.error ? $("sampleInterval").value.trim() : f.sample.value;
        const refresh = f.refresh.error ? $("refreshInterval").value.trim() : f.refresh.value;
        return String(sample) !== String(savedSettings.traffic_sample_interval_seconds) ||
               String(refresh) !== String(savedSettings.dashboard_refresh_seconds);
    }

    // refreshTrafficButtons(): enable Save/Discard only when there are changes
    function refreshTrafficButtons() {
        if (!trafficSupported && savedSettings) return; // locked: keep everything disabled
        const dirty = isTrafficDirty();
        $("trafficSave").disabled = !dirty;
        if (!savedSettings) {
            $("trafficReset").textContent = "Reload settings";
            $("trafficReset").disabled = false;
        } else {
            $("trafficReset").textContent = "Discard changes";
            $("trafficReset").disabled = !dirty;
        }
    }

    // lockTraffic(): disable the section if the server doesn't support it
    function lockTraffic() {
        trafficSupported = false;
        ["sampleInterval", "refreshInterval", "trafficSave", "trafficReset"].forEach(function (id) { $(id).disabled = true; });
        Array.prototype.forEach.call(document.querySelectorAll(".trafficPreset"), function (b) { b.disabled = true; });
        setStatus($("sampleStatus"), "Not available yet.", "warn");
        setStatus($("refreshStatus"), "Not available yet.", "warn");
        setMsg($("trafficMsg"), "The server doesn't have the traffic settings yet. Replace app/routes/settings.py with the latest version and restart Flask.", "error");
    }

    // applyTraffic(): fill the Traffic section from `s`
    function applyTraffic(s) {
        if (typeof s.traffic_sample_interval_seconds !== "number" || typeof s.dashboard_refresh_seconds !== "number") {
            lockTraffic();
            return;
        }
        trafficSupported = true;
        $("sampleInterval").value = s.traffic_sample_interval_seconds;
        $("refreshInterval").value = s.dashboard_refresh_seconds;
        updateTrafficStatus();
        refreshTrafficButtons();
    }

    // saveTraffic(): Trigger "Save changes" (Traffic) -> PUT /api/settings/
    async function saveTraffic() {
        const f = currentTrafficForm();
        if (f.sample.error) { setMsg($("trafficMsg"), f.sample.error, "error"); $("sampleInterval").focus(); return; }
        if (f.refresh.error) { setMsg($("trafficMsg"), f.refresh.error, "error"); $("refreshInterval").focus(); return; }

        $("trafficSave").disabled = true;
        $("trafficReset").disabled = true;
        setMsg($("trafficMsg"), "Saving…");
        try {
            const updated = await request(SETTINGS_URL, "PUT", {
                traffic_sample_interval_seconds: f.sample.value,
                dashboard_refresh_seconds: f.refresh.value
            });
            savedSettings = Object.assign({}, savedSettings, {
                traffic_sample_interval_seconds: updated.traffic_sample_interval_seconds,
                dashboard_refresh_seconds: updated.dashboard_refresh_seconds
            });
            applyTraffic(savedSettings);
            setMsg($("trafficMsg"), "Settings saved.", "ok");
        } catch (err) {
            setMsg($("trafficMsg"), err.message, "error");
            refreshTrafficButtons();
        }
    }

    // resetTraffic(): Trigger "Discard changes" -> put the saved values back
    function resetTraffic() {
        if (!savedSettings) { loadSettings(); return; }
        applyTraffic(savedSettings);
        setMsg($("trafficMsg"), "");
    }

    // initTraffic(): connect the Traffic inputs, preset buttons and buttons
    function initTraffic() {
        ["sampleInterval", "refreshInterval"].forEach(function (id) {
            $(id).addEventListener("input", function () {
                updateTrafficStatus();
                refreshTrafficButtons();
                setMsg($("trafficMsg"), "");
            });
        });
        Array.prototype.forEach.call(document.querySelectorAll(".trafficPreset"), function (btn) {
            btn.addEventListener("click", function () {
                $(btn.getAttribute("data-target")).value = btn.getAttribute("data-preset");
                updateTrafficStatus();
                refreshTrafficButtons();
                setMsg($("trafficMsg"), "");
            });
        });
        $("trafficSave").addEventListener("click", saveTraffic);
        $("trafficReset").addEventListener("click", resetTraffic);
    }

    // ========================================================================
    // GROUP 4: ACCOUNT SECTION
    // Create the first admin account, or change username/password.
    // ========================================================================

    // setAccountEnabled(): lock/unlock the account inputs
    function setAccountEnabled(on) {
        ["accUsername", "accNewPassword", "accConfirmPassword", "accCurrentPassword"].forEach(function (id) {
            $(id).disabled = !on;
        });
        $("accountSave").disabled = !on;
    }

    // setAccountMode(): switch between "Create account" and "Update account"
    function setAccountMode(setup) {
        setupMode = setup;
        $("accCurrentWrap").classList.toggle("hidden", setup);
        $("accountSave").textContent = setup ? "Create account" : "Update account";
        $("accountSubtitle").textContent = setup ? "Create the admin account for LanWatch" : "Change your username or password";
    }

    // showAccount(): show the signed-in username and member-since date
    function showAccount(a) {
        savedUsername = a.username;
        setAccountMode(false);
        $("accUsername").value = a.username;
        $("accUsername").placeholder = a.username;
        const d = toDate(a.created_at);
        $("accountInfo").textContent = "Signed in as " + a.username + (d ? " · Member since " + d.toLocaleDateString() : "");
        $("accountLocked").classList.add("hidden");
        setAccountEnabled(true);
    }

    // showSetup(): first run, no account yet -> show the create form
    function showSetup() {
        savedUsername = "";
        setAccountMode(true);
        $("accUsername").value = "";
        $("accountInfo").textContent = "No account yet";
        $("accountLocked").classList.add("hidden");
        setAccountEnabled(true);
        $("accCurrentPassword").disabled = true;
    }

    // lockAccount(): disable the section and explain why
    function lockAccount(message) {
        $("accountInfo").textContent = "Not available";
        $("accountLocked").textContent = message;
        $("accountLocked").classList.remove("hidden");
        setAccountEnabled(false);
    }

    // validateAccount(): check the form and build the request body (or an error)
    function validateAccount() {
        const rawName = $("accUsername").value.trim();
        const username = rawName || savedUsername;
        const pw = $("accNewPassword").value;
        const confirm = $("accConfirmPassword").value;
        const current = $("accCurrentPassword").value;

        if (!username) return { error: "Enter a username.", focus: "accUsername" };
        if (!USERNAME_RE.test(username)) return { error: "Username must be 3 to 32 characters: letters, numbers, dots, dashes or underscores.", focus: "accUsername" };
        if (setupMode) {
            if (pw.length < PASSWORD_MIN) return { error: "Choose a password of at least " + PASSWORD_MIN + " characters.", focus: "accNewPassword" };
            if (pw !== confirm) return { error: "The passwords don't match.", focus: "accConfirmPassword" };
            return { body: { username: username, new_password: pw } };
        }
        if (pw && pw.length < PASSWORD_MIN) return { error: "New password must be at least " + PASSWORD_MIN + " characters.", focus: "accNewPassword" };
        if (pw !== confirm) return { error: "The new passwords don't match.", focus: "accConfirmPassword" };

        const changedName = username !== savedUsername;
        if (!changedName && !pw) return { error: "There's nothing to change yet.", focus: "accUsername" };
        if (!current) return { error: "Enter your current password to save changes.", focus: "accCurrentPassword" };

        const body = { current_password: current };
        if (changedName) body.username = username;
        if (pw) body.new_password = pw;
        return { body: body };
    }

    // saveAccount(): Trigger "Create account" / "Update account" (or Enter)
    // -> POST /api/account/ (first run) or PUT /api/account/ -> show the result
    async function saveAccount() {
        const v = validateAccount();
        if (v.error) { setMsg($("accountMsg"), v.error, "error"); $(v.focus).focus(); return; }

        $("accountSave").disabled = true;
        setMsg($("accountMsg"), "Saving…");
        try {
            const wasSetup = setupMode;
            const updated = await request(ACCOUNT_URL, wasSetup ? "POST" : "PUT", v.body);
            showAccount(updated);
            ["accNewPassword", "accConfirmPassword", "accCurrentPassword"].forEach(function (id) { $(id).value = ""; });
            setMsg($("accountMsg"),
                wasSetup ? "Account created." : (v.body.new_password ? "Account updated. Your new password is active." : "Account updated."),
                "ok");
        } catch (err) {
            setMsg($("accountMsg"), err.message, "error");
            if (err.status === 403) $("accCurrentPassword").focus();
            if (err.status === 409 && setupMode) { loadAccount(); return; } // an account was created elsewhere
            $("accountSave").disabled = false;
        }
    }

    // loadAccount(): GET /api/account/ and show the right form
    async function loadAccount() {
        setAccountEnabled(false);
        try {
            showAccount(await request(ACCOUNT_URL, "GET"));
        } catch (err) {
            if (err.status === 404 && err.data && err.data.setup_required) showSetup();
            else if (err.status === 401) lockAccount("You need to be signed in to change account details.");
            else if (err.status === 404 && !err.network) lockAccount("This account no longer exists.");
            else lockAccount(err.message);
        }
    }

    // initAccount(): connect the Save button and the Enter key
    function initAccount() {
        $("accountSave").addEventListener("click", saveAccount);
        ["accUsername", "accNewPassword", "accConfirmPassword", "accCurrentPassword"].forEach(function (id) {
            $(id).addEventListener("input", function () { setMsg($("accountMsg"), ""); });
            $(id).addEventListener("keydown", function (e) {
                if (e.key === "Enter" && !$("accountSave").disabled) saveAccount();
            });
        });
    }

    // ========================================================================
    // GROUP 4b: AI ASSISTANT SECTION
    // Two switches (ai_enabled, ai_auto_summary) saved with PUT /api/settings/,
    // and a status line from GET /api/ai/status (is the model installed?).
    // ========================================================================

    // applyAi(): put the saved switches on the page
    function applyAi(s) {
        $("aiEnabled").checked = !!s.ai_enabled;
        $("aiAutoSummary").checked = !!s.ai_auto_summary;
        $("aiAutoSummary").disabled = !s.ai_enabled;
        refreshAiButton();
    }

    // refreshAiButton(): only allow saving when a switch was changed
    function refreshAiButton() {
        $("aiSave").disabled = !savedSettings ||
            ($("aiEnabled").checked === !!savedSettings.ai_enabled &&
             $("aiAutoSummary").checked === !!savedSettings.ai_auto_summary);
    }

    // loadAiStatus(): "AI ready: model found ..." / "model not installed"
    async function loadAiStatus() {
        const line = $("aiStatusLine");
        try {
            const st = await request("/api/ai/status", "GET");
            if (!st.available) {
                line.textContent = "The AI model isn't installed on this computer, so AI explanations aren't available. Everything else works as normal.";
                line.className = "rounded-lg bg-[#090D16] p-3 font-mono text-xs text-[#FFB4AB]";
            } else {
                line.textContent = "AI model installed. " + (st.enabled ? "The assistant is on" : "The assistant is off") +
                    (st.loaded ? " and loaded in memory." : (st.enabled ? " (it loads the first time it's needed)." : ".")) +
                    (st.last_error ? " Last problem: " + st.last_error : "");
                line.className = "rounded-lg bg-[#090D16] p-3 font-mono text-xs " + (st.enabled ? "text-[#34D399]" : "text-slate-400");
            }
        } catch (err) {
            line.textContent = "Couldn't check the AI: " + err.message;
        }
    }

    // saveAi(): Trigger "Save changes" (AI Assistant) -> PUT /api/settings/
    async function saveAi() {
        $("aiSave").disabled = true;
        setMsg($("aiMsg"), "Saving…");
        try {
            const updated = await request(SETTINGS_URL, "PUT", {
                ai_enabled: $("aiEnabled").checked,
                ai_auto_summary: $("aiAutoSummary").checked
            });
            savedSettings = Object.assign({}, savedSettings, {
                ai_enabled: updated.ai_enabled, ai_auto_summary: updated.ai_auto_summary
            });
            applyAi(savedSettings);
            setMsg($("aiMsg"), "Settings saved.", "ok");
            loadAiStatus();
        } catch (err) {
            setMsg($("aiMsg"), err.message, "error");
            refreshAiButton();
        }
    }

    // initAi(): connect the two switches and the Save button
    function initAi() {
        $("aiEnabled").addEventListener("change", function () {
            $("aiAutoSummary").disabled = !$("aiEnabled").checked;
            refreshAiButton();
            setMsg($("aiMsg"), "");
        });
        $("aiAutoSummary").addEventListener("change", function () {
            refreshAiButton();
            setMsg($("aiMsg"), "");
        });
        $("aiSave").addEventListener("click", saveAi);
    }

    // ========================================================================
    // GROUP 5: START-UP
    // ========================================================================

    // init(): connect all three sections, then load settings and account
    function init() {
        const clock = $("datetime");
        if (clock) clock.textContent = new Date().toLocaleString();

        initScan();
        initTraffic();
        initAccount();
        initAi();
        initLock();
        // shared with phone_settings.js (Phones and tablets section)
        window.LanWatchSettings = { request: request, setMsg: setMsg, toDate: toDate };
        checkLock();        // loads the settings once unlocked (startPage)
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();