# =============================================================================
# FILE: Mrk1-Installer/build_installer.ps1  -  BUILD LanWatch-Setup-x.y.z.exe
# -----------------------------------------------------------------------------
# One command that makes the Windows installer from the project:
#
#   1. (optional, -RebuildCss) rebuild app\static\css\tailwind.css
#   2. check the AI model and the pinned packages are there
#   3. PyInstaller: LanWatch.spec -> dist\LanWatch\ (LanWatch.exe + _internal)
#   4. Inno Setup:  LanWatch.iss  -> Output\LanWatch-Setup-0.01.exe
#
# Run from the project folder, in PowerShell:
#   .\Mrk1-Installer\build_installer.ps1
#
# Needs: the project's mrk1_env (with requirements.txt + pyinstaller installed)
#        and Inno Setup 6 (winget install --id JRSoftware.InnoSetup -e --scope user)
# =============================================================================
param([switch]$RebuildCss)
$ErrorActionPreference = "Stop"

$Here = $PSScriptRoot                                  # ...\Mrk1-Installer
$Root = Split-Path $Here -Parent                       # the project folder
$Python = Join-Path $Root "mrk1_env\Scripts\python.exe"
Set-Location $Root

function Step($text) { Write-Host "`n=== $text" -ForegroundColor Cyan }

# Invoke-Tool: run a program and fail only if its EXIT CODE says so. (PyInstaller
# writes normal progress messages to stderr, which Windows PowerShell 5.1 would
# otherwise treat as an error and stop the build.)
function Invoke-Tool([string]$Program, [string[]]$Arguments, [string]$What) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $Program @Arguments 2>&1 | ForEach-Object { "$_" }
    $code = $LASTEXITCODE
    $ErrorActionPreference = $previous
    if ($code) { throw "$What failed (exit code $code)" }
}

Step "1. Styles"
if ($RebuildCss) { Invoke-Tool $Python @("tools\build_css.py") "build_css.py" }
else { Write-Host "Using the existing app\static\css\tailwind.css (add -RebuildCss to rebuild it)." }

Step "2. Checks"
if (-not (Test-Path $Python)) { throw "mrk1_env not found: create it first (BUILD_GUIDE Step 2)." }
$model = Join-Path $Root "models\qwen2.5-1.5b-instruct-q4_k_m.gguf"
if (-not (Test-Path $model)) { throw "The AI model is missing: $model (BUILD_GUIDE Step 37)." }
Invoke-Tool $Python @("-c", "import PyInstaller, waitress, flask, scapy, nmap, llama_cpp") "Package check (pip install -r requirements.txt pyinstaller)"
Write-Host "Python environment, packages and AI model: OK"

Step "3. PyInstaller (LanWatch.exe)"
Invoke-Tool $Python @("-m", "PyInstaller", "--noconfirm", "--clean",
    "--distpath", (Join-Path $Here "dist"), "--workpath", (Join-Path $Here "build"),
    (Join-Path $Here "LanWatch.spec")) "PyInstaller"

Step "4. Inno Setup (the setup wizard)"
$iscc = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 not found: winget install --id JRSoftware.InnoSetup -e --scope user" }
Invoke-Tool $iscc @((Join-Path $Here "LanWatch.iss")) "Inno Setup"

$setup = Get-ChildItem (Join-Path $Here "Output\LanWatch-Setup-*.exe") | Sort-Object LastWriteTime | Select-Object -Last 1
$hash = (Get-FileHash $setup.FullName -Algorithm SHA256).Hash
Step "Done"
Write-Host ("{0}  ({1:N0} MB)" -f $setup.FullName, ($setup.Length / 1MB))
Write-Host "SHA-256: $hash"
