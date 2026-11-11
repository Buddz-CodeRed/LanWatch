// ============================================================================
// FILE: app/static/js/ai_explain.js  -  SHARED "EXPLAIN THIS" BUTTONS (local AI)
// ----------------------------------------------------------------------------
// Used by: alerts.js (each alert), devices.js (each open port),
//          advice.js (each recommendation). Self-contained (no utils.js needed).
//
// How a page uses it: inside its HTML it writes
//     AiExplain.slot("alert", alertId, null, "Explain this")
// which becomes a small box that shows, depending on the state:
//     nothing             - the AI is switched off or not installed
//     [Explain this]      - a button
//     "Writing..."        - while the AI works (can take up to ~30 s)
//     the explanation     - labelled as an AI explanation that may contain mistakes
//     an error + retry    - if the AI couldn't answer
//
// Flow: page opens -> loadStatus() -> GET /api/ai/status (routes/ai.py)
//       user clicks the button -> requestExplanation()
//       -> POST /api/ai/explain {kind, id, port} (routes/ai.py explain)
//       -> the answer is kept in `explanations`, so when the page redraws
//          itself every few seconds the explanation stays on screen.
// ============================================================================

(function () {

    // ========================================================================
    // SETUP: STATE
    // ========================================================================
    let aiReady = null;                 // null = not known yet, true/false from the server
    const explanations = new Map();     // "kind:id[:port]" -> {status, text, error}


    // ========================================================================
    // GROUP 1: SMALL HELPERS
    // ========================================================================

    // esc(): make text safe to put into the page
    function esc(text) {
        const div = document.createElement("div");
        div.textContent = text == null ? "" : String(text);
        return div.innerHTML;
    }

    // keyOf(): one name per explainable thing, e.g. "port:3:23"
    function keyOf(kind, id, port) {
        return port == null || port === "" ? `${kind}:${id}` : `${kind}:${id}:${port}`;
    }

    // paragraphs(): the AI's text with its line breaks kept
    function paragraphs(text) {
        return String(text).split(/\n{2,}/)
            .map(p => `<p class="mt-2 first:mt-0">${esc(p).replace(/\n/g, "<br>")}</p>`).join("");
    }


    // ========================================================================
    // GROUP 2: DRAWING A SLOT
    // ========================================================================

    // inner(): what goes inside one slot right now
    function inner(kind, id, port, label) {
        if (aiReady !== true) return "";        // AI off / not installed: show nothing
        const state = explanations.get(keyOf(kind, id, port));
        const btn = "rounded-lg border border-[#8B5CF6]/30 bg-[#8B5CF6]/10 px-3 py-1.5 text-xs font-medium text-[#C4ABFF] hover:bg-[#8B5CF6]/20 transition-colors";

        if (!state) {
            return `<button type="button" data-ai-explain class="${btn} inline-flex items-center gap-1.5">
                        <span class="material-symbols-outlined text-sm">auto_awesome</span>${esc(label)}
                    </button>`;
        }
        if (state.status === "loading") {
            return `<p class="flex items-center gap-2 text-xs text-[#C4ABFF]">
                        <span class="inline-block h-3 w-3 animate-spin rounded-full border-2 border-[#C4ABFF] border-t-transparent"></span>
                        Writing a plain-English explanation… this can take up to 30 seconds.
                    </p>`;
        }
        if (state.status === "error") {
            return `<p class="text-xs text-[#FFB4AB]">${esc(state.error)}
                        <button type="button" data-ai-explain class="ml-2 underline hover:text-white">Try again</button></p>`;
        }
        return `<div class="rounded-lg border border-[#8B5CF6]/25 bg-[#8B5CF6]/5 p-3">
                    <div class="mb-1 flex items-center justify-between gap-2">
                        <p class="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider text-[#C4ABFF]">
                            <span class="material-symbols-outlined text-sm">auto_awesome</span>AI explanation
                        </p>
                        <button type="button" data-ai-hide class="text-xs text-slate-500 hover:text-slate-300">Hide</button>
                    </div>
                    <div class="text-sm leading-relaxed text-slate-200">${paragraphs(state.text)}</div>
                    <p class="mt-2 text-[11px] text-slate-500">Written by the AI on this computer from LanWatch's facts. It may contain mistakes, so check it against the details shown above.</p>
                </div>`;
    }

    // slot(): the HTML a page puts where the button/explanation should appear
    function slot(kind, id, port, label) {
        return `<div class="mt-2" data-ai-slot data-kind="${esc(kind)}" data-id="${esc(id)}"
                     data-port="${port == null ? "" : esc(port)}" data-label="${esc(label || "Explain this")}">${inner(kind, id, port, label || "Explain this")}</div>`;
    }

    // refresh(): redraw every slot on the page (or only those for `key`)
    function refresh(key) {
        document.querySelectorAll("[data-ai-slot]").forEach(function (el) {
            const d = el.dataset;
            if (key && keyOf(d.kind, d.id, d.port) !== key) return;
            el.innerHTML = inner(d.kind, d.id, d.port === "" ? null : d.port, d.label);
        });
    }


    // ========================================================================
    // GROUP 3: TALKING TO THE SERVER
    // ========================================================================

    // loadStatus(): is the AI switched on and installed? (decides if buttons show)
    async function loadStatus() {
        try {
            const res = await fetch("/api/ai/status");
            aiReady = res.ok ? !!(await res.json()).ready : false;
        } catch (e) {
            aiReady = false;
        }
        refresh();
    }

    // requestExplanation(): Trigger "Explain this" -> POST /api/ai/explain
    async function requestExplanation(kind, id, port) {
        const key = keyOf(kind, id, port);
        explanations.set(key, { status: "loading" });
        refresh(key);

        const body = { kind: kind, id: Number(id) };
        if (port != null && port !== "") body.port = Number(port);
        try {
            const res = await fetch("/api/ai/explain", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(body)
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok || !data.text) throw new Error(data.error || "The AI couldn't answer this time.");
            explanations.set(key, { status: "done", text: data.text });
        } catch (err) {
            const message = err instanceof TypeError ? "Couldn't reach LanWatch. Check that it is running." : err.message;
            explanations.set(key, { status: "error", error: message });
        }
        refresh(key);
    }

    // one click handler for every slot on the page (slots are redrawn often)
    document.addEventListener("click", function (event) {
        const slotEl = event.target.closest("[data-ai-slot]");
        if (!slotEl) return;
        const d = slotEl.dataset;
        if (event.target.closest("[data-ai-explain]")) {
            requestExplanation(d.kind, d.id, d.port);
        } else if (event.target.closest("[data-ai-hide]")) {
            explanations.delete(keyOf(d.kind, d.id, d.port));   // asking again is instant (server cache)
            refresh(keyOf(d.kind, d.id, d.port));
        }
    });

    window.AiExplain = { slot: slot, refresh: refresh, isReady: function () { return aiReady === true; } };
    loadStatus();
})();
