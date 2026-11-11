// ============================================================================
// FILE: app/static/js/router_guide.js  -  "YOUR ROUTER" SECTION (Advice page)
// ----------------------------------------------------------------------------
// Page: templates/advice.html   Needs: utils.js loaded first (esc)
//
// Flow (viewing):  page opens -> loadRouter() -> GET /api/knowledge/router
//                  (routes/findings.py -> services/router_advice.py)
//                  -> renderSummary() (router, provider, how to open it)
//                  + renderChecks() (the hardening checklist)
// Flow (updating): provider picker / "Done" tick -> saveRouter()
//                  -> PUT /api/knowledge/router -> redraw
//
// Knowledge: app/knowledge/routers.json (checklist + router brands) and
//            app/knowledge/isps.json (NZ internet providers' routers).
// Advice only: LanWatch never logs in to the router or changes it.
// ============================================================================

(function () {
    const summaryEl = document.getElementById("routerSummary");
    const checksEl = document.getElementById("routerChecks");
    const progressEl = document.getElementById("routerProgressText");
    if (!summaryEl) return;

    const DIFFICULTY = { easy: "Easy", medium: "A little technical", advanced: "Technical" };
    let guide = null;
    const openChecks = new Set();          // checklist items the user expanded


    // ========================================================================
    // GROUP 1: DRAWING
    // ========================================================================

    // a "label: value" line; skipped when there is no value
    function line(label, value) {
        return value ? `<p class="text-sm text-slate-300"><span class="text-slate-500">${label}</span> ${value}</p>` : "";
    }

    // renderSummary(): which router, which provider, how to open its settings
    function renderSummary() {
        const g = guide;
        const r = g.router;
        const routerLine = r
            ? `<a href="/devices?id=${encodeURIComponent(r.device_id)}" class="text-[#00F2FE] hover:underline">${esc(r.name || r.ip)}</a>
               <span class="font-mono text-slate-500"> · ${esc(r.ip)}</span>${r.maker ? ` <span class="text-slate-500">· made by ${esc(r.maker)}</span>` : ""}`
            : (g.gateways.length
                ? `<span class="font-mono">${esc(g.gateways.join(", "))}</span> <span class="text-slate-500">(run a scan so LanWatch can look at it)</span>`
                : `<span class="text-slate-400">LanWatch couldn't tell which device is your router. The steps below still apply.</span>`);

        const options = [`<option value="">Not sure</option>`].concat(g.isp_choices.map(c =>
            `<option value="${esc(c.id)}" ${c.id === g.isp_id ? "selected" : ""}>${esc(c.name)}</option>`)).join("");

        const isp = g.isp;
        const model = g.model;
        const brand = g.brand;
        const howToOpen = (model && model.open) || (brand && brand.open)
            || "Type the address printed on the router's label into a web browser (often http://192.168.1.1 or http://192.168.1.254), or use the maker's app.";
        const help = isp && isp.help_url
            ? `<a href="${esc(isp.help_url)}" target="_blank" rel="noopener noreferrer" class="text-[#00F2FE] hover:underline">${esc(isp.name)} help page</a>
               <span class="text-slate-500">(opens their website; needs the internet)</span>`
            : "";

        summaryEl.innerHTML = `
            <div class="grid gap-4 md:grid-cols-2">
                <div class="flex flex-col gap-1.5">
                    ${line("Router:", routerLine)}
                    ${line("Model:", model ? esc(model.model) : (brand ? esc(brand.name) + " <span class=\"text-slate-500\">(exact model unknown)</span>" : ""))}
                    ${line("How to open its settings:", esc(howToOpen))}
                    ${line("Login:", model && model.login ? esc(model.login) : "")}
                </div>
                <div class="flex flex-col gap-1.5">
                    <label class="text-sm text-slate-500" for="routerIsp">Your internet provider
                        ${g.isp_guessed ? `<span class="ml-1 rounded-full border border-[#00F2FE]/30 px-2 py-0.5 text-[11px] text-[#00F2FE]">guessed from your router</span>` : ""}
                    </label>
                    <select id="routerIsp" class="w-full rounded-lg border border-white/10 bg-[#090D16] px-3 py-2 text-sm text-white focus:border-[#00F2FE]/50 focus:outline-none">${options}</select>
                    ${isp && isp.note ? `<p class="text-sm text-slate-400">${esc(isp.note)}</p>` : ""}
                    ${help ? `<p class="text-sm">${help}</p>` : ""}
                </div>
            </div>
            <p class="text-xs text-slate-500">Advice only: LanWatch never logs in to your router or changes its settings. If your provider manages the router, some options may be hidden or locked; that's normal.</p>`;
    }

    // one checklist item: tick box + title, expands to why / where / steps
    function checkItem(c) {
        const open = openChecks.has(c.id);
        const steps = c.steps.map(s => `<li>${esc(s)}</li>`).join("");
        return `
        <div class="rounded-lg border ${c.done ? "border-[#10B981]/30" : "border-white/10"} bg-[#090D16]">
            <div class="flex items-center gap-3 p-3">
                <input type="checkbox" data-check="${esc(c.id)}" ${c.done ? "checked" : ""}
                       aria-label="Mark '${esc(c.title)}' as done"
                       class="h-5 w-5 shrink-0 cursor-pointer accent-[#10B981]">
                <button type="button" data-toggle="${esc(c.id)}" class="flex min-w-0 flex-1 items-center justify-between gap-2 text-left">
                    <span class="min-w-0">
                        <span class="block text-sm font-semibold ${c.done ? "text-slate-400 line-through" : "text-white"}">${esc(c.title)}</span>
                        <span class="text-xs text-slate-500">${esc(DIFFICULTY[c.difficulty] || c.difficulty)}</span>
                    </span>
                    <span class="material-symbols-outlined shrink-0 text-slate-400">${open ? "expand_less" : "expand_more"}</span>
                </button>
            </div>
            ${open ? `
            <div class="border-t border-white/5 px-4 pb-4 pt-3">
                <p class="text-sm text-slate-300"><span class="text-slate-500">Why it matters:</span> ${esc(c.why)}</p>
                ${c.where ? `<p class="mt-2 rounded-lg border border-[#00F2FE]/20 bg-[#00F2FE]/5 p-2.5 text-sm text-slate-200">
                    <span class="text-[#00F2FE]">On your ${esc(guide.brand ? guide.brand.name : "router")}:</span> ${esc(c.where)}</p>` : ""}
                <p class="mt-3 text-sm font-semibold text-white">What to do</p>
                <ol class="mt-1 list-decimal space-y-1 pl-5 text-sm text-slate-300">${steps}</ol>
            </div>` : ""}
        </div>`;
    }

    function renderChecks() {
        checksEl.innerHTML = guide.checks.map(checkItem).join("");
        progressEl.textContent = `${guide.done_count} of ${guide.checks.length} done`;
    }

    function render() {
        renderSummary();
        renderChecks();
    }


    // ========================================================================
    // GROUP 2: LOADING AND SAVING
    // ========================================================================

    async function loadRouter() {
        try {
            const res = await fetch("/api/knowledge/router");
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            guide = await res.json();
            render();
        } catch (err) {
            console.error("Router guide load error:", err);
            summaryEl.innerHTML = `<p class="py-4 text-center text-sm text-[#FFB4AB]">Couldn't load the router checklist. Check that LanWatch is running.</p>`;
        }
    }

    async function saveRouter(body) {
        try {
            const res = await fetch("/api/knowledge/router", {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(body)
            });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            guide = await res.json();
        } catch (err) {
            console.error("Router guide save error:", err);
            alert("Couldn't save that change. Please try again.");
        }
        render();
    }


    // ========================================================================
    // GROUP 3: USER ACTIONS
    // ========================================================================

    summaryEl.addEventListener("change", event => {
        if (event.target.id === "routerIsp") saveRouter({ isp: event.target.value });
    });

    checksEl.addEventListener("change", event => {
        const box = event.target.closest("[data-check]");
        if (box) saveRouter({ done: { [box.dataset.check]: box.checked } });
    });

    checksEl.addEventListener("click", event => {
        const btn = event.target.closest("[data-toggle]");
        if (!btn) return;
        const id = btn.dataset.toggle;
        openChecks.has(id) ? openChecks.delete(id) : openChecks.add(id);
        renderChecks();
    });

    // a scan may have just found the router
    document.addEventListener("lanwatch:scan-finished", loadRouter);

    loadRouter();
})();
