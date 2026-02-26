# Windows Fresh Dev Environment Setup

Everything you need to go from a clean Windows machine to running tests.
Copy-paste each block in order. All commands are PowerShell.

---

## Quick run (PowerShell commands that work)

If you just want to get the demo running, use this sequence. It uses the project’s **setup.ps1** (which installs Temporal CLI and Python packages) and sets **PYTHONPATH** so `python demo_runner.py` and `python -m temporal.worker` find the `temporal` package.

**Terminal 1 — Setup (one-time) then Temporal server**

If setup needs to install Temporal CLI or change PATH, run PowerShell as Administrator first:

```powershell
powershell -Command "Start-Process powershell -Verb RunAs"
```

In the **new (elevated) window** that opens:

```powershell
cd C:\dev\temporal-security-scanner
.\setup.ps1
```

After setup completes, start the Temporal dev server (in that same window or a new one):

```powershell
temporal server start-dev
```

**Terminal 2 — Worker**

```powershell
cd C:\dev\temporal-security-scanner
$env:PYTHONPATH = "C:\dev\temporal-security-scanner"
python -m temporal.worker
```

**Terminal 3 — Interactive demo**

```powershell
cd C:\dev\temporal-security-scanner
$env:PYTHONPATH = "C:\dev\temporal-security-scanner"
python demo_runner.py
```

**Why PYTHONPATH?** So that `import temporal` (and the `temporal` package) resolve to the project directory. If you use `pip install -e ".[dev]"` from the project root and run commands from there, you can sometimes skip it — but setting PYTHONPATH is the most reliable on Windows.

**Why RunAs for setup?** Only if setup.ps1 needs to write to Program Files or update system PATH; otherwise run `.\setup.ps1` in a normal PowerShell.

---

## Phase 1: Install Tools (one-time, ~10 minutes)

### 1A. Install Python 3.12

Open a browser and go to:
```
https://www.python.org/downloads/
```

Download the latest 3.12.x installer (NOT 3.13 — some packages lag behind).

**CRITICAL during install:**
- ✅ Check "Add python.exe to PATH"
- ✅ Check "Use admin privileges when installing py"
- Click "Install Now" (default location is fine)

After install, **close and reopen PowerShell**, then verify:

```powershell
python --version
# Should show: Python 3.12.x

pip --version
# Should show: pip 24.x from ...
```

If `python` isn't found, the PATH wasn't set. Fix it:
```powershell
# Find where Python installed
Get-Command python.exe -ErrorAction SilentlyContinue

# If nothing, add it manually (adjust version number if different):
$env:Path += ";$env:LOCALAPPDATA\Programs\Python\Python312;$env:LOCALAPPDATA\Programs\Python\Python312\Scripts"
```

### 1B. Install Temporal CLI

```powershell
# Option A: PowerShell one-liner (recommended)
irm https://temporal.download/cli.sh | iex
```

Close and reopen PowerShell, then verify:
```powershell
temporal version
```

If `irm` fails (corporate firewall, etc.), do it manually:
```powershell
# Option B: Manual download
# 1. Go to: https://github.com/temporalio/cli/releases/latest
# 2. Download: temporal_cli_<version>_windows_amd64.zip
# 3. Extract to a folder, e.g. C:\temporal
# 4. Add to PATH:
$env:Path += ";C:\temporal"
# To make permanent:
[Environment]::SetEnvironmentVariable("Path", $env:Path + ";C:\temporal", "User")
```

### 1C. Enable UTF-8 (fixes emoji in terminal output)

```powershell
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new()
```

To make this permanent, add that line to your PowerShell profile:
```powershell
# This creates/opens your profile file
if (!(Test-Path -Path $PROFILE)) { New-Item -ItemType File -Path $PROFILE -Force }
Add-Content -Path $PROFILE -Value '$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new()'
```

### 1D. (Optional) Install Windows Terminal

If you're using the old "Windows PowerShell" window (blue background), get
Windows Terminal from the Microsoft Store — it handles colors, emoji, and
`\r` carriage returns much better. Search "Windows Terminal" in the Store.

---

## Phase 2: Set Up the Project (~5 minutes)

### 2A. Create a working directory

```powershell
mkdir C:\dev
cd C:\dev
```

### 2B. Get the project files

If you downloaded the `temporal-security-scanner` folder from Claude's outputs,
copy it into `C:\dev\` so you have `C:\dev\temporal-security-scanner\`.

Verify the structure:
```powershell
cd C:\dev\temporal-security-scanner
dir
```

You should see:
```
PLAYBOOK.md
PRESENTATION.md
README.md
before\
go_comparison\
pyproject.toml
temporal\
tests\
```

### 2C. Create a virtual environment (recommended)

```powershell
cd C:\dev\temporal-security-scanner

