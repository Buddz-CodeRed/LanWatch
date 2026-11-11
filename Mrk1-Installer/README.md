# Mrk1-Installer — the LanWatch Windows installer

Everything needed to turn the LanWatch project into **one file** a user downloads and runs:
`Output\LanWatch-Setup-0.01.exe`.

## What's in this folder

| File / folder | What it is |
| --- | --- |
| `build_installer.ps1` | **Builds everything** in one go (run it from the project folder). |
| `LanWatch.spec` | PyInstaller recipe: turns the Python project into `dist\LanWatch\LanWatch.exe` (no Python needed on the user's PC). |
| `LanWatch.iss` | Inno Setup script: the setup wizard. |
| `scripts\register_task.ps1` | Run by the wizard: creates the "LanWatch" sign-in task. |
| `assets\lanwatch.ico` | The program and installer icon. |
| `LICENSE.txt` | Shown in the wizard: LanWatch's terms and the third-party licences. |
| `dist\`, `build\` | Made by PyInstaller (can be deleted; rebuilt each time). |
| `Output\` | **The finished installer.** |

## Building the installer

Needs: the project's `mrk1_env` with `requirements.txt` and `pyinstaller` installed, the AI model in `models\`, and Inno Setup 6.

```powershell
# once: the build tools
.\mrk1_env\Scripts\python.exe -m pip install pyinstaller
winget install --id JRSoftware.InnoSetup -e --scope user

# every build (from the project folder)
.\Mrk1-Installer\build_installer.ps1              # add -RebuildCss if page styles changed
```

It takes a few minutes. The result is `Output\LanWatch-Setup-0.01.exe` (about 1.2 GB, because the AI model is included).

## What the user experiences

1. Double-click `LanWatch-Setup-0.01.exe`. Windows asks for Administrator permission once.
2. Accept the licence and choose the install folder (default `C:\Program Files\LanWatch`).
3. Options: **Start LanWatch automatically when I sign in** (ticked) and a desktop shortcut.
4. **Create the admin account** (username + password, typed twice). It protects the System Settings and marking alerts as resolved.
5. **Install.** If Nmap isn't installed, the wizard downloads the official installer from nmap.org (checked against its published SHA-256) and opens it. The user clicks through it, keeping **Npcap** ticked.
6. **Finish.** LanWatch is already running; "Open LanWatch now" opens the dashboard in the browser.

After that, LanWatch starts by itself at every sign-in (with the rights it needs, without a permission prompt). The **LanWatch** shortcut (Start menu or desktop) opens it in the browser at <http://127.0.0.1:5000>, starting it first if needed. Only this computer can open it.

| Where | What |
| --- | --- |
| `C:\Program Files\LanWatch\` | the program |
| `C:\ProgramData\LanWatch\` | the user's data: `lanwatch.db`, `logs\lanwatch.log` |
| Task Scheduler → `LanWatch` | starts LanWatch at sign-in |

**Forgot the admin password?** Run the installer again: on the admin account page, enter a new username and password (leave them empty to keep the old ones). The data is kept.

**Uninstalling** (Settings → Apps → LanWatch) stops LanWatch, removes the task and the program, and asks whether to also delete the data (default: keep it).

## Before giving it to anyone: test on a clean PC

A Windows virtual machine (free from Microsoft, in Hyper-V or VirtualBox) that has never had Python, Nmap or LanWatch:

- [ ] Setup runs; the admin account page refuses a short password and mismatched passwords.
- [ ] Nmap is downloaded and its installer opens; with Npcap ticked, it finishes.
- [ ] The Finish page opens the dashboard; there is no orange "missing something" bar.
- [ ] System Settings asks for the admin password; the one chosen in the wizard works.
- [ ] Sign out and in again: LanWatch starts by itself (no permission prompt).
- [ ] With Wi-Fi off, the pages still look right and "Explain this" still works.
- [ ] A full scan finds devices; Security Advice appears.
- [ ] Uninstall: "No" keeps `C:\ProgramData\LanWatch`; reinstalling keeps the data.

## Known limits

- **"Windows protected your PC" warning:** the installer isn't digitally signed. Users click *More info → Run anyway*. Removing it needs a code-signing certificate (paid, yearly).
- **Nmap's installer shows its own screens:** the free Npcap licence doesn't allow a silent install. A paid Nmap OEM licence would allow it.
- **Licences:** LanWatch includes two GPL-licensed libraries (scapy: GPL-2.0, python-nmap: GPL-3.0). Sharing the installer privately (e.g. for marking) is normally fine; before publishing it publicly, check what the GPL requires (usually: make LanWatch's source code available under GPL-compatible terms).
- **Windows 10/11, 64-bit only.**
