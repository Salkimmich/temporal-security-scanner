# Temporalizing a GitHub Security Scanner

A before-and-after demonstration of converting a real-world Python script into a Temporal workflow application. Click on the image below to watch the demo! Read below for more insights on how to get started.

[<img src="Screenshot 2026-02-20 at 09.43.19.png">](https://drive.google.com/file/d/1dgRSQyodot7vif9Y_COP-WTf4_wOIndf/view?usp=sharing)


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
│   ├── activities.py           # GitHub API calls wrapped as Temporal activities
│   ├── workflows.py            # Orchestration logic with signals and safe handler patterns
│   ├── encryption.py           # PayloadCodec for end-to-end AES encryption
│   ├── worker.py               # Security-hardened worker with encrypted data converter
│   └── starter.py              # Starts, queries, and cancels workflows with encryption
├── go_comparison/              # Annotated Go equivalent (reference, not runnable)
│   ├── SDK_COMPARISON.md       # Side-by-side analysis of Python vs Go SDKs
│   ├── models.go               # Go structs vs Python dataclasses
│   ├── activities.go           # Go struct methods vs Python decorated functions
│   ├── workflow.go             # Go function vs Python class — the key differences
│   └── worker/main.go          # Go worker registration pattern
├── tests/
│   └── test_workflow.py        # Tests using Temporal's in-memory test server
├── pyproject.toml
└── README.md
```

## Getting Started

### Prerequisites

- Python 3.11+
- [Temporal CLI](https://docs.temporal.io/cli) (includes a local dev server)

### 1. Install Dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 2. Start the Temporal Dev Server

In a separate terminal:

```bash
temporal server start-dev
```

This starts a local Temporal server with the Web UI at http://localhost:8233.

### 3. Start the Worker

In another terminal:

```bash
python -m temporal.worker
```

### 4. Run a Scan

```bash
# Scan a public org (no token needed for public repos)
python -m temporal.starter --org eclipse-bci

# Scan with authentication (for private repos and higher rate limits)
export GITHUB_TOKEN=ghp_your_token_here
python -m temporal.starter --org your-org

# Start scan without waiting (fire and forget)
python -m temporal.starter --org eclipse-bci --no-wait
```

### 5. Query Progress (While Running)

```bash
# Using the starter (recommended)
python -m temporal.starter --org eclipse-bci --query

# Or using the Temporal CLI directly
temporal workflow query -w security-scan-eclipse-bci --type progress

# Cancel a running scan gracefully
python -m temporal.starter --org eclipse-bci --cancel "Rate limit hit"
```

### 6. Run Tests

```bash
pytest tests/ -v
```

Tests use Temporal's built-in time-skipping test environment — no external server needed.

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
python scanner.py --org eclipse-bci
# or with a token:
python scanner.py --org eclipse-bci --token ghp_xxx
```
