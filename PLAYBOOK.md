# Demo Playbook: Security Scanner → Temporal

This is your step-by-step guide to running, testing, and presenting this demo.
Everything here has been thought through for executability — where something
might break, it's called out explicitly.

**Two ways to run the demo:**

1. **CLI flow** (below) — Use `temporal.starter` and the Temporal Web UI. Best for a short, scripted demo (kill test, query, cancel).
2. **Interactive narrated demo** — Run `python demo_runner.py` in a third terminal. Walks through concepts, live scan, encryption proof, kill test, and signals with on-screen narrative. See [Interactive demo (demo_runner.py)](#interactive-demo-demo_runnerpy) below.

**On Windows:** Use [WINDOWS_SETUP.md](WINDOWS_SETUP.md) for install and run steps; the kill test there uses "close the worker window" instead of `kill -9`.

---

## Prerequisites Checklist

Before anything else, verify these are installed:

```bash
# Python 3.11+ required (for StrEnum, `str | None` union syntax)
python3 --version   # must be 3.11.x or higher

# Temporal CLI — this starts the local dev server
temporal version    # if missing, see installation below

# pip — for Python dependencies
pip --version
```

### Installing the Temporal CLI

```bash
# macOS (Homebrew)
brew install temporal

# Linux / macOS (curl)
curl -sSf https://temporal.download/cli.sh | sh
# Then add to PATH: export PATH="$HOME/.temporalio/bin:$PATH"

# Verify
temporal version
```

**⚠️ KNOWN ISSUE:** The Temporal CLI installs a self-contained binary. It does NOT
require Go, Docker, or any other runtime. But some Linux distros may need
`ca-certificates` updated for the download to work.

---

## Quick Start (5 minutes)

Open **three terminal windows**. All commands assume you're in the project root.

### Terminal 1: Temporal Dev Server

```bash
temporal server start-dev
```

This starts:
- gRPC endpoint on `localhost:7233` (workers and clients connect here)
- Web UI on `http://localhost:8233` (open this in your browser now)

**⚠️ NOTE:** The dev server stores everything in memory. If you restart it,
all workflow history is gone. This is fine for the demo — just be aware.

The dev server will print a lot of output. Leave it running.

### Terminal 2: Worker

```bash
cd temporal-security-scanner
pip install -e ".[dev]"        # first time only
python -m temporal.worker
```

You should see:
```
... [INFO] security-scanner.worker: Payload encryption: ENABLED
... [INFO] security-scanner.worker: Worker started on task queue 'security-scanner' (host: localhost:7233)
```

**⚠️ KNOWN ISSUE:** If you see `ModuleNotFoundError: No module named 'temporal'`,
you're either not in the project root or `pip install -e .` didn't finish.
The `-e` (editable) install is important — it registers the `temporal` package
from the local directory.

**⚠️ KNOWN ISSUE:** If you see `Connection refused`, the Temporal server (Terminal 1)
isn't running yet. Start it first.

### Terminal 3: Run a Scan

**You need a GitHub token** so the demo doesn’t hit rate limits. Without a token, GitHub allows 60 API requests/hour (~20 repos); we use the larger `temporalio` org, so the scan will fail or stall without a token. Create one at [GitHub → Settings → Developer settings → Personal access tokens](https://github.com/settings/tokens) with `repo` and `read:org`, then:

```bash
cd temporal-security-scanner
export GITHUB_TOKEN=ghp_your_token_here
python -m temporal.starter --org temporalio
```

You should see progress updates, then a compliance report.

**⚠️ RATE LIMITS:** Without a token: 60 requests/hour (~20 repos). With a token: 5,000 requests/hour. For this demo we point at the larger org (`temporalio`), so a token is required for it to complete.

---

## Choosing a Demo Organization

Pick an org that gives you 15–50 repos. Enough to show batching, not so many
that the scan takes minutes.

| Org | Repos (approx) | Notes |
|-----|----------------|-------|
| `eclipse-bci` | ~15 | Small, public, works without token |
| `temporalio` | ~50 | Temporal's own repos, good irony |
| `your-company` | varies | Most compelling but needs token with org access |

**Before the demo:** Run the scan once to verify it works and note the compliance
numbers. You don't want surprises on camera.

```bash
# Dry run — verify everything works
python -m temporal.starter --org YOUR_ORG --token ghp_xxx
```

---

## Interactive demo (demo_runner.py)

For a **narrated, concept-by-concept** walkthrough (great for first-time viewers or when you want the story to drive the demo):

1. **Terminal 1:** `temporal server start-dev`
2. **Terminal 2:** `python -m temporal.worker`
3. **Terminal 3:** `python demo_runner.py`

The script runs **Part 1** (core concepts: problem, architecture, encryption, sovereignty architecture, live scan, kill test, graceful cancel) with pauses and explanations. It then offers **Part 2** (production patterns: update handlers, durable timers, continue-as-new, schedules). All commands and "what to look for" are printed in the terminal; the narrative reflects the thinking behind the design (durable execution, HYOK encryption, queries vs signals).

**Kill test in demo_runner:** When it asks you to kill the worker, **close the worker terminal window** (or Ctrl+C / Task Kill). On Windows there is no `kill -9`; closing the window is the equivalent. Then open a new terminal, start the worker again, and press Enter in the demo script to continue.

**Windows (PowerShell):** To run the interactive demo you need `PYTHONPATH` set so `temporal` is found. In each terminal where you run Python (worker and demo_runner): `cd C:\dev\temporal-security-scanner`, then `$env:PYTHONPATH = "C:\dev\temporal-security-scanner"`. Optionally run `.\setup.ps1` first (in an elevated PowerShell if install needs it). Full sequence: [WINDOWS_SETUP.md](WINDOWS_SETUP.md#quick-run-powershell-commands-that-work).

---

## Demo Script (CLI flow)

This is the exact sequence for a live presentation using the **starter** and Web UI. Each section maps to the PRESENTATION.md talking points.

### 1. Show the "Before" Script (2 min)

```bash
# Open before/scanner.py in your editor
# Point out:
#   - sys.exit(1) on any error (line ~35)
#   - time.sleep(60) rate limit "handling" (line ~38)
#   - Sequential repo scanning (line ~83)
#   - No retry, no resume, no state tracking

# Optionally run it (use a token to avoid rate limits):
export GITHUB_TOKEN=ghp_your_token_here
python before/scanner.py --org temporalio
```

**What to say:** "This works. Until you hit a rate limit at repo 47 of 200
and lose everything. Or your SSH session drops. Or GitHub has a blip at 3am."

### 2. Show the Temporal Version (3 min)

Open files in this order:
1. `temporal/models.py` — "Data flows through typed dataclasses"
2. `temporal/activities.py` — "Side effects live here — API calls with heartbeats"
3. `temporal/workflows.py` — "Orchestration is deterministic — no I/O, just decisions"
4. `temporal/worker.py` — "The worker ties it all together"

**What to say:** "The workflow is a state machine. Activities are the side effects.
If any activity fails, Temporal retries it with exponential backoff. If the worker
crashes, the workflow picks up exactly where it left off."

### 3. The Kill Test (3 min) ⭐

This is the most memorable demo moment.

```bash
# Terminal 3: Start a scan with --no-wait so we can interact with it
python -m temporal.starter --org temporalio --no-wait

# Immediately go to Web UI: http://localhost:8233
# Show the workflow is "Running"
# Show the event history — activities completing

# Terminal 2: KILL THE WORKER (not graceful — hard kill)
# Linux/macOS:
ps aux | grep "temporal.worker" | grep -v grep
kill -9 <PID>
# Windows: close the worker PowerShell window (the X), or:
# taskkill /F /IM python.exe /FI "WINDOWTITLE eq *temporal*"  (if needed)

# ⭐ THE MOMENT: Go back to Web UI
# The workflow is STILL "Running"
# It's waiting for a worker to pick up the next task

# Terminal 2: Restart the worker
python -m temporal.worker

# Watch the Web UI: activities resume from where they stopped
# The workflow completes without re-scanning already-scanned repos
```

**What to say:** "I just killed the worker process. The workflow kept running.
When I restarted the worker, it picked up exactly where it left off. No repo
was scanned twice. That's durable execution."

**⚠️ TIMING:** Start the kill test with a medium-sized org (30+ repos).
If the org is too small, all activities complete before you can kill the worker.
If it's too large, you'll be waiting a while.

**⚠️ KNOWN ISSUE:** After killing the worker, the workflow tasks will time out
(~30 seconds for activities). You'll see "Activity task timed out" events in
the Web UI. This is correct behavior — Temporal detected the worker died and
will retry when a new worker starts.

### 4. The Signal Test (2 min)

```bash
# Terminal 3: Start a new scan (the previous one completed or was terminated)
python -m temporal.starter --org temporalio --no-wait

# Wait a few seconds for some repos to be scanned, then:
python -m temporal.starter --org temporalio --cancel "Demo: graceful stop"

# The scan stops after the current batch and generates a partial report.
# Query the progress to see the partial results:
temporal workflow query -w security-scan-temporalio --type progress
```

**What to say:** "Signals let you communicate with a running workflow. This isn't
an abort — the scan finishes its current batch, generates a partial report with
whatever data it collected, and exits cleanly."

### 5. The Encryption Story (2 min)

```bash
# Go to Web UI: http://localhost:8233
# Click on a completed workflow
# Click on any event (e.g., ActivityTaskCompleted)
# Look at the Input/Result: you'll see "binary/encrypted"

# Compare with what you'd see WITHOUT encryption:
# (Don't actually do this in the demo — just describe it)
# Without encryption: {"org": "temporalio", "token": "ghp_mysecret123"}
# With encryption: [binary/encrypted]
```

**What to say:** "By default, Temporal stores all workflow data in plaintext.
Our GitHub token would be sitting right there in the event history. The
PayloadCodec encrypts everything client-side — the server never sees our token.
And search attributes bypass the codec by design, so you'd never put secrets there."

### 6. The Go Comparison (2 min)

Open `go_comparison/SDK_COMPARISON.md` or show `go_comparison/workflow.go` side-by-side
with `temporal/workflows.py`.

**What to say:** "Same workflow, same Temporal concepts. Python uses a class with
decorators; Go uses a function with closures. Python's `asyncio.gather()` becomes
Go's `workflow.Go()` plus channels. The wire protocol is the same — a Python
workflow can call a Go activity and vice versa."

---

## Test Execution

```bash
cd temporal-security-scanner
pytest tests/ -v
```

### Expected Output

```
tests/test_workflow.py::test_full_scan_workflow PASSED
tests/test_workflow.py::test_progress_query PASSED
tests/test_workflow.py::test_cancel_scan_signal PASSED
tests/test_workflow.py::test_encryption_codec_roundtrip PASSED
tests/test_workflow.py::test_encryption_wrong_key_fails PASSED
tests/test_workflow.py::test_workflow_with_encryption PASSED
tests/test_workflow.py::test_repo_security_result_no_token_leak PASSED
tests/test_workflow.py::test_compliance_calculation PASSED
```

**⚠️ KNOWN ISSUE: pytest-asyncio deprecation warnings.**
You may see: `DeprecationWarning: The configuration option "asyncio_mode" is deprecated`.
In pytest-asyncio >= 0.24, the config moved. If this happens:

```toml
# In pyproject.toml, replace:
[tool.pytest.ini_options]
asyncio_mode = "auto"

# With:
[tool.pytest.ini_options]
asyncio_mode = "auto"
# Or if using >= 0.24:
# [tool.pytest.ini_options]
# [tool.pytest_asyncio]
# mode = "auto"
```

This is cosmetic — tests still pass.

**⚠️ KNOWN ISSUE: Test 3 (cancel signal) timing.**
In the time-skipping test environment, activities execute almost instantly.
The cancel signal races with batch completion. The test is written to be
robust to this — it checks that cancellation metadata is present regardless
of how many repos were scanned. But if you see flakiness here, it's this race.

**⚠️ KNOWN ISSUE: Temporal test server startup.**
The first test run downloads and starts an embedded Temporal server binary.
This can take 10-30 seconds on first run. Subsequent runs reuse the cached binary.

### What the Tests Prove

| Test | What It Demonstrates |
|------|---------------------|
| `test_full_scan_workflow` | Workflow correctly orchestrates fetch → scan → report |
| `test_progress_query` | Queryable state works (can check progress mid-scan) |
| `test_cancel_scan_signal` | Signal-based cancellation produces partial report |
| `test_encryption_codec_roundtrip` | Encrypt → decrypt preserves data perfectly |
| `test_encryption_wrong_key_fails` | Wrong key cannot read encrypted data |
| `test_workflow_with_encryption` | Full workflow works with encryption enabled |
| `test_repo_security_result_no_token_leak` | Result model has no token-carrying fields |
| `test_compliance_calculation` | Business logic (compliance check) is correct |

---

## Test Matrix: Manual Verification

Beyond `pytest`, here's what to verify manually before recording.

### Happy Path
| Step | Command | Expected |
|------|---------|----------|
| Start server | `temporal server start-dev` | Running on :7233 and :8233 |
| Start worker | `python -m temporal.worker` | "Worker started" log |
| Run scan | `export GITHUB_TOKEN=...; python -m temporal.starter --org temporalio` | Report printed, JSON saved |
| Check Web UI | `http://localhost:8233` | Workflow shows "Completed" |
| Check encryption | Click any event in Web UI | Payloads show "binary/encrypted" |

### Kill Test
| Step | Command | Expected |
|------|---------|----------|
| Start scan | `python -m temporal.starter --org temporalio --no-wait` | "Workflow started" |
| Verify running | Check Web UI | Status: "Running" |
| Kill worker | `kill -9 $(pgrep -f temporal.worker)` | Worker dies, no output |
| Check Web UI | Refresh | Still "Running", tasks timing out |
| Restart worker | `python -m temporal.worker` | "Worker started" log |
| Check Web UI | Wait ~30s | Activities resume, workflow completes |

### Signal Test
| Step | Command | Expected |
|------|---------|----------|
| Start scan | `python -m temporal.starter --org temporalio --no-wait` | "Workflow started" |
| Send cancel | `python -m temporal.starter --org temporalio --cancel "demo"` | "Signal sent" |
| Check result | `temporal workflow query -w security-scan-temporalio --type progress` | Status: "cancelled" |

### Error Handling
| Scenario | How to Trigger | Expected |
|----------|---------------|----------|
| Invalid org | `--org nonexistent-org-xyz` | Workflow fails with ValueError (non-retryable) |
| Invalid token | `--token ghp_invalid` | Workflow fails with ValueError (non-retryable) |
| No token, big org | `--org microsoft` (no token) | Rate limit after ~20 repos, retries with backoff |
| Worker not running | Start workflow without worker | Workflow stays "Running", tasks queue up |
| Re-run same org | Run starter twice for same org | Second run terminates first, starts fresh |

---

## Troubleshooting Guide

### "Connection refused" on worker/starter start

The Temporal server isn't running. Start it:
```bash
temporal server start-dev
```

### "ModuleNotFoundError: No module named 'temporal'"

You're not in the project root, or haven't installed:
```bash
cd temporal-security-scanner
pip install -e ".[dev]"
```

### "Workflow execution already started"

A previous scan for this org is still running (e.g., from a killed worker test).
The starter now handles this automatically with `id_conflict_policy=TERMINATE_EXISTING`.
If using the CLI directly:
```bash
temporal workflow terminate -w security-scan-<org>
```

### "GitHub API rate limit exceeded"

You're making too many unauthenticated requests. Set a token:
```bash
export GITHUB_TOKEN=ghp_your_token_here
```
Rate limits: 60/hour without token, 5,000/hour with token.
Each repo takes 3 API calls, so without a token you max out at ~20 repos.

### Worker logs show "Activity task timed out" after kill test

This is **correct behavior**. After you kill the worker:
1. The Temporal server waits for the activity's `start_to_close_timeout` (60s)
2. When it times out, Temporal marks it for retry
3. When a new worker starts, it picks up the retried task

### Tests download something on first run

The Temporal test framework downloads a small embedded server binary on first run.
It's cached at `~/.cache/temporalio/` after that.

### "InvalidToken" error from encryption

The worker and starter are using different encryption keys. Either:
- Both should use the dev key (default, no config needed)
- Or both should have the same `TEMPORAL_ENCRYPTION_KEY` env var

### Tests fail with "event loop is closed"

This is a pytest-asyncio version issue. Pin it:
```bash
pip install "pytest-asyncio>=0.23,<0.25"
```

---

## Architecture Decision Log

These are the "why" behind each Temporal pattern choice, ordered by how likely
they are to come up in Q&A.

### Why activities are synchronous (`def`, not `async def`)

The activities use the `requests` library, which is blocking I/O. The Temporal
Python SDK runs synchronous activities in a `ThreadPoolExecutor` automatically.
This is the **recommended approach** — simpler than converting everything to
`aiohttp`, and the thread pool provides natural concurrency.

If we used `async def` activities, we'd need `aiohttp` or `httpx` for async HTTP.
That's more dependencies and complexity for no real benefit here — the thread pool
already gives us 20 concurrent activities per worker.

### Why `asyncio.gather()` for parallel batches (not individual awaits)

`asyncio.gather(*tasks, return_exceptions=True)` starts all activities in a batch
concurrently and waits for all of them. The `return_exceptions=True` means a
single failed activity doesn't cancel the others — we get results (or errors)
for every repo in the batch.

Without `return_exceptions=True`, one failed activity would raise immediately
and cancel the rest of the batch. That would lose scan results unnecessarily.

### Why `ValueError` is non-retryable

`ValueError` signals "the input is wrong" — invalid org name, bad token. Retrying
won't help and would just burn GitHub API quota. `RuntimeError` signals "transient
failure" — timeout, rate limit, connection error. These should be retried with
exponential backoff.

This maps to a real-world pattern: distinguish between client errors (4xx → don't
retry) and server errors (5xx → retry). Our retry policy encodes this distinction
in `non_retryable_error_types`.

### Why `BATCH_SIZE = 10`

GitHub's authenticated rate limit is 5,000 requests/hour ≈ 83/minute. Each repo
takes 3 API calls. With batch size 10, we make 30 API calls per batch, which is
well within limits even with multiple batches per minute.

Batch size 10 also provides a good demo experience — large enough that the kill
test has time to work, small enough that progress updates are visible.

### Why `workflow.wait_condition(workflow.all_handlers_finished)`

Without this, a signal arriving during the final `generate_report` activity could
be silently dropped. The workflow would complete before the signal handler ran.
This is a real production bug that's hard to find — the `all_handlers_finished`
pattern prevents it.

### Why Fernet (not raw AES-256-GCM)

Fernet is the `cryptography` library's high-level recipe. It wraps AES-128-CBC
with HMAC-SHA256 in a format that includes a version number and timestamp. It's
deliberately hard to misuse — you can't accidentally use a bad IV or forget the MAC.

For production, you'd use envelope encryption with a KMS (AWS KMS, GCP KMS,
HashiCorp Vault). The Fernet key becomes a data encryption key (DEK), wrapped by
a key encryption key (KEK) managed by the KMS. This gives you key rotation without
re-encrypting existing workflow history.

### Why deterministic workflow IDs (`security-scan-{org}`)

This prevents duplicate scans. If someone starts two scans for the same org,
the second one terminates the first (via `id_conflict_policy=TERMINATE_EXISTING`).
Without deterministic IDs, you could have two workflows scanning the same org
concurrently, doubling your API usage and creating confusing results.

### Why `execution_timeout=30min`

Safety net for runaway workflows. Without this, a workflow scanning a massive
org (10,000+ repos) could run for hours, consuming GitHub API quota indefinitely.
The timeout kills the workflow cleanly after 30 minutes regardless of state.

---

## Known Limitations and Risks

### Things That Could Go Wrong During the Demo

1. **GitHub API changes the `security_and_analysis` response shape.**
   The activity parses `data.get("security_and_analysis", {})`. If GitHub
   changes this field name or nesting, results will show "unknown" for
   secret scanning. The scan won't crash — it'll just be less informative.
   Mitigation: test with your chosen org before recording.

2. **The Dependabot preview API header (`dorian-preview`) is deprecated.**
   GitHub has been moving Dependabot to a non-preview endpoint. If the
   preview header stops working, Dependabot results will show "disabled"
   for all repos. The scan still completes — just with incorrect data.
   Mitigation: this is acceptable for a Temporal demo (the point is the
   infrastructure, not the GitHub API accuracy).

3. **`temporalio` or your chosen org gets deleted/made private.**
   The scan will fail with a non-retryable ValueError. Choose a stable org
   and verify it exists before recording.

4. **Temporal Python SDK updates change the sandbox behavior.**
   The `workflow.unsafe.imports_passed_through()` context manager is the
   current approach for importing non-deterministic modules. If the SDK
   changes this API, workflows.py needs updating. Pin your SDK version
   in pyproject.toml for stability.

5. **`\r` (carriage return) progress output doesn't render in all terminals.**
   The starter's progress bar uses `\r` to overwrite the line. This may look
   garbled in some terminal emulators or when piped to a file. For the demo,
   use a standard terminal (iTerm2, Terminal.app, gnome-terminal).

### Things That Are Intentionally Simplified

1. **Token passed as workflow input (encrypted at rest).**
   In production, the token would be an environment variable on the worker,
   never passed through the workflow. We pass it as input to demonstrate
   encryption. Trade-off: passing it as input lets different callers use
   different tokens. Storing it on the worker limits you to one token.

2. **No Codec Server for the Web UI.**
   The Web UI shows `[binary/encrypted]` for all payloads. To see decrypted
   data, you'd deploy a Codec Server — an HTTP endpoint behind auth that
   decrypts payloads on demand. We skip this for demo simplicity.

3. **Failure messages are not encrypted.**
   Exception messages and stack traces are stored in plaintext by default.
   We document the `encode_common_attributes=True` pattern but don't enable
   it because it makes Web UI debugging impossible without a Codec Server.

4. **No `continue_as_new` for long-running scans.**
   If this scanner ran on a schedule (weekly scans), the workflow history
   would grow unboundedly. `continue_as_new` resets the history while
   preserving state. We mention this in "What I'd do next" but don't
   implement it.

---

## File Inventory

| File | Lines | Purpose | Temporal Concepts |
|------|-------|---------|-------------------|
| `before/scanner.py` | 154 | Original script (the "pain") | None — this is the problem |
| `temporal/models.py` | 98 | Shared data models | Dataclass serialization |
| `temporal/activities.py` | 174 | GitHub API calls | Activities, heartbeating, retry |
| `temporal/workflows.py` | 228 | Orchestration logic | Workflows, queries, signals, parallel execution |
| `temporal/encryption.py` | 119 | Payload encryption | PayloadCodec, data converter |
| `temporal/worker.py` | 122 | Worker configuration | Worker, task queue, thread pool |
| `temporal/starter.py` | 164 | CLI for starting/cancelling scans | Client, workflow handle, signals |
| `tests/test_workflow.py` | 305 | 8 test cases | Test framework, activity mocking, time-skipping |
| `go_comparison/workflow.go` | ~300 | Go SDK equivalent (annotated) | Cross-SDK comparison |
| `go_comparison/SDK_COMPARISON.md` | ~240 | Feature-by-feature comparison | SDK selection guidance |
| `PRESENTATION.md` | ~200 | Talk script and Q&A prep | Presentation narrative |
| `README.md` | ~240 | Project overview | Before/after comparison |
| `DEMO.md` | — | How to run the demo + thinking behind it | Demo flow, checklist, two options |
| `demo_runner.py` | ~1100 | Interactive narrated demo ("The 2am Incident") | Part 1 + Part 2, concepts + live proof |
