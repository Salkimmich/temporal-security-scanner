# Temporalizing a GitHub Security Scanner

A before-and-after demonstration of converting a real-world Python script into a Temporal workflow application, prepared for presentation to Temporal engineers as if explaining to a developer audience at a meetup or conference. Click on the image below to watch the demo; read on for how to get started and how each presentation objective is covered.

[<img src="Screenshot 2026-02-20 at 09.43.19.png" alt="Demo screenshot">](https://drive.google.com/file/d/1dgRSQyodot7vif9Y_COP-WTf4_wOIndf/view?usp=sharing)

**How this demo maps to the presentation objectives**

- **Original problem and how the code worked before** — The [The Problem](#the-problem) section describes what the scanner does and the five ways it breaks at scale (rate limits, partial failures, no visibility, fragile error handling, no resilience). The **before** version lives in [`before/scanner.py`](before/scanner.py): a single script that fetches repos and checks security settings sequentially, with `sys.exit(1)` on errors and `time.sleep(60)` on rate limits. You can run it yourself under [Running the "Before" Version](#running-the-before-version).
- **How it was broken apart and Temporalized (rationale)** — [The Solution](#the-solution) and the [Architecture](#architecture) diagram show the split: side effects (GitHub API calls) become **Activities**; orchestration (batching, progress, reporting) becomes the **Workflow**. [The Transformation: Step by Step](#the-transformation-step-by-step) explains how we identified activities vs workflow logic and the design choices (retry policy, batch size, heartbeating, non-retryable error types). The **after** implementation is in `temporal/`: workflows, activities, worker, encryption, and a starter CLI.
- **Challenges (failure handling, state management, scaling)** — [What Breaks in Production](#what-breaks-in-production) spells out the challenges. The [What Temporal Gives Us](#what-temporal-gives-us) table shows how each challenge is addressed (retries, replay, queries, signals, encryption). [The Kill Test](#the-kill-test) and the live demo prove durability: kill the worker mid-scan and the workflow resumes from the next repo.
- **Why the Temporalized version improves** — Summarized in the same table and in [Security Architecture](#security-architecture) (payload encryption, safe signal handling, defense in depth). The demo itself—running a scan, querying progress, killing the worker, and watching recovery—is the evidence.
- **Trade-offs and considerations** — [Trade-offs and Considerations](#trade-offs-and-considerations) covers added complexity, infrastructure dependency, determinism constraints, and serialization. [Key Design Decisions](#key-design-decisions) documents why we chose synchronous activities, batches of 10, `ValueError` as non-retryable, and so on.
- **Documenting and teaching for a developer audience** — This README explains how to get the demo up and running ([Run the demo](#run-the-demo-quick-start) and [Project Structure](#project-structure)). [DEMO.md](DEMO.md) ties the demo flow to the thinking behind it; [PLAYBOOK.md](PLAYBOOK.md) is the step-by-step runbook and troubleshooting; [PRESENTATION.md](PRESENTATION.md) is the talking points and Q&A prep for the live walkthrough. The interactive [`demo_runner.py`](demo_runner.py) (“The 2am Incident”) walks through concepts, encryption, the kill test, and graceful cancel with on-screen narrative.
- **Deliverables** — **Before/after code:** `before/scanner.py` (original) and `temporal/` (Worker, Workflow, Activities, plus encryption, starter, and tests). **README:** the section below gets you from clone to a running scan in three terminals. The app is **testable** (`pytest tests/ -v` uses Temporal’s test server; no external server needed) and **resilient** (retries, timeouts, replay, and the kill test).

---

## Run the demo (quick start)

You need **three terminals**. All commands assume you're in the project root (e.g. `C:\dev\temporal-security-scanner` or `~/temporal-security-scanner`).

### Prerequisites

- **Python 3.11+**
- **Temporal CLI** (provides the local dev server) — [install](https://docs.temporal.io/cli)
- **GitHub Personal Access Token (recommended)** — Without a token, GitHub allows only **60 API requests/hour** (~20 repos before rate limit). With a token you get **5,000 requests/hour**, so the demo can scan a larger org (e.g. `temporalio`) without hitting limits. Create one at [GitHub → Settings → Developer settings → Personal access tokens](https://github.com/settings/tokens) with `repo` and `read:org` scopes, then set `GITHUB_TOKEN` in your environment before running the demo.

### One-time setup

**Linux / macOS:**

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

**Windows (PowerShell):** You can either create a venv and install deps the same way as Linux (`.\.venv\Scripts\Activate.ps1` then `pip install -e ".[dev]"`), or use the project’s **setup script** for a one-time install:

- **What `setup.ps1` does:** Checks Python 3.11+, installs the Temporal CLI if missing (and adds it to your user PATH), installs the project’s Python dependencies with pip, and sets `PYTHONPATH` for that session. It does not create a venv.
- **When to use it:** Handy on a fresh Windows machine when you want one script to get Temporal CLI + deps without following the Linux-style venv steps.
- **How to run it:** From the project root in PowerShell. If the script needs to install Temporal CLI and update PATH, you may need to run PowerShell as Administrator first:

```powershell
# If you need admin rights (e.g. for PATH when installing Temporal CLI):
powershell -Command "Start-Process powershell -Verb RunAs"
# In the new (elevated) window that opens:
cd C:\dev\temporal-security-scanner
.\setup.ps1
```

Otherwise, in a normal PowerShell window:

```powershell
cd C:\dev\temporal-security-scanner
.\setup.ps1
```

After setup, you still need to set `$env:PYTHONPATH = "C:\dev\temporal-security-scanner"` in any terminal where you run the worker or demo (see Terminal 2 and 3 below).

### Terminal 1 — Temporal server

```bash
temporal server start-dev
```

Leave this running. Web UI: **http://localhost:8233**

### Terminal 2 — Worker

**Linux / macOS:**

```bash
# Activate venv if you haven't in this terminal
source .venv/bin/activate
python -m temporal.worker
```

**Windows (PowerShell):**

```powershell
cd C:\dev\temporal-security-scanner
$env:PYTHONPATH = "C:\dev\temporal-security-scanner"
python -m temporal.worker
```

You should see: `Payload encryption: ENABLED` and `Worker started on task queue 'security-scanner'`.

### Terminal 3 — Run the demo

**Option A — CLI (quick scan):**

Set a GitHub token first so the scan doesn’t hit rate limits (see Prerequisites above). Then:

```bash
# Linux/macOS (activate venv if needed)
export GITHUB_TOKEN=ghp_your_token_here
python -m temporal.starter --org temporalio
```

```powershell
# Windows
$env:GITHUB_TOKEN = "ghp_your_token_here"
cd C:\dev\temporal-security-scanner
$env:PYTHONPATH = "C:\dev\temporal-security-scanner"
python -m temporal.starter --org temporalio
```

You’ll get progress and a compliance report. The example uses the `temporalio` org (larger); without a token you’ll hit the 60 req/hr limit after ~20 repos.

**Option B — Interactive narrated demo (“The 2am Incident”):**

Walks through concepts, live scan, encryption proof, kill-the-worker test, and graceful cancel.

```bash
# Linux/macOS
python demo_runner.py
```

```powershell
# Windows (set GITHUB_TOKEN first so the demo doesn't hit rate limits)
$env:GITHUB_TOKEN = "ghp_your_token_here"
cd C:\dev\temporal-security-scanner
$env:PYTHONPATH = "C:\dev\temporal-security-scanner"
python demo_runner.py
```

Follow the prompts. When it asks to “kill the worker,” close the **Terminal 2** window, then open a new Terminal 2 and start the worker again; return to Terminal 3 and press Enter.

### What to try next (Terminal 3)

| Goal | Command |
|------|---------|
| Query progress (scan running) | `python -m temporal.starter --org temporalio --query` |
| Start scan without waiting | `python -m temporal.starter --org temporalio --no-wait` |
| Cancel a running scan | `python -m temporal.starter --org temporalio --cancel "reason"` |
| Run tests (no server needed) | `pytest tests/ -v` |

On Windows, run `cd C:\dev\temporal-security-scanner` and `$env:PYTHONPATH = "C:\dev\temporal-security-scanner"` in Terminal 3 before these commands if you haven’t already.

**The kill test:** Start a scan with `--no-wait`, then close the worker window (Terminal 2). In the Web UI the workflow stays “Running.” Restart the worker; the scan resumes from the next repo.

More detail: **[DEMO.md](DEMO.md)** (what we’re demonstrating and why), **[PLAYBOOK.md](PLAYBOOK.md)** (full demo script and timing), **[WINDOWS_SETUP.md](WINDOWS_SETUP.md)** (Windows-only setup and troubleshooting).

---

## Documentation map

| Doc | Purpose |
|-----|---------|
| **README.md** (this file) | Run the demo + problem, solution, architecture |
| **[DEMO.md](DEMO.md)** | What we show, why, and pre-presentation checklist |
| **[PLAYBOOK.md](PLAYBOOK.md)** | Step-by-step demo script and what to say |
| **[PRESENTATION.md](PRESENTATION.md)** | Talking points and Q&A prep |
| **[WINDOWS_SETUP.md](WINDOWS_SETUP.md)** | Windows install, venv, and proven PowerShell sequence |

---
## The Problem

I maintain a security scanner that audits GitHub organizations for proper security configuration — checking whether repositories have secret scanning, Dependabot alerts, and code scanning (GHAS) enabled. The [original script](before/scanner.py) works. Until it doesn't.

### What Breaks in Production

When you point this scanner at a large organization (CNCF has 200+ repos, Eclipse Foundation has 400+), several things go wrong:

1. **Rate limiting.** GitHub's API allows 5,000 authenticated requests per hour. Each repo requires 3 API calls. Scanning 400 repos = 1,200 calls. Hit a rate limit and the script sleeps, hopes, and retries — or crashes.

2. **Partial failures.** If the network drops at repo 150 of 300, you start over. There's no checkpoint, no resume. All prior work is lost.

3. **No visibility.** While the script is running — which can take 20+ minutes for large orgs — you have no way to check how far along it is or what it's found so far.

4. **Fragile error handling.** The original script calls `sys.exit(1)` on errors. A single bad repo kills the entire scan.

5. **Not resilient.** Close your laptop lid? Scan gone. Server restart? Start over. OOM kill? Same.

These are exactly the problems Temporal is designed to solve.

## The Solution

The Temporal version wraps each distinct piece of work — fetching the repo list, checking each repo's security settings, generating the report — as an **Activity**. A **Workflow** orchestrates these activities with retry policies, parallelism, and queryable state.

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                  SecurityScanWorkflow                        │
│                                                              │
│  1. Execute Activity: fetch_org_repos(org)                   │
│     └─ Returns list of repos (paginated, with heartbeat)     │
│                                                              │
│  2. For each batch of 10 repos (parallel):                   │
│     └─ Execute Activity: check_repo_security(org, repo)      │
│        └─ Checks secret scanning, Dependabot, code scanning  │
│        └─ Retries automatically on transient failures        │
│                                                              │
│  3. Execute Activity: generate_report(org, results)          │
│     └─ Aggregates results into compliance summary            │
│                                                              │
│  Queries (available anytime):                                │
│     • progress → ScanProgress (repos scanned, % complete)    │
│     • results_so_far → partial results                       │
└─────────────────────────────────────────────────────────────┘
```

### What Temporal Gives Us

| Problem | Before | After (Temporal) |
|---------|--------|-------------------|
| Rate limit hit | Script sleeps 60s and hopes | Activity retries with exponential backoff (2s → 4s → 8s → 60s max) |
| Crash at repo 150/300 | Start over from scratch | Workflow replays; completed activities skip; resumes at repo 151 |
| "How far along is it?" | No idea, check the terminal | `temporal workflow query -w scan-myorg --type progress` |
| One bad repo | `sys.exit(1)` kills everything | That repo's activity fails after retries; others continue |
| Worker process dies | Scan is gone | Restart worker; Temporal reassigns pending tasks; scan continues |
| Need to stop a long scan | Ctrl+C, lose everything | Signal-based cancellation — still get a partial report |
| GitHub token in event history | N/A (no history) | AES-encrypted via PayloadCodec — server never sees plaintext |
| Need to scan weekly | Set up a cron job and pray | `temporal schedule create` — Temporal manages the schedule |

### Security Architecture

Temporalizing code doesn't *automatically* make it secure. By default, Temporal stores all workflow history — including activity inputs and outputs — in plaintext. For a security scanner that handles GitHub API tokens and private repository data, this matters.

This project demonstrates three layers of security hardening:

**1. Payload Encryption (PayloadCodec)** — All data is encrypted client-side before reaching the Temporal server using AES-128-CBC (Fernet). The server only stores ciphertext. The Temporal Web UI shows `[binary/encrypted]` for every payload.

**2. Safe Signal Handling** — The `cancel_scan` signal allows operators to gracefully stop a long scan. The workflow waits for `all_handlers_finished` before completing, preventing signal races.

**3. Defense in Depth** — Non-retryable error types prevent auth failures from burning rate limits. Execution timeout (30 min) prevents runaway scans. Deterministic workflow IDs prevent duplicates. Activity heartbeating detects stuck scans early.

**What's NOT encrypted (by design):** Search attributes bypass the codec (used for indexing, not secrets). Failure messages/stack traces are configurable via `encode_common_attributes` on the failure converter — omitted here for demo readability.

## Project Structure

```
.
├── before/
│   └── scanner.py              # Original script (the "before")
├── temporal/
│   ├── __init__.py
│   ├── models.py               # Shared dataclasses (ScanInput, RepoSecurityResult, etc.)
│   ├── activities.py          # GitHub API calls wrapped as Temporal activities
│   ├── workflows.py            # Orchestration logic with signals and safe handler patterns
│   ├── encryption.py           # PayloadCodec for end-to-end AES encryption
│   ├── worker.py               # Security-hardened worker with encrypted data converter
│   └── starter.py              # Starts, queries, and cancels workflows (CLI)
├── demo_runner.py              # Interactive narrated demo ("The 2am Incident") — 3 terminals
├── setup.ps1                   # Windows one-time setup (Temporal CLI + Python deps)
├── go_comparison/              # Annotated Go equivalent (reference, not runnable)
│   ├── SDK_COMPARISON.md       # Side-by-side analysis of Python vs Go SDKs
│   ├── models.go               # Go structs vs Python dataclasses
│   ├── activities.go          # Go struct methods vs Python decorated functions
│   ├── workflow.go             # Go function vs Python class — the key differences
│   └── worker/main.go          # Go worker registration pattern
├── tests/
│   └── test_workflow.py        # Tests using Temporal's in-memory test server
├── DEMO.md                     # How to run the demo + thinking behind it
├── PLAYBOOK.md                 # Demo script: steps, timing, troubleshooting
├── PRESENTATION.md             # Talking points and Q&A
├── WINDOWS_SETUP.md            # Windows-specific setup and demo steps
├── pyproject.toml
└── README.md
```

## The Transformation: Step by Step

### Identifying Activities

The key question: *"What has side effects?"* Anything that touches the network, disk, or external state becomes an activity.

| Original Code | Temporal Activity | Why |
|---|---|---|
| `fetch_repositories()` | `fetch_org_repos` | HTTP calls to GitHub; can fail, rate limit |
| `check_repo_security()` | `check_repo_security` | 3 HTTP calls per repo; independently retryable |
| `json.dump(results)` | `generate_report` | Could be extended to write to S3, send Slack, etc. |

### Identifying the Workflow

Everything else — the loop over repos, the progress tracking, the decision to scan in batches — is **orchestration logic**. It's deterministic: given the same activity results, it produces the same outcome. That's the workflow.

### Key Design Decisions

**Why synchronous activities?** The `requests` library is blocking. Temporal's Python SDK handles this via `ThreadPoolExecutor` — the recommended pattern for blocking I/O.

**Why batches of 10?** Balances concurrency (faster than sequential) with rate limit respect (won't fire 400 requests simultaneously). This is configurable.

**Why `non_retryable_error_types=["ValueError"]`?** A `ValueError` means the org doesn't exist or the token is invalid. Retrying won't help. But a `RuntimeError` (rate limit, timeout) is transient and should be retried.

**Why `heartbeat` in `fetch_org_repos`?** For large orgs, pagination can take a while. Heartbeating tells Temporal "I'm still alive" so it doesn't assume the activity has hung and retry it unnecessarily.

## The Kill Test

The most compelling demo of Temporal's value:

1. Start a scan against a large org
2. Watch it begin processing repos
3. **Kill the worker process** (Ctrl+C or `kill -9`)
4. Check the Temporal Web UI — the workflow is still "Running"
5. Restart the worker
6. Watch the scan **resume from where it left off**

Completed activity results are stored in Temporal's event history. They don't re-execute. The workflow picks up exactly at the next pending activity.

## Trade-offs and Considerations

**Added complexity.** The original is one file, ~150 lines. The Temporal version is multiple files with workflow/activity separation, data models, a worker process, and a starter script. This is the right trade-off for production reliability, but would be over-engineering for a one-off local script.

**Infrastructure dependency.** Temporal requires a running server. For development, `temporal server start-dev` makes this trivial. For production, you'd use Temporal Cloud or self-host.

**Determinism constraints.** Workflow code can't call `datetime.now()`, `random()`, or do any I/O directly. All side effects must go through activities. This takes some getting used to, but the Python SDK's sandbox catches violations early with clear error messages.

**Data serialization.** All activity inputs and outputs must be serializable (via JSON by default). This is why we use `dataclasses` — they serialize cleanly and are the SDK's recommended pattern.

## Go SDK Comparison

The `go_comparison/` directory contains the same workflow, activities, and worker expressed in Go, with detailed inline annotations comparing the two SDKs. This is not a separate runnable project — it's a reference for understanding how Temporal concepts map across languages.

See **[`go_comparison/SDK_COMPARISON.md`](go_comparison/SDK_COMPARISON.md)** for the full analysis, covering:

- Workflow definition (class vs function)
- Parallel execution (`asyncio.gather()` vs `workflow.Go()` + channels)
- Error handling (exceptions vs explicit error returns)
- Activity registration (functions vs struct methods)
- Determinism enforcement (runtime sandbox vs static analysis)

The key takeaway: the Temporal *concepts* are identical across SDKs. The *expression* differs in ways that reflect each language's idioms. Choosing between them is a team and ecosystem decision, not a capability one.

## Running the "Before" Version

To see the original script (for comparison):

```bash
cd before/
pip install requests
# Use a GitHub token to avoid rate limits (60/hr without, 5000/hr with):
export GITHUB_TOKEN=ghp_your_token_here
python scanner.py --org temporalio
# or pass token on the command line:
python scanner.py --org temporalio --token ghp_xxx
```
