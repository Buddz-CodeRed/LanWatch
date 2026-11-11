// ============================================================================
// FILE: app/static/js/phone_login.js  -  SIGN-IN PAGE ON A PHONE OR TABLET
// ----------------------------------------------------------------------------
// Page: templates/phone_login.html
// Flow: "Sign in" -> signIn() -> POST /api/phone/login (routes/phone.py)
//       -> signed in: go to the page the phone first asked for (data-next)
//       -> wrong details: message; too many tries: button paused
// ============================================================================

(function () {
    const form = document.getElementById("phoneLoginForm");
    const msg = document.getElementById("phoneLoginMsg");
    const button = document.getElementById("phoneLoginSubmit");
    const username = document.getElementById("phoneUsername");
    const password = document.getElementById("phonePassword");

    async function signIn(event) {
        event.preventDefault();
        button.disabled = true;
        msg.textContent = "";
        try {
            const res = await fetch("/api/phone/login", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ username: username.value, password: password.value })
            });
            const data = await res.json().catch(() => ({}));
            if (res.ok) {
                const next = form.dataset.next || "/";
                window.location.replace(next.startsWith("/") && !next.startsWith("//") ? next : "/");
                return;
            }
            msg.textContent = data.error || `Couldn't sign in (HTTP ${res.status}).`;
            password.value = "";
            if (data.wait_seconds) {
                setTimeout(() => { button.disabled = false; }, data.wait_seconds * 1000);
                return;
            }
        } catch (err) {
            msg.textContent = "Couldn't reach LanWatch. Is this phone on your home Wi-Fi, and is LanWatch running on its computer?";
        }
        button.disabled = false;
        password.focus();
    }

    form.addEventListener("submit", signIn);
    username.focus();
})();
