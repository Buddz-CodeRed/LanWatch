// ============================================================================
// FILE: app/static/js/advice.js  -  SECURITY ADVICE PAGE LOGIC
// ----------------------------------------------------------------------------
// Page: templates/advice.html   Needs: utils.js and ai_explain.js loaded first
//
// Flow (viewing):  page opens -> loadAdvice() -> GET /api/findings/
//                  (routes/findings.py get_findings) -> renderProgress()
//                  + renderTabs() + render() one card per recommendation
//                  -> reloads every 30 s, and straight away when a scan
//                     finishes (event from scan_indicator.js)
// Flow (updating): user clicks "I've done this" / "Not relevant to me" /
//                  "Move back to my list" -> setStatus()
//                  -> POST /api/findings/<id>/status -> loadAdvice()
// Flow (AI):       "Explain in my words" -> ai_explain.js -> POST /api/ai/explain
//
// Recommendations are created after every FULL scan by services/findings.py.
// ============================================================================


// ============================================================================
// SETUP: SETTINGS, PAGE ELEMENTS AND LABELS
// ============================================================================

const ADVICE_REFRESH_MS = 30000;

const tabsEl = document.getElementById("adviceTabs");
const listEl = document.getElementById("adviceList");
const progressBar = document.getElementById("adviceProgressBar");
const progressText = document.getElementById("adviceProgressText");

const TABS = [
    { status: "open", label: "To do" },
    { status: "done", label: "Done" },
    { status: "dismissed", label: "Not relevant" },
];
const SEVERITY = {
    critical: { label: "Critical", cls: "bg-red-500/20 text-red-300 border-red-500/30" },
    high: { label: "High", cls: "bg-orange-500/20 text-orange-300 border-orange-500/30" },
    medium: { label: "Medium", cls: "bg-yellow-500/20 text-yellow-300 border-yellow-500/30" },
    low: { label: "Low", cls: "bg-blue-500/20 text-blue-300 border-blue-500/30" },
};
const DIFFICULTY = { easy: "Easy", medium: "A little technical", advanced: "Technical" };

let findings = [];
let counts = { open: 0, done: 0, dismissed: 0 };
let activeTab = "open";
let lastSignature = "";


// ============================================================================
// GROUP 1: DRAWING THE PAGE
// ============================================================================

// renderProgress(): "4 of 6 done" + the green bar (dismissed ones don't count)
function renderProgress() {
    const total = counts.open + counts.done;
    const percent = total ? Math.round((counts.done / total) * 100) : 0;
    progressBar.style.width = `${total ? percent : 0}%`;
    progressText.textContent = total
        ? `${counts.done} of ${total} done`
        : "Nothing to do yet";
}

// renderTabs(): "To do (3)", "Done (2)", "Not relevant (1)"
function renderTabs() {
    tabsEl.innerHTML = TABS.map(t => {
        const active = t.status === activeTab;
        return `<button type="button" data-tab="${t.status}"
                    class="rounded-lg border px-3 py-1.5 text-sm transition-colors ${active
                        ? "border-[#00F2FE]/40 bg-[#00F2FE]/10 text-[#00F2FE]"
                        : "border-white/10 text-slate-300 hover:border-white/30"}">
                    ${t.label} <span class="font-mono text-xs opacity-70">${counts[t.status] || 0}</span>
                </button>`;
    }).join("");
}

