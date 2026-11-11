# =============================================================================
# FILE: tools/build_css.py  -  BUILD app/static/css/tailwind.css (Step 27)
# -----------------------------------------------------------------------------
# The pages used to load Tailwind from the internet on every visit (the
# "Play CDN"), which builds the styles inside the browser. An installed app
# must work offline, so this script builds the styles ONCE and saves them as
# an ordinary stylesheet that LanWatch serves itself.
#
# How: it collects every word that could be a style class from the templates
# and scripts, puts them into one test page together with Tailwind's own
# script (pinned to v3.4.17), opens that page in headless Microsoft Edge, and
# saves the CSS Tailwind generates.
#
# Run it after adding or changing classes in any template or script:
#     python tools\build_css.py
# Needs: Microsoft Edge (comes with Windows) or Google Chrome, and an internet connection
# while building. LanWatch itself never needs the internet for its styles.
#
# (The usual way to do this is Tailwind's own "standalone CLI" program;
#  this script gives the same result using only the browser.)
# =============================================================================

import html
import os
import pathlib
import re
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "app" / "static" / "css" / "tailwind.css"
SOURCES = [*(ROOT / "app" / "templates").glob("*.html"), *(ROOT / "app" / "static" / "js").glob("*.js")]
TAILWIND = "https://cdn.tailwindcss.com/3.4.17"
# browsers that can build the styles, tried in this order: if one produces
# nothing (it happened with Edge right after an update), the next is tried
BROWSER_PATHS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]

# the same settings the pages used to give Tailwind
TAILWIND_CONFIG = """
tailwind.config = {
  darkMode: 'class',
  theme: { extend: {
    colors: {
      obsidian: { void: '#090D16', cockpit: '#0F172A', card: 'rgba(15, 23, 42, 0.75)', overlay: 'rgba(30, 41, 59, 0.65)' },
      cyan: { glow: '#00F2FE', deep: '#06B6D4' },
      violet: { plasma: '#8B5CF6', indigo: '#6366F1' },
      emerald: { neon: '#10B981', bright: '#34D399' }
    },
    fontFamily: { sans: ['Inter', 'sans-serif'], mono: ['JetBrains Mono', 'monospace'] }
  } }
}
"""


def candidate_classes():
    """Every word in the templates and scripts that could be a class name.
    Collecting too many is harmless: Tailwind ignores words it doesn't know."""
    words = set()
    for path in SOURCES:
        text = path.read_text(encoding="utf-8")
        # split JavaScript ${...} pieces into words instead of deleting them: classes
        # inside them, e.g. ${done ? "line-through" : ""}, must still be found
        text = text.replace("${", " ").replace("}", " ")
        for word in re.split(r"[\s\"'`<>={}]+", text):
            word = word.strip(",;")
            if 1 < len(word) < 100 and re.match(r"^[!-]?[a-z0-9\[@]", word) and "(" not in word.split("[")[0]:
                words.add(word)
    return sorted(words)


def installed_browsers():
    found = [path for path in BROWSER_PATHS if os.path.exists(path)]
    if not found:
        sys.exit("Neither Microsoft Edge nor Google Chrome was found. One is needed to build the styles.")
    return found


def generated_css(browser, page_file, tmp):
    """Open the page in `browser` (headless); return the CSS Tailwind made, or ""."""
    result = subprocess.run(
        [browser, "--headless=new", "--disable-gpu", "--no-first-run",
         f"--user-data-dir={pathlib.Path(tmp) / ('profile-' + pathlib.Path(browser).stem)}",
         "--virtual-time-budget=15000", "--dump-dom", page_file.as_uri()],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    styles = re.findall(r"<style>(.*?)</style>", result.stdout, re.S)
    css = max(styles, key=len, default="")
    ok = "--tw-" in css and ".flex{" in css.replace(" ", "").replace("\n", "")
    return css if ok else ""


def main():
    classes = candidate_classes()
    page = (f"<!DOCTYPE html><html class='dark'><head><script src='{TAILWIND}'></script>"
            f"<script>{TAILWIND_CONFIG}</script></head><body>"
            f"<div class=\"{html.escape(' '.join(classes))}\"></div></body></html>")

    css = ""
    # (ignore_cleanup_errors: a browser left running in the background can keep
    #  its temporary profile open; Windows clears the temp folder later)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        page_file = pathlib.Path(tmp) / "collect.html"
        page_file.write_text(page, encoding="utf-8")
        for browser in installed_browsers():
            css = generated_css(browser, page_file, tmp)
            if css:
                print(f"Built with {pathlib.Path(browser).name}")
                break
            print(f"{pathlib.Path(browser).name} produced no styles; trying the next browser...")
    if not css:
        sys.exit("Tailwind didn't produce any styles (is the internet connection working?).")

    header = ("/* app/static/css/tailwind.css - generated by tools/build_css.py from Tailwind CSS v3.4.17\n"
              "   (MIT licence). Do not edit by hand: change the classes in the templates or\n"
              "   scripts, then run  python tools\\build_css.py  again. */\n")
    OUTPUT.write_text(header + html.unescape(css).strip() + "\n", encoding="utf-8", newline="\n")
    print(f"{len(classes)} candidate words -> {OUTPUT.relative_to(ROOT)} ({OUTPUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
