// ============================================================================
// FILE: app/static/js/setup.js  -  FIRST-RUN SET-UP PAGE LOGIC
// ----------------------------------------------------------------------------
// Page: templates/setup.html
//
// Flow: user fills in the form -> createAccount() checks it
//       -> POST /api/account/ (routes/settings.py create_first_account)
//       -> 201: the account exists, this browser is unlocked -> go to the Dashboard
//       -> 409: an account already exists (created elsewhere) -> go to the Dashboard
// The same rules are checked again on the server.
// ============================================================================

(function () {
    const $ = (id) => document.getElementById(id);
    const USERNAME_RE = /^[A-Za-z0-9_.-]{3,32}$/;

    // check(): the same rules as the server; returns an error message or ""
    function check(username, password, confirm) {
        if (!USERNAME_RE.test(username)) return "Username must be 3 to 32 characters: letters, numbers, dots, dashes or underscores.";
        if (password.length < 8 || password.length > 128) return "Password must be 8 to 128 characters.";
        if (password !== confirm) return "The two passwords don't match.";
        return "";
    }

    // createAccount(): Trigger "Create account" -> POST /api/account/
    async function createAccount(event) {
        event.preventDefault();
        const username = $("setupUsername").value.trim();
        const password = $("setupPassword").value;
        const problem = check(username, password, $("setupConfirm").value);
        if (problem) { $("setupMsg").textContent = problem; return; }

        $("setupSubmit").disabled = true;
        $("setupMsg").textContent = "";
        try {
            const res = await fetch("/api/account/", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ username: username, new_password: password })
            });
            const data = await res.json().catch(() => ({}));
            if (res.ok || res.status === 409) { location.href = "/"; return; }
            $("setupMsg").textContent = data.error || "Something went wrong. Please try again.";
        } catch (err) {
            $("setupMsg").textContent = "Couldn't reach LanWatch. Check that it is running.";
        }
        $("setupSubmit").disabled = false;
    }

    $("setupForm").addEventListener("submit", createAccount);
    $("setupUsername").focus();
})();