// adviceCard(): one recommendation with its steps and buttons
function adviceCard(f) {
    const sev = SEVERITY[f.severity] || SEVERITY.low;
    const article = f.article || {};
    const deviceName = f.hostname || f.device_ip || "Unknown device";
    const steps = (article.steps || []).map(s => `<li>${esc(s)}</li>`).join("");

    const buttons = f.status === "open"
        ? `<button type="button" data-status="done" data-id="${f.finding_id}"
               class="rounded-lg border border-[#10B981]/40 bg-[#10B981]/10 px-4 py-2 text-sm text-[#34D399] hover:bg-[#10B981]/20">I've done this</button>
           <button type="button" data-status="dismissed" data-id="${f.finding_id}"
               class="rounded-lg border border-white/10 px-4 py-2 text-sm text-slate-300 hover:border-white/30">Not relevant to me</button>`
        : `<button type="button" data-status="open" data-id="${f.finding_id}"
               class="rounded-lg border border-white/10 px-4 py-2 text-sm text-slate-300 hover:border-white/30">Move back to my list</button>`;

    return `
    <article class="rounded-lg border border-white/10 bg-[#090D16] p-4">
        <div class="flex flex-wrap items-center gap-2">
            <span class="rounded-full border px-2.5 py-0.5 text-xs ${sev.cls}">${sev.label}</span>
            ${article.difficulty ? `<span class="rounded-full border border-white/10 px-2.5 py-0.5 text-xs text-slate-400">${esc(DIFFICULTY[article.difficulty] || article.difficulty)}</span>` : ""}
            <span class="font-mono text-xs text-slate-500">updated ${esc(fmtTime(f.updated_at))}</span>
        </div>
        <h4 class="mt-2 font-semibold text-white">${esc(f.title)}</h4>
        <p class="mt-0.5 text-sm text-slate-400">
            Device: <a href="/devices?id=${encodeURIComponent(f.device_id)}" class="text-[#00F2FE] hover:underline">${esc(deviceName)}</a>
            <span class="text-slate-600"> · </span><span class="font-mono">${esc(f.device_ip || "")}</span>
        </p>
        ${f.detail ? `<p class="mt-2 text-sm text-slate-300"><span class="text-slate-500">What LanWatch saw:</span> ${esc(f.detail)}</p>` : ""}
        ${article.why ? `<p class="mt-1 text-sm text-slate-300"><span class="text-slate-500">Why it matters:</span> ${esc(article.why)}</p>` : ""}
        ${steps ? `<div class="mt-3"><p class="text-sm font-semibold text-white">What to do</p>
                   <ol class="mt-1 list-decimal space-y-1 pl-5 text-sm text-slate-300">${steps}</ol></div>` : ""}
        ${window.AiExplain ? AiExplain.slot("finding", f.finding_id, null, "Explain in my words") : ""}
        <div class="mt-3 flex flex-wrap gap-2">${buttons}</div>
    </article>`;
}

// emptyMessage(): what to show when a tab has nothing in it
function emptyMessage() {
    if (activeTab === "open" && counts.done + counts.dismissed + counts.open === 0) {
        return `No security advice yet. Run a <b>Full</b> scan from the <a href="/" class="text-[#00F2FE] hover:underline">dashboard</a>:
                LanWatch then checks the open ports on every device and lists anything worth looking at here.`;
    }
    if (activeTab === "open") return "Nothing left to do. Well done! LanWatch checks again after every full scan.";
    if (activeTab === "done") return "Nothing marked as done yet.";
    return "Nothing marked as not relevant.";
}

// render(): the cards for the current tab (skipped when nothing changed)
function render(force = false) {
    const signature = findings.map(f => `${f.finding_id}:${f.status}:${f.updated_at}`).join(",") + "|" + activeTab;
    if (!force && signature === lastSignature) return;
    lastSignature = signature;

    renderProgress();
    renderTabs();
    const visible = findings.filter(f => f.status === activeTab);
    listEl.innerHTML = visible.length
        ? visible.map(adviceCard).join("")
        : `<p class="py-8 text-center text-sm text-slate-400">${emptyMessage()}</p>`;
}


// ============================================================================
// GROUP 2: LOADING AND UPDATING
// ============================================================================

// loadAdvice(): GET /api/findings/ and redraw
async function loadAdvice() {
    try {
        const res = await fetch("/api/findings/");
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        findings = data.findings || [];
        counts = data.counts || counts;
        render();
    } catch (err) {
        console.error("Advice load error:", err);
        listEl.innerHTML = `<p class="py-8 text-center text-sm text-[#FFB4AB]">Couldn't load the security advice. Check that LanWatch is running.</p>`;
        lastSignature = "";
    }
}

// setStatus(): Trigger "I've done this" / "Not relevant to me" / "Move back to my list"
async function setStatus(findingId, status, button) {
    button.disabled = true;
    try {
        const res = await fetch(`/api/findings/${findingId}/status`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ status: status })
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        await loadAdvice();
    } catch (err) {
        console.error("Advice update error:", err);
        button.disabled = false;
        alert("Couldn't save that change. Please try again.");
    }
}


// ============================================================================
// GROUP 3: START-UP AND USER ACTIONS
// ============================================================================

tabsEl.addEventListener("click", event => {
    const btn = event.target.closest("[data-tab]");
    if (!btn) return;
    activeTab = btn.dataset.tab;
    render(true);
});

listEl.addEventListener("click", event => {
    const btn = event.target.closest("[data-status]");
    if (btn) setStatus(btn.dataset.id, btn.dataset.status, btn);
});

// a full scan has just finished (scan_indicator.js): the advice may have changed
document.addEventListener("lanwatch:scan-finished", loadAdvice);

loadAdvice();
setInterval(() => { if (!document.hidden) loadAdvice(); }, ADVICE_REFRESH_MS);
