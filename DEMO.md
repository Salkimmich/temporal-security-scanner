# How to Run the Demo

This page ties the **demo flow** to the **thinking behind it**: what we're showing and why. Use it to prepare for a live run or to explain the repo to someone else.

## What We're Demonstrating

The repo shows a single idea: **durable execution** — your workflow logic runs to completion even when processes die, networks blip, or rate limits hit. We prove that with a real security scanner (GitHub org audit) and four concrete demos:

| Demo | What we show | Why it matters |
|------|----------------|----------------|
| **Live scan** | Start a workflow; watch activities in the Web UI; query progress from the CLI | Observability and structure: workflows are first-class, queryable state machines. |
| **Encryption** | Payloads in the Web UI show `[binary/encrypted]`; we connect without the key and show ciphertext | Security: tokens and scan data never hit the server in plaintext (Hold Your Own Key). |
| **Kill test** | Kill the worker mid-scan; workflow stays "Running"; restart worker → scan resumes from the next repo | Durability: no checkpoint file, no Redis — the event history *is* the state. |
| **Graceful cancel** | Send a signal; workflow stops after the current batch and returns a partial report | Control: you can stop long runs without losing what’s already done. |

The **thinking process** behind the design:

1. **Separate side effects from orchestration** — Anything that touches the network (GitHub API) is an *activity*; the loop, batching, and progress tracking are *workflow*. That split is what makes replay and retries possible.
2. **Encrypt by default for sensitive data** — The scanner handles tokens and compliance data. A PayloadCodec keeps that out of the server’s view.
3. **Use signals for “stop gracefully,” not “abort”** — So we can return partial results and a reason instead of losing everything.

## Two Ways to Run the Demo

**GitHub token required for the demo to work.** Without a token, GitHub allows only 60 API requests/hour (~20 repos); the demo uses a larger org (`temporalio`) and will hit rate limits. Create a [Personal Access Token](https://github.com/settings/tokens) with `repo` and `read:org` scopes, then set `GITHUB_TOKEN` in your environment before running any scan.

### Option A: Interactive narrated demo (recommended for first run)

Best if you want the **story** to drive the demo: concepts, then live proof, then kill test and cancel.

**Prerequisites:** Three terminals; `GITHUB_TOKEN` set (see above). Terminal 1 = Temporal server, Terminal 2 = worker, Terminal 3 = demo script.

**Linux/macOS:**

```bash
# Terminal 1
temporal server start-dev

# Terminal 2
pip install -e ".[dev]"   # once
python -m temporal.worker

# Terminal 3 (set token first so the scan doesn't hit rate limits)
export GITHUB_TOKEN=ghp_your_token_here
python demo_runner.py
```

**Windows (PowerShell):** Set `$env:GITHUB_TOKEN = "ghp_your_token_here"` before running the demo. Use the project’s setup script, then set `PYTHONPATH` so Python finds the `temporal` package. Exact commands that work:

```powershell
# Terminal 1 — optional: RunAs if setup needs to install Temporal CLI / PATH
# powershell -Command "Start-Process powershell -Verb RunAs"
# In that window: cd C:\dev\temporal-security-scanner; .\setup.ps1
temporal server start-dev

# Terminal 2
cd C:\dev\temporal-security-scanner
$env:PYTHONPATH = "C:\dev\temporal-security-scanner"
python -m temporal.worker

# Terminal 3 (set token first so the demo doesn't hit rate limits)
$env:GITHUB_TOKEN = "ghp_your_token_here"
cd C:\dev\temporal-security-scanner
$env:PYTHONPATH = "C:\dev\temporal-security-scanner"
python demo_runner.py
```

See [WINDOWS_SETUP.md](WINDOWS_SETUP.md) for the full “Quick run” sequence (including RunAs and setup.ps1).

Follow the prompts. The script walks through the problem, the architecture, a live scan, encryption proof, the kill test (it will ask you to close the worker window, then restart it), and graceful cancellation. For the kill step on Windows, closing the worker window is the equivalent of `kill -9`.

### Option B: CLI + Web UI (scripted / short demo)

Best if you already know the story and want to **execute the same steps by hand** (e.g. for a tight 10‑minute slot).

1. Start server and worker (as above). Set **GITHUB_TOKEN** so the scan doesn’t hit rate limits.
2. **Run a scan (no wait):**  
   `python -m temporal.starter --org temporalio --no-wait`
3. **Open Web UI:** http://localhost:8233 — show workflow “Running” and event history.
4. **Query progress:**  
   `python -m temporal.starter --org temporalio --query`
5. **Kill test:** Close the worker window (or `kill -9` on Linux/macOS), show workflow still “Running”, restart worker, show completion without re-scanning repos.
6. **Cancel test:** Start another scan with `--no-wait`, then:  
   `python -m temporal.starter --org temporalio --cancel "Demo: graceful stop"`  
   Show partial report and status.

Full step-by-step and timing: [PLAYBOOK.md](PLAYBOOK.md). What to say: [PRESENTATION.md](PRESENTATION.md).

## Checklist Before You Present

- [ ] **GITHUB_TOKEN set** — create a token at [GitHub → Settings → Tokens](https://github.com/settings/tokens) (`repo`, `read:org`) so the scan doesn’t hit rate limits.
- [ ] `pytest tests/ -v` — all 8 tests pass.
- [ ] Temporal server and worker run; one scan completes and prints a report (use `--org temporalio`).
- [ ] Web UI shows workflow “Completed” and payloads as `[binary/encrypted]`.
- [ ] Kill test: worker killed → workflow still “Running” → worker restarted → scan resumes and completes.
- [ ] Cancel: signal sent → workflow finishes current batch and returns partial report.
- [ ] (Optional) Run `python demo_runner.py` once start-to-finish to confirm narrative and timing.

## Where Things Live in the Repo

- **Problem (before):** `before/scanner.py` — sequential script, no retry, no resume, no encryption.
- **Solution (after):** `temporal/` — workflows, activities, encryption, worker, starter.
- **Demo narrative:** `demo_runner.py` — interactive “2am Incident” walkthrough.
- **Runbook:** [PLAYBOOK.md](PLAYBOOK.md). **Talking points:** [PRESENTATION.md](PRESENTATION.md). **Windows:** [WINDOWS_SETUP.md](WINDOWS_SETUP.md).

This structure is intentional: README explains the *what* and *why*, PLAYBOOK and DEMO explain *how to run it*, and PRESENTATION explains *what to say*.
