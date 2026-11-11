// ============================================================================
// FILE: app/static/js/backups.js  -  "BACKUPS" (Settings page)
// ----------------------------------------------------------------------------
// Page: templates/settings.html   Needs: settings.js loaded first
//       (window.LanWatchSettings.request: API calls that re-show the password
//        dialog when the Settings unlock has expired)
//
// Flow (opening):  settings.js unlocks -> event "lanwatch:settings-unlocked"
//                  -> loadBackups() -> GET /api/backups/ -> renderList()
// Flow (back up):  "Back up now" -> backUpNow() -> POST /api/backups/
// Flow (add file): "Add a backup file" -> uploadFile() -> POST /api/backups/upload
//                  (checked by the server, then listed; nothing is replaced yet)
// Flow (restore):  "Restore" -> confirm -> restore() -> POST /api/backups/<name>/restore
//                  -> the page reloads and asks for the admin password again
// Flow (download): "Download" is a plain link: GET /api/backups/<name>/download
// Server side: routes/backup.py, services/backup.py
// ============================================================================

(function () {
    "use strict";
    const $ = (id) => document.getElementById(id);
    if (!$("backupList")) return;

    const api = () => window.LanWatchSettings;

    function esc(text) {
        return String(text == null ? "" : text).replace(/[&<>"']/g, c =>
            ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    }

    // "2026-11-12T14:05:33" (this computer's time) -> "12/11/2026, 2:05 PM"
    function when(value) {
        const d = new Date(value);
        return isNaN(d) ? value : d.toLocaleString([], { dateStyle: "short", timeStyle: "short" });
    }

    function size(bytes) {
        return bytes >= 1048576 ? (bytes / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(bytes / 1024)) + " KB";
    }

    function setBusy(busy) {
        $("backupNow").disabled = busy;
        $("backupFile").disabled = busy;
        document.querySelectorAll("#backupList button").forEach(b => { b.disabled = busy; });
    }


    // ========================================================================
    // GROUP 1: DRAWING
    // ========================================================================

    const SHOW_FIRST = 5;        // newest backups shown before "Show all"
    let showAll = false;
    let last = null;             // the last list from the server (for "Show all")

    function renderList(data, highlight) {
        last = { data: data, highlight: highlight };
        $("backupKeep").textContent = data.keep;
        $("backupFolder").textContent = "Saved in: " + data.folder;
        const all = data.backups || [];
        const list = showAll ? all : all.slice(0, SHOW_FIRST);
        const more = all.length > SHOW_FIRST
            ? `<button type="button" data-showall class="self-start text-xs text-[#00F2FE] hover:underline">${showAll ? "Show fewer" : `Show all ${all.length} backups`}</button>`
            : "";
        $("backupList").innerHTML = list.length
            ? list.map(b => `
                <div class="flex flex-col gap-3 rounded-lg border ${b.name === highlight ? "border-[#00F2FE]/40" : "border-white/10"} bg-[#090D16] p-3 sm:flex-row sm:items-center sm:justify-between">
                    <div class="min-w-0">
                        <p class="text-sm text-white">${esc(when(b.created))}${b.name === highlight ? ` <span class="text-xs text-[#34D399]">(new)</span>` : ""}</p>
                        <p class="font-mono text-xs text-slate-500">${esc(b.reason)} · ${size(b.size)}${b.problem ? "" : ` · ${b.devices} devices · ${b.scans} scans`}</p>
                        ${b.problem ? `<p class="mt-1 text-xs text-[#FFB4AB]">${esc(b.problem)}</p>` : ""}
                    </div>
                    <div class="flex shrink-0 gap-2">
                        <a href="/api/backups/${encodeURIComponent(b.name)}/download" download class="whitespace-nowrap rounded-lg border border-white/10 px-3 py-1.5 text-xs text-slate-300 hover:border-white/30 hover:text-white">Download</a>
                        ${b.problem ? "" : `<button type="button" data-restore="${esc(b.name)}" data-when="${esc(when(b.created))}" class="whitespace-nowrap rounded-lg border border-[#F59E0B]/40 px-3 py-1.5 text-xs text-[#FBBF24] hover:bg-[#F59E0B]/10 disabled:opacity-40">Restore</button>`}
                    </div>
                </div>`).join("") + more
            : `<p class="text-sm text-slate-500">No backups yet. Click <b>Back up now</b> to make the first one.</p>`;
    }


    // ========================================================================
    // GROUP 2: ACTIONS
    // ========================================================================

    async function loadBackups(highlight) {
        try {
            renderList(await api().request("/api/backups/", "GET"), highlight);
        } catch (err) {
            if (err.status === 401) return;          // locked again: settings.js shows the dialog
            $("backupList").innerHTML = `<p class="text-sm text-[#FFB4AB]">Couldn't list the backups: ${esc(err.message)}</p>`;
        }
    }

    async function backUpNow() {
        setBusy(true);
        api().setMsg($("backupMsg"), "Backing up…");
        try {
            const data = await api().request("/api/backups/", "POST");
            renderList(data, data.created);
            api().setMsg($("backupMsg"), "Backed up. Download it too if you want a copy off this computer.", "ok");
        } catch (err) {
            api().setMsg($("backupMsg"), err.message, "error");
        }
        setBusy(false);
    }

    async function uploadFile() {
        const file = $("backupFile").files[0];
        $("backupFile").value = "";                  // choosing the same file again still works
        if (!file) return;
        setBusy(true);
        api().setMsg($("backupMsg"), "Checking " + file.name + "…");
        const form = new FormData();
        form.append("file", file);
        try {
            const res = await fetch("/api/backups/upload", { method: "POST", body: form, cache: "no-store" });
            const data = await res.json().catch(() => null);
            if (res.status === 401) {
                await api().request("/api/backups/", "GET").catch(() => {});   // shows the password dialog
                throw new Error("Settings locked. Unlock, then add the file again.");
            }
            if (!res.ok) throw new Error((data && data.error) || "The server returned an error (HTTP " + res.status + ").");
            renderList(data, data.created);
            api().setMsg($("backupMsg"), "The file is a good LanWatch backup and has been added to the list. Nothing has changed yet: click Restore on it to use it.", "ok");
        } catch (err) {
            api().setMsg($("backupMsg"), err.message, "error");
        }
        setBusy(false);
    }

    async function restore(name, label) {
        if (!confirm(`Restore the backup from ${label}?\n\nLanWatch's devices, scans, alerts and settings go back to how they were then. ` +
                     `Your current data is backed up first, so you can undo this.\n\nAfterwards, unlock Settings with the admin password from that backup.`)) return;
        setBusy(true);
        api().setMsg($("backupMsg"), "Restoring…");
        try {
            await api().request(`/api/backups/${encodeURIComponent(name)}/restore`, "POST");
            api().setMsg($("backupMsg"), "Restored. Reloading…", "ok");
            setTimeout(() => location.reload(), 1500);
        } catch (err) {
            api().setMsg($("backupMsg"), err.message, "error");
            setBusy(false);
        }
    }


    // ========================================================================
    // GROUP 3: START-UP
    // ========================================================================

    $("backupNow").addEventListener("click", backUpNow);
    $("backupFile").addEventListener("change", uploadFile);
    $("backupList").addEventListener("click", event => {
        if (event.target.closest("[data-showall]")) {
            showAll = !showAll;
            renderList(last.data, last.highlight);
            return;
        }
        const btn = event.target.closest("[data-restore]");
        if (btn) restore(btn.dataset.restore, btn.dataset.when);
    });
    document.addEventListener("lanwatch:settings-unlocked", () => loadBackups());
})();
