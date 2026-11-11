// ============================================================================
// FILE: app/static/js/phone_settings.js  -  "PHONES AND TABLETS" (Settings page)
// ----------------------------------------------------------------------------
// Page: templates/settings.html   Needs: settings.js loaded first
//       (window.LanWatchSettings.request: API calls that re-show the password
//        dialog when the Settings unlock has expired)
//
// Flow (opening): settings.js unlocks -> event "lanwatch:settings-unlocked"
//                 -> loadPhone() -> GET /api/phone/status + /api/phone/sessions
//                 -> renderStatus() (address, QR code, problems) + renderSessions()
// Flow (saving):  "Save changes" -> savePhone() -> PUT /api/phone/settings
//                 (the server starts/stops the home-network address at once)
// Flow (sign out): "Sign out" / "Sign out all" -> DELETE /api/phone/sessions[/<id>]
// Flow (allow):   a phone that isn't recognised yet shows under "Waiting to be
//                 let in" -> "Allow" -> POST /api/phone/waiting/<id>/allow
// While the page is open the status refreshes every 5 s (a phone asking to be
// let in, a network change, a phone that has just signed in).
// Server side: routes/phone.py, app/phone_access.py, services/phone_listener.py
// ============================================================================

(function () {
    "use strict";
    const $ = (id) => document.getElementById(id);
    if (!$("phoneStatus")) return;

    const REFRESH_MS = 5000;     // quick, so a phone waiting to be let in shows up fast
    let saved = null;            // { enabled, recognised_only } as last saved
    let timer = null;

    const api = () => window.LanWatchSettings;

    function esc(text) {
        return String(text == null ? "" : text).replace(/[&<>"']/g, c =>
            ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    }

    // "2026-11-11 08:39:14" (UTC) -> "11/11/2026, 9:39 PM"
    function fmt(value) {
        const d = api().toDate(value);
        return d ? d.toLocaleString([], { dateStyle: "short", timeStyle: "short" }) : "";
    }


    // ========================================================================
    // GROUP 1: DRAWING
    // ========================================================================

    function refreshSaveButton() {
        const changed = saved && ($("phoneEnabled").checked !== saved.enabled ||
                                  $("phoneRecognisedOnly").checked !== saved.recognised_only);
        $("phoneSave").disabled = !changed;
    }

    // renderStatus(): off / the address to open (+ QR code) / what's in the way
    function renderStatus(st) {
        const box = $("phoneStatus");
        // only redraw when something changed (the QR code would flicker)
        const show = (html) => { if (box.dataset.shown !== html) { box.innerHTML = html; box.dataset.shown = html; } };
        const problems = (st.problems || []).map(p =>
            `<p class="mt-2 rounded-lg border border-[#F59E0B]/30 bg-[#F59E0B]/10 p-2.5 font-sans text-sm text-[#FBBF24]">${esc(p)}</p>`).join("");

        if (!st.enabled) {
            show(`<p class="font-sans text-sm text-slate-400">Off. Only this computer can open LanWatch.</p>`);
            return;
        }
        if (!st.running || !st.url) {
            show(`<p class="font-sans text-sm text-white">On, but phones can't connect right now.</p>${problems}`);
            return;
        }
        show(renderWaiting(st.waiting) + `
            <div class="flex flex-col gap-4 sm:flex-row sm:items-center">
                <img src="/api/phone/qr.svg?u=${encodeURIComponent(st.url)}" alt="QR code for ${esc(st.url)}"
                     class="h-40 w-40 shrink-0 self-center rounded-lg bg-white p-1 sm:self-auto" width="160" height="160">
                <div class="min-w-0 font-sans">
                    <p class="text-sm text-slate-300">On a phone connected to your home Wi-Fi, scan this code with the camera, or type:</p>
                    <p class="mt-1 break-all font-mono text-lg font-semibold text-[#00F2FE]">${esc(st.url)}</p>
                    <p class="mt-2 text-xs text-slate-400">Then sign in with the admin username and password.
                        ${st.recognised_only ? "A phone LanWatch doesn't recognise yet appears above with an <b>Allow</b> button." : ""}
                        Tip: use the browser's <b>Add to Home screen</b> to open it like an app.</p>
                    ${st.firewall === "ok" ? `<p class="mt-2 text-xs text-[#34D399]">Windows Firewall: allowed on home (Private) networks only.</p>` : ""}
                </div>
            </div>${problems}`);
    }

    // renderWaiting(): unrecognised phones that tried to open LanWatch, with "Allow"
    function renderWaiting(list) {
        if (!list || !list.length) return "";
        return `
            <div class="mb-4 rounded-lg border border-[#00F2FE]/30 bg-[#00F2FE]/5 p-3 font-sans">
                <p class="text-sm font-semibold text-white">Waiting to be let in</p>
                <p class="mt-0.5 text-xs text-slate-400">Only allow a device if it's yours and you've just opened LanWatch on it.</p>
                <div class="mt-2 space-y-2">${list.map(w => `
                    <div class="flex items-center justify-between gap-3 rounded-lg border border-white/10 bg-[#090D16] p-3">
                        <div class="min-w-0">
                            <p class="truncate text-sm text-white">${esc(w.label)}</p>
                            <p class="truncate font-mono text-xs text-slate-500">${esc(w.ip)}${w.mac ? " · " + esc(w.mac) : ""}</p>
                        </div>
                        <button type="button" data-allow="${w.device_id}" class="shrink-0 whitespace-nowrap rounded-lg bg-[#00F2FE] px-3 py-1.5 text-xs font-semibold text-[#090D16] hover:bg-[#00F2FE]/80">Allow</button>
                    </div>`).join("")}
                </div>
            </div>`;
    }

    // renderSessions(): one row per signed-in phone, with "Sign out"
    function renderSessions(list) {
        $("phoneSignOutAll").classList.toggle("hidden", list.length < 2);
        $("phoneSessions").innerHTML = list.length
            ? list.map(s => `
                <div class="flex items-center justify-between gap-3 rounded-lg border border-white/10 bg-[#090D16] p-3">
                    <div class="min-w-0">
                        <p class="truncate text-sm text-white">${esc(s.device_label)}${s.this_device ? ` <span class="text-xs text-[#34D399]">(this device)</span>` : ""}</p>
                        <p class="truncate font-mono text-xs text-slate-500">${esc(s.ip_address)} · last used ${esc(fmt(s.last_used))}</p>
                    </div>
                    <button type="button" data-signout="${s.session_id}" class="shrink-0 whitespace-nowrap rounded-lg border border-white/10 px-3 py-1.5 text-xs text-slate-300 hover:border-[#FFB4AB]/40 hover:text-[#FFB4AB]">Sign out</button>
                </div>`).join("")
            : `<p class="text-sm text-slate-500">No phones or tablets are signed in.</p>`;
    }


    // ========================================================================
    // GROUP 2: LOADING AND SAVING
    // ========================================================================

    async function loadPhone(fromTimer) {
        try {
            const [st, sessions] = await Promise.all([
                api().request("/api/phone/status", "GET"),
                api().request("/api/phone/sessions", "GET"),
            ]);
            // don't overwrite switches the user is changing
            if (!fromTimer || $("phoneSave").disabled) {
                $("phoneEnabled").checked = st.enabled;
                $("phoneRecognisedOnly").checked = st.recognised_only;
            }
            saved = { enabled: st.enabled, recognised_only: st.recognised_only };
            refreshSaveButton();
            renderStatus(st);
            renderSessions(sessions.sessions || []);
        } catch (err) {
            if (err.status === 401) return;          // locked again: settings.js shows the dialog
            $("phoneStatus").textContent = "Couldn't check phone access: " + err.message;
            delete $("phoneStatus").dataset.shown;
        }
    }

    async function savePhone() {
        const enabled = $("phoneEnabled").checked;
        if (!enabled && saved && saved.enabled) {
            const st = await api().request("/api/phone/status", "GET").catch(() => ({}));
            if (st.this_device_is_remote && !confirm("You're using LanWatch on a phone or tablet. Switching this off disconnects it straight away. Continue?")) return;
        }
        $("phoneSave").disabled = true;
        api().setMsg($("phoneMsg"), "Saving…");
        try {
            const st = await api().request("/api/phone/settings", "PUT", {
                enabled: enabled,
                recognised_only: $("phoneRecognisedOnly").checked,
            });
            saved = { enabled: st.enabled, recognised_only: st.recognised_only };
            renderStatus(st);
            api().setMsg($("phoneMsg"), st.enabled ? "Saved. Phone access is on." : "Saved. Phone access is off.", "ok");
        } catch (err) {
            api().setMsg($("phoneMsg"), err.message, "error");
        }
        refreshSaveButton();
    }

    async function allowDevice(id) {
        try {
            await api().request(`/api/phone/waiting/${id}/allow`, "POST");
            api().setMsg($("phoneMsg"), "Allowed. The phone moves on to the sign-in page within a few seconds.", "ok");
        } catch (err) {
            api().setMsg($("phoneMsg"), err.message, "error");
        }
        loadPhone();
    }

    async function signOut(id) {
        try {
            await api().request(id ? `/api/phone/sessions/${id}` : "/api/phone/sessions", "DELETE");
            api().setMsg($("phoneMsg"), id ? "Signed out." : "All phones and tablets signed out.", "ok");
        } catch (err) {
            api().setMsg($("phoneMsg"), err.message, "error");
        }
        loadPhone();
    }


    // ========================================================================
    // GROUP 3: USER ACTIONS AND START-UP
    // ========================================================================

    $("phoneEnabled").addEventListener("change", () => { refreshSaveButton(); api().setMsg($("phoneMsg"), ""); });
    $("phoneRecognisedOnly").addEventListener("change", () => { refreshSaveButton(); api().setMsg($("phoneMsg"), ""); });
    $("phoneSave").addEventListener("click", savePhone);
    $("phoneSignOutAll").addEventListener("click", () => {
        if (confirm("Sign out every phone and tablet? They'll need the password again.")) signOut(null);
    });
    $("phoneStatus").addEventListener("click", event => {
        const btn = event.target.closest("[data-allow]");
        if (btn) { btn.disabled = true; allowDevice(btn.dataset.allow); }
    });
    $("phoneSessions").addEventListener("click", event => {
        const btn = event.target.closest("[data-signout]");
        if (btn) signOut(btn.dataset.signout);
    });

    document.addEventListener("lanwatch:settings-unlocked", () => {
        loadPhone();
        clearInterval(timer);
        timer = setInterval(() => {
            // not while hidden, and not while the password dialog is up (it would clear what's typed)
            const locked = !$("lockOverlay").classList.contains("hidden");
            if (!document.hidden && !locked) loadPhone(true);
        }, REFRESH_MS);
    });
})();
