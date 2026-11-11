// ============================================================================
// FILE: app/static/js/admin_lock.js  -  "ADMIN PASSWORD REQUIRED" DIALOG
// ----------------------------------------------------------------------------
// Some actions need the admin password (app/auth.py). This draws the password
// box when one of them is tried while locked, so the action can carry on once
// the right password is entered.
//
// Used by: alerts.js ("Mark as resolved").
// (The Settings page has its own full-page version: settings.js GROUP 0.)
//
// How a page uses it:
//     const ok = await AdminLock.unlock("Resolving an alert needs the admin password.");
//     if (ok) { ...try the action again... }
//
// Flow: AdminLock.unlock() -> draws the dialog -> user types the password
//       -> POST /api/auth/unlock (routes/auth.py) -> true (unlocked)
//       or "Cancel" -> false. Unlocking lasts until 15 minutes unused, and
//       also unlocks the Settings page (it's the same admin unlock).
// ============================================================================

(function () {

    // build(): create the dialog the first time it's needed
    function build() {
        let box = document.getElementById("adminLockDialog");
        if (box) return box;
        box = document.createElement("div");
        box.id = "adminLockDialog";
        box.className = "fixed inset-0 z-[60] hidden items-center justify-center bg-[#090D16]/80 p-5 backdrop-blur-md";
        box.setAttribute("role", "dialog");
        box.setAttribute("aria-modal", "true");
        box.innerHTML = `
            <form class="w-full max-w-sm rounded-xl border border-white/10 bg-[#0F172A] p-6 shadow-2xl">
                <div class="mb-4 flex items-center gap-3">
                    <span class="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-[#00F2FE]/10 text-[#00F2FE]">
                        <span class="material-symbols-outlined">lock</span>
                    </span>
                    <div>
                        <h3 class="font-bold text-white">Admin password required</h3>
                        <p data-lock-reason class="text-xs text-slate-400"></p>
                    </div>
                </div>
                <label class="mb-1 block font-mono text-xs uppercase tracking-wider text-slate-400" for="adminLockPassword">Admin password</label>
                <input id="adminLockPassword" type="password" autocomplete="current-password" maxlength="128"
                    class="font-mono w-full rounded-lg border border-white/10 bg-[#090D16] px-4 py-2.5 text-sm text-[#00F2FE] focus:border-[#00F2FE] focus:outline-none focus:ring-1 focus:ring-[#00F2FE]">
                <p data-lock-msg class="mt-2 min-h-[1.25rem] font-mono text-xs text-[#FFB4AB]" role="status" aria-live="polite"></p>
                <div class="mt-2 grid grid-cols-2 gap-3">
                    <button type="button" data-lock-cancel class="h-10 rounded-lg border border-white/10 text-sm text-slate-300 hover:border-white/30">Cancel</button>
                    <button type="submit" data-lock-submit class="h-10 rounded-lg bg-gradient-to-r from-[#00F2FE] to-[#8B5CF6] font-bold text-[#090D16] disabled:opacity-50">Unlock</button>
                </div>
                <p class="mt-4 text-xs text-slate-500">Stays unlocked until 15 minutes without use.</p>
            </form>`;
        document.body.appendChild(box);
        return box;
    }

    // unlock(): ask for the password. Resolves true once unlocked, false if cancelled.
    function unlock(reason) {
        const box = build();
        const form = box.querySelector("form");
        const input = box.querySelector("#adminLockPassword");
        const msg = box.querySelector("[data-lock-msg]");
        const submit = box.querySelector("[data-lock-submit]");
        box.querySelector("[data-lock-reason]").textContent = reason || "This action is protected.";
        msg.textContent = "";
        input.value = "";
        box.classList.remove("hidden");
        box.classList.add("flex");
        setTimeout(() => input.focus(), 50);

        return new Promise(function (resolve) {
            function close(result) {
                box.classList.add("hidden");
                box.classList.remove("flex");
                form.onsubmit = null;
                box.querySelector("[data-lock-cancel]").onclick = null;
                document.removeEventListener("keydown", onKey);
                resolve(result);
            }
            function onKey(e) { if (e.key === "Escape") close(false); }
            document.addEventListener("keydown", onKey);
            box.querySelector("[data-lock-cancel]").onclick = () => close(false);

            // Trigger "Unlock" -> POST /api/auth/unlock
            form.onsubmit = async function (event) {
                event.preventDefault();
                if (!input.value) { msg.textContent = "Enter the admin password."; return; }
                submit.disabled = true;
                try {
                    const res = await fetch("/api/auth/unlock", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ password: input.value })
                    });
                    const data = await res.json().catch(() => ({}));
                    if (res.ok) { close(true); return; }
                    msg.textContent = data.error || "That didn't work. Try again.";
                    input.value = "";
                    const wait = data.wait_seconds;
                    setTimeout(() => { submit.disabled = false; input.focus(); }, wait ? wait * 1000 : 0);
                } catch (err) {
                    msg.textContent = "Couldn't reach LanWatch. Check that it is running.";
                    submit.disabled = false;
                }
            };
        });
    }

    window.AdminLock = { unlock: unlock };
})();
