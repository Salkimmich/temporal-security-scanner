<#
.SYNOPSIS
    One-step setup for the Temporal Security Scanner demo.
.DESCRIPTION
    Installs all dependencies (Temporal CLI, Python packages),
    configures PATH, and validates the environment is ready.
    Run from the project root:  .\setup.ps1
.NOTES
    Author: Sal Kimmich
    Requires: Python 3.11+ already installed
#>

$ErrorActionPreference = "Stop"

function Write-Step  { param($msg) Write-Host "`n>> $msg" -ForegroundColor Cyan }
function Write-Ok    { param($msg) Write-Host "   OK  $msg" -ForegroundColor Green }
function Write-Fail  { param($msg) Write-Host "   FAIL  $msg" -ForegroundColor Red }
function Write-Info  { param($msg) Write-Host "   ..  $msg" -ForegroundColor DarkGray }
function Write-Warn  { param($msg) Write-Host "   !!  $msg" -ForegroundColor Yellow }

Write-Host ""
Write-Host " ================================================================" -ForegroundColor White
Write-Host "   TEMPORAL SECURITY SCANNER -- ENVIRONMENT SETUP" -ForegroundColor White
Write-Host " ================================================================" -ForegroundColor White
Write-Host ""

$projectDir = $PSScriptRoot
if (-not $projectDir) { $projectDir = Get-Location }
Set-Location $projectDir

$allGood = $true

# ==================================================================
#  1. PYTHON
# ==================================================================
Write-Step "Checking Python..."

$py = $null
foreach ($cmd in @("python", "python3", "py")) {
    try {
        $ver = & $cmd --version 2>&1
        if ($ver -match "Python (\d+)\.(\d+)") {
            $major = [int]$Matches[1]
            $minor = [int]$Matches[2]
            if ($major -ge 3 -and $minor -ge 11) {
                $py = $cmd
                Write-Ok "$ver"
                break
            }
            else {
                Write-Info "$ver found but need 3.11+"
            }
        }
    }
    catch { }
}

if (-not $py) {
    Write-Fail "Python 3.11+ not found."
    Write-Host "   Install from: https://www.python.org/downloads/" -ForegroundColor Yellow
    Write-Host "   IMPORTANT: Check 'Add Python to PATH' during install." -ForegroundColor Yellow
    $allGood = $false
}

# ==================================================================
#  2. TEMPORAL CLI
# ==================================================================
Write-Step "Checking Temporal CLI..."

$temporalDir = "$env:LOCALAPPDATA\Programs\temporal"
$temporalExe = "$temporalDir\temporal.exe"

$existingTemporal = Get-Command temporal -ErrorAction SilentlyContinue

if ($existingTemporal) {
    $tver = & temporal --version 2>&1
    Write-Ok "Already installed: $tver"
}
elseif (Test-Path $temporalExe) {
    Write-Info "Found at $temporalExe but not on PATH. Fixing..."
    $currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($currentPath -notlike "*$temporalDir*") {
        [Environment]::SetEnvironmentVariable("Path", "$currentPath;$temporalDir", "User")
        $env:PATH += ";$temporalDir"
        Write-Ok "Added to PATH (permanent)."
    }
    $tver = & $temporalExe --version 2>&1
    Write-Ok "$tver"
}
else {
    Write-Info "Not found. Downloading Temporal CLI..."
    New-Item -ItemType Directory -Force -Path $temporalDir | Out-Null

    $zipPath = "$env:TEMP\temporal-cli.zip"
    $extractPath = "$env:TEMP\temporal-cli-extract"

    try {
        $url = "https://temporal.download/cli/archive/latest?platform=windows&arch=amd64"
        Write-Info "Downloading from temporal.download..."
        Invoke-WebRequest -Uri $url -OutFile $zipPath -UseBasicParsing

        if (Test-Path $extractPath) {
            Remove-Item -Recurse -Force $extractPath
        }
        Expand-Archive -Path $zipPath -DestinationPath $extractPath -Force

        $found = Get-ChildItem -Path $extractPath -Recurse -Filter "temporal.exe" | Select-Object -First 1
        if ($found) {
            Copy-Item $found.FullName -Destination $temporalExe -Force
            Write-Ok "Installed to $temporalExe"
        }
        else {
            $allFiles = Get-ChildItem -Path $extractPath -Recurse
            Write-Fail "Could not find temporal.exe in download."
            Write-Info "Zip contents: $($allFiles.Name -join ', ')"
            $allGood = $false
        }

        Remove-Item $zipPath -Force -ErrorAction SilentlyContinue
        Remove-Item $extractPath -Recurse -Force -ErrorAction SilentlyContinue

        if (Test-Path $temporalExe) {
            $currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
            if ($currentPath -notlike "*$temporalDir*") {
                [Environment]::SetEnvironmentVariable("Path", "$currentPath;$temporalDir", "User")
                $env:PATH += ";$temporalDir"
                Write-Ok "Added to PATH (permanent, survives restarts)."
            }
            $tver = & $temporalExe --version 2>&1
            Write-Ok "$tver"
        }
    }
    catch {
        Write-Fail "Download failed: $_"
        Write-Warn "Manual install: https://docs.temporal.io/cli#install"
        $allGood = $false
    }
}

