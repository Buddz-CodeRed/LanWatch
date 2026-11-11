// ============================================================================
// FILE: app/static/js/nav.js  -  PHONE MENU (every page, via base.html)
// ----------------------------------------------------------------------------
// Below 768 px wide the sidebar is hidden off-screen. This script slides it in
// when ☰ is pressed and out again when:
//   - the ✕ button, the dark backdrop or a menu link is pressed
//   - the Escape key is pressed
//   - the window is widened past 768 px (the sidebar is then always shown)
//
// Flow: user taps ☰ (#navOpen) -> openMenu() -> sidebar slides in
//       user taps ✕ / backdrop / a link / Escape -> closeMenu()
// ============================================================================

(function () {
    const sidebar = document.getElementById("sidebar");
    const backdrop = document.getElementById("navBackdrop");
    const openBtn = document.getElementById("navOpen");
    const closeBtn = document.getElementById("navClose");
    if (!sidebar || !openBtn) return;

    const desktop = window.matchMedia("(min-width: 768px)");

    // openMenu(): slide the sidebar in, show the backdrop, stop the page scrolling behind it
    function openMenu() {
        sidebar.classList.remove("-translate-x-full");
        backdrop.classList.remove("hidden");
        document.body.style.overflow = "hidden";
        openBtn.setAttribute("aria-expanded", "true");
        (sidebar.querySelector("[aria-current]") || sidebar.querySelector("a")).focus();
    }

    // closeMenu(): slide it back out (on phones; on wide screens it always shows)
    function closeMenu() {
        if (!desktop.matches) sidebar.classList.add("-translate-x-full");
        backdrop.classList.add("hidden");
        document.body.style.overflow = "";
        openBtn.setAttribute("aria-expanded", "false");
    }

    openBtn.addEventListener("click", openMenu);
    if (closeBtn) closeBtn.addEventListener("click", closeMenu);
    backdrop.addEventListener("click", closeMenu);
    sidebar.addEventListener("click", function (event) {
        if (event.target.closest("a")) closeMenu();
    });
    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && openBtn.getAttribute("aria-expanded") === "true") {
            closeMenu();
            openBtn.focus();
        }
    });
    // widening the window past 768 px: reset to the normal desktop layout
    desktop.addEventListener("change", function () {
        backdrop.classList.add("hidden");
        document.body.style.overflow = "";
        openBtn.setAttribute("aria-expanded", "false");
        if (!desktop.matches) sidebar.classList.add("-translate-x-full");
    });
})();