# Create the virtual environment
python -m venv .venv

# Activate it
.\.venv\Scripts\Activate.ps1
```

If you get a "running scripts is disabled" error:
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
# Then try activating again:
.\.venv\Scripts\Activate.ps1
```

Your prompt should now show `(.venv)` at the beginning.

**IMPORTANT:** Every time you open a new PowerShell window for this project,
you need to activate the venv:
```powershell
cd C:\dev\temporal-security-scanner
.\.venv\Scripts\Activate.ps1
```

### 2D. Install Python dependencies

```powershell
pip install -e ".[dev]"
```

This installs:
- `temporalio` — Temporal Python SDK
- `requests` — HTTP client for GitHub API
- `cryptography` — Fernet encryption for PayloadCodec
- `pytest` + `pytest-asyncio` — Testing framework

Verify:
```powershell
python -c "import temporalio; print('temporalio OK')"
python -c "import requests; print('requests OK')"
python -c "from cryptography.fernet import Fernet; print('cryptography OK')"
python -c "import pytest; print('pytest OK')"
```

All four should print "OK".

---

## Phase 3: Run Unit Tests (~2 minutes)

This validates the code WITHOUT any server. Run from the project root:

```powershell
cd C:\dev\temporal-security-scanner
pytest tests/ -v
```

**First run will be slow (10-30 seconds)** — it downloads an embedded Temporal
test server binary and caches it. Subsequent runs are fast.

### Expected output:

```
tests/test_workflow.py::test_full_scan_workflow PASSED
tests/test_workflow.py::test_progress_query PASSED
tests/test_workflow.py::test_cancel_scan_signal PASSED
tests/test_workflow.py::test_encryption_codec_roundtrip PASSED
tests/test_workflow.py::test_encryption_wrong_key_fails PASSED
tests/test_workflow.py::test_workflow_with_encryption PASSED
tests/test_workflow.py::test_repo_security_result_no_token_leak PASSED
tests/test_workflow.py::test_compliance_calculation PASSED

8 passed
```

### If tests fail:

| Error | Fix |
|-------|-----|
| `ModuleNotFoundError: No module named 'temporal'` | `pip install -e ".[dev]"` from project root |
| `SyntaxError` on `str \| None` | Your Python is < 3.11. Install 3.12. |
| `DeprecationWarning: asyncio_mode` | Ignore it — tests still pass |
| Hangs for > 60 seconds | Test server binary download. Wait. Check internet. |
| `event loop is closed` | `pip install "pytest-asyncio>=0.23,<0.25"` |

---

## Phase 4: Live Demo Tests (~10 minutes)

You need THREE PowerShell windows. Open them all now and activate the venv in each:

```powershell
cd C:\dev\temporal-security-scanner
.\.venv\Scripts\Activate.ps1
```

### 4A. Window 1 — Temporal Dev Server

```powershell
temporal server start-dev
```

You'll see a lot of output. Leave it running.

Open your browser to `http://localhost:8233` — you should see the Temporal Web UI.

### 4B. Window 2 — Worker

```powershell
cd C:\dev\temporal-security-scanner
.\.venv\Scripts\Activate.ps1
python -m temporal.worker
```

You should see:
```
... Payload encryption: ENABLED
... Worker started on task queue 'security-scanner' (host: localhost:7233)
```

Leave it running.

### 4C. Window 3 — Run Commands

This is where you'll run all the test commands.

```powershell
cd C:\dev\temporal-security-scanner
.\.venv\Scripts\Activate.ps1
```

---

### TEST 1: Basic scan (happy path)