# ==================================================================
#  3. PYTHON PACKAGES
# ==================================================================
Write-Step "Installing Python packages..."

if ($py) {
    $packages = @(
        @{ name = "temporalio";   import_name = "temporalio" },
        @{ name = "cryptography"; import_name = "cryptography" },
        @{ name = "requests";     import_name = "requests" }
    )

    foreach ($pkg in $packages) {
        $check = & $py -c "import $($pkg.import_name); print($($pkg.import_name).__version__)" 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Ok "$($pkg.name) $check (already installed)"
        }
        else {
            Write-Info "Installing $($pkg.name)..."
            & $py -m pip install $pkg.name --quiet 2>&1 | Out-Null
            $check2 = & $py -c "import $($pkg.import_name); print($($pkg.import_name).__version__)" 2>&1
            if ($LASTEXITCODE -eq 0) {
                Write-Ok "$($pkg.name) $check2"
            }
            else {
                Write-Fail "$($pkg.name) install failed"
                $allGood = $false
            }
        }
    }
}

# ==================================================================
#  4. PROJECT FILES
# ==================================================================
Write-Step "Checking project files..."

$required = @(
    "temporal/workflows.py",
    "temporal/activities.py",
    "temporal/encryption.py",
    "temporal/models.py",
    "temporal/worker.py",
    "temporal/starter.py",
    "demo_runner.py"
)

foreach ($f in $required) {
    $full = Join-Path $projectDir $f
    if (Test-Path $full) {
        Write-Ok $f
    }
    else {
        Write-Fail "$f -- MISSING"
        $allGood = $false
    }
}

# ==================================================================
#  5. PYTHONPATH
# ==================================================================
Write-Step "Setting PYTHONPATH..."

$env:PYTHONPATH = $projectDir
Write-Ok "PYTHONPATH = $projectDir (this session)"

# ==================================================================
#  SUMMARY
# ==================================================================

Write-Host ""
Write-Host " ================================================================" -ForegroundColor White

if ($allGood) {
    Write-Host "   SETUP COMPLETE -- Environment is ready." -ForegroundColor Green
    Write-Host " ================================================================" -ForegroundColor White
    Write-Host ""
    Write-Host "  Next steps (three separate terminals):" -ForegroundColor White
    Write-Host ""
    Write-Host "    Terminal 1 -- Server:" -ForegroundColor Cyan
    Write-Host "      temporal server start-dev" -ForegroundColor White
    Write-Host ""
    Write-Host "    Terminal 2 -- Worker:" -ForegroundColor Cyan
    Write-Host "      cd $projectDir" -ForegroundColor White
    Write-Host '      $env:PYTHONPATH = "' -NoNewline -ForegroundColor White
    Write-Host "$projectDir" -NoNewline -ForegroundColor White
    Write-Host '"' -ForegroundColor White
    Write-Host "      python -m temporal.worker" -ForegroundColor White
    Write-Host ""
    Write-Host "    Terminal 3 -- Demo:" -ForegroundColor Cyan
    Write-Host "      cd $projectDir" -ForegroundColor White
    Write-Host '      $env:PYTHONPATH = "' -NoNewline -ForegroundColor White
    Write-Host "$projectDir" -NoNewline -ForegroundColor White
    Write-Host '"' -ForegroundColor White
    Write-Host "      python demo_runner.py" -ForegroundColor White
    Write-Host ""
}
else {
    Write-Host "   SETUP INCOMPLETE -- Fix the FAIL items above." -ForegroundColor Red
    Write-Host " ================================================================" -ForegroundColor White
    Write-Host ""
}