**Set a GitHub token first** so the scan doesn’t hit rate limits (the demo uses the larger `temporalio` org). Create one at [GitHub → Settings → Developer settings → Personal access tokens](https://github.com/settings/tokens) with `repo` and `read:org`, then:

```powershell
$env:GITHUB_TOKEN = "ghp_your_token_here"
python -m temporal.starter --org temporalio
```

You should see progress updates, then a compliance report. Takes about 1–2 minutes for temporalio.

After it finishes:
- Check the Web UI (`http://localhost:8233`) — you should see the workflow as "Completed"
- Click on it, then click any event — payloads should show `[binary/encrypted]`
- Check the JSON output: `Get-Content security_scan_temporalio.json`

### TEST 2: The Kill Test

This is the showstopper demo. Use the same token; the scan is already targeting `temporalio`.

```powershell
# Window 3: Start a scan without waiting
python -m temporal.starter --org temporalio --no-wait
```

Output:
```
Starting security scan for 'temporalio'...
  Workflow ID: security-scan-temporalio
  Encryption:  ENABLED
Workflow started. Useful commands: ...
```

```powershell
# Window 3: Verify it's scanning
python -m temporal.starter --org temporalio --query
```

You should see status "scanning" with some repos scanned.

**NOW KILL THE WORKER:**

Go to **Window 2** (the worker) and just CLOSE THE WINDOW. That's a hard kill.

```powershell
# Window 3: Check — the workflow is still alive!
python -m temporal.starter --org temporalio --query
```

Status should still show "scanning" (tasks are timing out, waiting for a worker).

Check the Web UI — the workflow shows "Running".

**RESTART THE WORKER:**

Open a new PowerShell window (this is your new Window 2):

```powershell
cd C:\dev\temporal-security-scanner
.\.venv\Scripts\Activate.ps1
python -m temporal.worker
```

```powershell
# Window 3: Query again — it's resuming!
python -m temporal.starter --org temporalio --query
```

The scan continues from where it left off. No repos re-scanned.

Wait for it to complete, or:

```powershell
# Wait for the full result
python -m temporal.starter --org temporalio
```

### TEST 3: Cancel Signal

```powershell
# Start a new scan
python -m temporal.starter --org temporalio --no-wait

# Wait 5-10 seconds for some repos to be scanned, then:
python -m temporal.starter --org temporalio --cancel "Testing graceful stop"

# Check the result:
python -m temporal.starter --org temporalio --query
```

Status should show "cancelled" with partial results.

### TEST 4: Error Handling

```powershell
# Invalid org — should fail fast (non-retryable ValueError)
python -m temporal.starter --org this-org-definitely-does-not-exist-xyz

# Invalid token — should fail fast
python -m temporal.starter --org temporalio --token ghp_invalid_token_000
```

Both should fail within a few seconds (not retry forever).

---

## Phase 5: Verify Everything for Presentation

### Checklist

Run through this before recording:

- [ ] `pytest tests/ -v` — all 8 pass
- [ ] Basic scan completes and prints a report
- [ ] Web UI shows workflow as "Completed"
- [ ] Event history shows `[binary/encrypted]` payloads (not plaintext)
- [ ] Kill test: kill worker → workflow stays Running → restart → resumes
- [ ] Cancel signal: scan stops, partial report generated
- [ ] Invalid org: fails fast, doesn't retry
- [ ] `--query` flag shows progress correctly
- [ ] JSON report file is created (`security_scan_<org>.json`)

### Cleanup Between Test Runs

If you need to reset:

```powershell
# Terminate any running workflows
temporal workflow terminate -w security-scan-temporalio
temporal workflow terminate -w security-scan-temporalio

# Or just restart the Temporal dev server (clears all history)
# In Window 1: Ctrl+C, then:
temporal server start-dev
```

---

## Troubleshooting

### "temporal: The term 'temporal' is not recognized"

PATH wasn't updated. Close ALL PowerShell windows and reopen. Or:
```powershell
$env:Path += ";$env:LOCALAPPDATA\Programs\temporal-cli"
```

### "python: The term 'python' is not recognized"

Python not in PATH. Close and reopen PowerShell. Or:
```powershell
$env:Path += ";$env:LOCALAPPDATA\Programs\Python\Python312;$env:LOCALAPPDATA\Programs\Python\Python312\Scripts"
```

### pip install fails with "Microsoft Visual C++ 14.0 required"

The `cryptography` package needs C build tools. Install them:
```powershell
# Option A: Install pre-built wheel (usually works without C tools)
pip install --only-binary=:all: cryptography

# Option B: Install Visual C++ Build Tools
# Download from: https://visualstudio.microsoft.com/visual-cpp-build-tools/
# During install, select "Desktop development with C++"
```

### "Connection refused" on worker or starter

The Temporal dev server (Window 1) isn't running. Start it:
```powershell
temporal server start-dev
```

### Worker shows SSL/certificate errors

Some corporate networks intercept HTTPS. The Temporal dev server uses plain
gRPC (no TLS) on localhost, so this shouldn't happen. But if it does:
```powershell
$env:PYTHONHTTPSVERIFY = "0"  # Not recommended for production
```

### Web UI doesn't load at localhost:8233

Check that the dev server output mentions port 8233. If your port is different:
```powershell
temporal server start-dev --ui-port 8233
```

### "Execution policy" error when activating venv

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### Everything works but the scan returns weird results

GitHub API responses vary based on token permissions. If Dependabot shows
"disabled" for everything, the token may lack the `repo` scope. Generate a
new token with `repo` and `read:org` scopes.
