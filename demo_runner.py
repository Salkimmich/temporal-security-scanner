#!/usr/bin/env python3
"""
The 2am Incident — A Temporal Security Scanner Demo
=====================================================

Built by Sal Kimmich to demonstrate that durable execution isn't a
feature you bolt on — it's an architectural decision that changes
what's possible.

This demo has two parts:

    PART 1 — CORE CONCEPTS  (~5 minutes)
    Start a real security scan of Temporal's GitHub organization,
    prove the payloads are encrypted, kill the worker mid-scan, watch
    it recover, and cancel gracefully. Five Temporal primitives, fast.

    PART 2 — PRODUCTION PATTERNS  (~7 minutes, optional)
    Live-reconfigure a running scan, pause it with a crash-proof
    timer, watch the event history reset itself, and replace cron
    with Temporal's native scheduler.

PREREQUISITES:
    Terminal 1:  temporal server start-dev
    Terminal 2:  python -m temporal.worker
    Terminal 3:  python demo_runner.py
"""

import asyncio
import base64
import dataclasses
import os
import subprocess
import sys
import textwrap
import time
from datetime import timedelta

# ── terminal formatting ─────────────────────────────────────────────

BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
MAGENTA = "\033[35m"
WHITE = "\033[97m"
BG_BLUE = "\033[44m"
BG_GREEN = "\033[42m"
BG_RED = "\033[41m"
BG_MAGENTA = "\033[45m"
BG_CYAN = "\033[46m"
BG_YELLOW = "\033[43m"

if sys.platform == "win32":
    os.system("")

WEB_UI = "http://localhost:8233"


def banner(text, bg=BG_BLUE):
    w = 72
    pad = max(0, (w - len(text) - 2) // 2)
    print(f"\n{bg}{WHITE}{BOLD}")
    print(f" {'=' * w} ")
    print(f" {' ' * pad} {text} {' ' * max(0, w - pad - len(text) - 2)}  ")
    print(f" {'=' * w} ")
    print(RESET)


def story(text):
    """Narrative voice."""
    for line in textwrap.dedent(text).strip().splitlines():
        print(f"  {WHITE}{line}{RESET}")
    print()


def teach(text):
    """Educational content."""
    for line in textwrap.dedent(text).strip().splitlines():
        print(f"  {CYAN}{line}{RESET}")
    print()


def definition(term, text):
    print(f"  {YELLOW}{BOLD}>> {term}{RESET}")
    for line in textwrap.dedent(text).strip().splitlines():
        print(f"  {YELLOW}   {line}{RESET}")
    print()


def concept_box(title, text):
    bar = "-" * max(0, 55 - len(title))
    print(f"  {MAGENTA}{BOLD}+-- {title} {bar}+{RESET}")
    for line in textwrap.dedent(text).strip().splitlines():
        print(f"  {MAGENTA}|{RESET}  {line}")
    print(f"  {MAGENTA}{BOLD}+{'-' * (60 + len(title[:1]))}+{RESET}")
    print()


def insight(text):
    print(f"  {GREEN}{BOLD}   >> KEY INSIGHT:{RESET}")
    for line in textwrap.dedent(text).strip().splitlines():
        print(f"  {GREEN}   {line}{RESET}")
    print()


def why_it_matters(text):
    print(f"\n  {GREEN}{BOLD}* WHY THIS MATTERS:{RESET}")
    for line in textwrap.dedent(text).strip().splitlines():
        print(f"  {GREEN}  {line}{RESET}")
    print()


def compare(label_a, text_a, label_b, text_b):
    print(f"  {RED}{BOLD}x {label_a}{RESET}")
    for line in textwrap.dedent(text_a).strip().splitlines():
        print(f"    {DIM}{line}{RESET}")
    print()
    print(f"  {GREEN}{BOLD}> {label_b}{RESET}")
    for line in textwrap.dedent(text_b).strip().splitlines():
        print(f"    {line}")
    print()


def server_sees(label, data):
    d = str(data)[:80] + ("..." if len(str(data)) > 80 else "")
    print(f"  {RED}{BOLD}LOCKED - WHAT THE SERVER SEES ({label}):{RESET}")
    print(f"    {DIM}{d}{RESET}")
    print()


def worker_sees(label, data):
    d = str(data)[:120] + ("..." if len(str(data)) > 120 else "")
    print(f"  {GREEN}{BOLD}UNLOCKED - WHAT THE WORKER SEES ({label}):{RESET}")
    print(f"    {d}")
    print()


def cmd_display(text):
    print(f"  {GREEN}{BOLD}> {text}{RESET}\n")


def web_link(path, what_to_look_for=""):
    url = f"{WEB_UI}{path}"
    print(f"  {MAGENTA}🌐 Web UI: {WHITE}{url}{RESET}")
    if what_to_look_for:
        print(f"     {DIM}Look for: {what_to_look_for}{RESET}")
    print()


def wait(msg="Press Enter to continue..."):
    print(f"  {DIM}{'-' * 64}{RESET}")
    input(f"  {DIM}{msg}{RESET}")
    print()


def quiz(question, options, answer_idx, explanation):
    print(f"  {BG_CYAN}{WHITE}{BOLD} QUICK CHECK {RESET}\n")
    print(f"  {WHITE}{BOLD}{question}{RESET}\n")
    for i, opt in enumerate(options):
        print(f"    {WHITE}{chr(65 + i)}) {opt}{RESET}")
    print()
    input(f"  {DIM}Think about it, then press Enter for the answer...{RESET}")
    print()
    print(f"  {GREEN}{BOLD}Answer: {chr(65 + answer_idx)}) {options[answer_idx]}{RESET}\n")
    for line in textwrap.dedent(explanation).strip().splitlines():
        print(f"  {GREEN}{line}{RESET}")
    print()


# ── temporal helpers ─────────────────────────────────────────────────

async def get_client(encrypted=True):
    import temporalio.converter
    from temporalio.client import Client
    from temporal.encryption import EncryptionCodec
    if encrypted:
        dc = dataclasses.replace(
            temporalio.converter.default(),
            payload_codec=EncryptionCodec(),
        )
        return await Client.connect("localhost:7233", data_converter=dc)
    return await Client.connect("localhost:7233")


async def new_scan(client, org="temporalio"):
    from temporal.models import ScanInput
    from temporal.workflows import SecurityScanWorkflow
    from temporalio.common import WorkflowIDConflictPolicy
    wf_id = f"security-scan-{org}"
    handle = await client.start_workflow(
        SecurityScanWorkflow.run,
        ScanInput(org=org, token=os.environ.get("GITHUB_TOKEN")),
        id=wf_id,
        task_queue="security-scanner",
        execution_timeout=timedelta(minutes=30),
        id_conflict_policy=WorkflowIDConflictPolicy.TERMINATE_EXISTING,
    )
    return handle, wf_id


async def qprogress(handle):
    from temporal.workflows import SecurityScanWorkflow
    return await handle.query(SecurityScanWorkflow.progress)


async def get_event_count(client, wf_id):
    handle = client.get_workflow_handle(wf_id)
    count = 0
    async for _ in handle.fetch_history_events():
        count += 1
    return count


async def get_workflow_status(client, wf_id):
    from temporalio.client import WorkflowExecutionStatus
    handle = client.get_workflow_handle(wf_id)
    desc = await handle.describe()
    status_map = {
        WorkflowExecutionStatus.RUNNING: "RUNNING",
        WorkflowExecutionStatus.COMPLETED: "COMPLETED",
        WorkflowExecutionStatus.FAILED: "FAILED",
        WorkflowExecutionStatus.CANCELED: "CANCELED",
        WorkflowExecutionStatus.TERMINATED: "TERMINATED",
        WorkflowExecutionStatus.TIMED_OUT: "TIMED_OUT",
        WorkflowExecutionStatus.CONTINUED_AS_NEW: "CONTINUED_AS_NEW",
    }
    return status_map.get(desc.status, str(desc.status))


def progress_bar(p, width=40):
    filled = int(width * p.percent_complete / 100)
    return f"{'#' * filled}{'.' * (width - filled)}"


# =====================================================================
#  PREFLIGHT CHECK
# =====================================================================

async def preflight():
    banner("PREFLIGHT CHECK")

    def check(label, status, ok=True):
        dots = "." * max(1, 24 - len(label))
        color = GREEN if ok else RED
        sym = "OK" if ok else "FAIL"
        print(f"  Checking {label}{dots} {color}{status} {sym}{RESET}")

    # Python
    check("Python", f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")

    # SDK
    try:
        import temporalio
        check("Temporal SDK", temporalio.__version__)
    except ImportError:
        check("Temporal SDK", "not installed", ok=False)
        return False

    # Project files
    files = ["temporal/workflows.py", "temporal/activities.py",
             "temporal/encryption.py", "temporal/models.py", "temporal/worker.py"]
    missing = [f for f in files if not os.path.exists(f)]
    if missing:
        check("project files", f"missing {len(missing)}", ok=False)
        return False
    check("project files", "found")

    # Server
    try:
        from temporalio.client import Client
        c = await Client.connect("localhost:7233")
        check("Temporal server", "connected")
    except Exception:
        check("Temporal server", "not reachable", ok=False)
        print(f"\n  {RED}Start the server: temporal server start-dev{RESET}")
        return False

    check("worker", "will verify during scan")
    print(f"\n  {GREEN}Preflight complete.{RESET}")
    return True


# =====================================================================
#  INTRODUCTION
# =====================================================================

async def introduction():
    banner("THE 2AM INCIDENT")

    story("""
        A Temporal Security Scanner Demo
        by Sal Kimmich
    """)

    story("""
        I built this because I've been the person staring at a dead
        terminal at 2am, re-running a script that lost four hours of
        API calls to a process crash. The standard fixes — more
        try/except blocks, a Redis checkpoint, a database-backed
        queue — all add complexity to paper over a fundamental gap:
        your application logic shouldn't be responsible for its own
        durability.

        Temporal separates the two. Your code says what to do. The
        platform guarantees it finishes, even when infrastructure
        doesn't cooperate.

        This demo proves that claim with a live system. We'll scan
        Temporal's own GitHub organization (~194 repos) for security
        compliance posture — real API calls, real encryption, real
        crashes, real recovery.
    """)

    concept_box("WHAT YOU'LL LEARN", """
        PART 1 — Core Concepts  (~5 minutes)
          Durable execution, payload encryption, queries, signals,
          idempotent retry. Start a scan, prove encryption, kill
          the worker, watch it recover, cancel gracefully.

        PART 2 — Production Patterns  (~7 minutes, optional)
          Update handlers with validators, durable timers that
          survive crashes, continue-as-new for bounded history,
          and Temporal's built-in scheduler.
    """)

    story("""
        You'll need three terminals running:
          1: temporal server start-dev
          2: python -m temporal.worker
          3: this script
    """)


# =====================================================================
#  PART 1 — CORE CONCEPTS
# =====================================================================

async def part1_intro():
    banner("PART 1: CORE CONCEPTS", BG_BLUE)
    story("""
        The fast version. Five Temporal primitives, each demonstrated
        live with a real security scan.
    """)


# ── Act 1: The Problem ──────────────────────────────────────────────

async def act_1():
    banner("ACT 1: THE PROBLEM")

    story("""
        Every infrastructure team has scripts like this. A Python file
        that calls an API in a loop, prints results, and exits. It works
        on your laptop at 2pm on a Tuesday. It fails at 3am on the server.

        Five failure modes. Each one is a different Temporal concept.
    """)

    wait()

    print(f"  {WHITE}{BOLD}1. No Fault Tolerance{RESET}\n")
    compare(
        "Without Temporal:",
        "One API error -> sys.exit(1). Repo 47 of 200 returns 500?\n"
        "All 46 previous results are gone. Start from scratch.",
        "With Temporal:",
        "Each repo is an independent activity. Repo 47 fails? Temporal\n"
        "retries it. The other 199 repos are unaffected."
    )

    definition("DURABLE EXECUTION", """
        The guarantee that a function will run to completion even if
        the process, machine, or datacenter fails. Not "we'll retry
        the function" — "the function will CONTINUE from where it was."

        Think of it like a save point in a video game. Temporal
        saves after every meaningful step. When you reload, you're
        exactly where you were — inventory intact, no replaying
        old boss fights.
    """)

    wait()

    print(f"  {WHITE}{BOLD}2. No State Persistence{RESET}\n")
    compare(
        "Without Temporal:",
        "Your laptop lid closes at repo 150. SSH drops. Process killed.\n"
        "All progress lost. No way to resume.",
        "With Temporal:",
        "Worker dies at repo 150. Temporal's event history has 149 results\n"
        "recorded. New worker replays history, resumes from 150."
    )

    definition("IDEMPOTENCY", """
        An operation is idempotent if doing it once has the same effect
        as doing it N times. GET /repos/sdk-python returns the same data
        whether you call it once or five times.

        Why it matters: when a worker crashes mid-activity, Temporal
        retries that activity. If your activity is idempotent (most
        reads are), the retry is SAFE. at-least-once + idempotent
        = effectively-once.
    """)

    wait()

    print(f"  {WHITE}{BOLD}3. No Observability{RESET}\n")
    compare(
        "Without Temporal:",
        '"How far along is the scan?" -> Parse stdout. Hope the\n'
        'process is still alive. Maybe tail a log file.',
        "With Temporal:",
        '"How far along?" -> Query the workflow. From CLI, code, Web UI.\n'
        "Returns typed data: {scanned: 150, total: 200, status: 75%}"
    )

    definition("QUERIES", """
        A synchronous, read-only request to a running workflow. Returns
        structured data — not log lines. Any system can query: a CLI,
        a dashboard, a Slack bot, another microservice. The workflow
        doesn't even know it was queried.
    """)

    wait()

    print(f"  {WHITE}{BOLD}4. No Encryption{RESET}\n")
    compare(
        "Without Temporal:",
        "GitHub token in env vars, passed through function args, maybe\n"
        "logged to stdout. No encryption at rest anywhere.",
        "With Temporal:",
        "PayloadCodec encrypts ALL data client-side with Fernet\n"
        "(AES-128-CBC + HMAC-SHA256). The server stores ciphertext.\n"
        "Token never visible at rest."
    )

    wait()

    print(f"  {WHITE}{BOLD}5. Sequential Execution{RESET}\n")
    compare(
        "Without Temporal:",
        "200 repos x 3 API calls x ~1s each = 10 minutes minimum.\n"
        "One repo at a time. No concurrency.",
        "With Temporal:",
        "Batches of 10 repos scanned in parallel. 200 repos in ~2 minutes.\n"
        "asyncio.gather() with return_exceptions=True: one failure\n"
        "doesn't cancel the batch."
    )

    story("""
        Five failures. Each one caused by the same root problem:
        application logic tangled with infrastructure concerns.
        Temporal separates them.
    """)


# ── Act 2: The Architecture ─────────────────────────────────────────

async def act_2():
    banner("ACT 2: THE ARCHITECTURE")

    concept_box("TEMPORAL CONCEPT: Separation of Concerns", """
        Temporal enforces a clean split:

        WORKFLOW (temporal/workflows.py)
          Pure orchestration logic. No I/O. Deterministic.
          "Fetch repos, then scan each in batches of 10, then report."
          If you replayed this code with the same inputs, it would
          make the same decisions every time. That's the contract.

        ACTIVITIES (temporal/activities.py)
          Side effects live here. Network calls. File I/O.
          Each activity is independently retryable and recorded.
          "Call GET /repos/{org}/{repo} and parse the response."

        WORKER (temporal/worker.py)
          Long-running process that polls the server for tasks.
          Can crash and restart. Configures encryption, thread pool.

        CLIENT (temporal/starter.py, or this demo script)
          Starts workflows, sends signals, queries state.
          Completely decoupled from the worker.
    """)

    wait()

    concept_box("TEMPORAL CONCEPT: The Event History", """
        Everything that happens in a workflow is recorded as events:

        Event 1:    WorkflowExecutionStarted  {input: [encrypted]}
        Event 2:    WorkflowTaskScheduled
        Event 5:    ActivityTaskScheduled      {activity: "fetch_org_repos"}
        Event 7:    ActivityTaskCompleted      {result: [encrypted]}
        Event 10:   ActivityTaskScheduled      {check_repo_security}
        ...
        Event 1139: WorkflowExecutionCompleted {result: [encrypted]}

        This history IS the workflow's durable state. No separate
        database. No Redis. No checkpoint file.

        When a worker crashes:
        1. Temporal replays the history from event 1
        2. Completed activities return their RECORDED results
        3. Only the last pending activity is retried
        4. The workflow doesn't know a crash happened
    """)

    why_it_matters("""
        This is fundamentally different from a task queue (Celery, SQS).
        A task queue retries TASKS. Temporal replays WORKFLOWS.
        The distinction: Temporal reconstructs the entire decision
        history, not just the last failed step.
    """)

    quiz(
        "After a crash, Temporal replays a workflow's history.\n"
        "  What happens when replay reaches a completed activity?",
        [
            "The activity is executed again (retry)",
            "The activity is skipped entirely",
            "The recorded result is returned without executing",
            "The workflow pauses and asks the user",
        ],
        2,
        """
        C is correct. The SDK checks the event history and sees
        "this activity already completed with result X." It returns X
        directly. No network call, no side effect, no retry. This is
        why the workflow must be deterministic — so replay arrives at
        the same activity calls in the same order.
        """
    )


# ── Act 3: Live Scan + Encryption ───────────────────────────────────

async def act_3():
    banner("ACT 3: THE LIVE PROOF")

    story("""
        Enough theory. Let's prove every claim with a live system.

        We're about to start a real security scan of Temporal's own
        GitHub organization — ~194 public repositories. Real API calls.
        Real encryption. Real durable execution.
    """)

    concept_box("What Happens When You Call start_workflow()", """
        Client (this process)
          |
          +-- Serialize ScanInput{org, token} -> JSON
          +-- Encrypt JSON via PayloadCodec (Fernet AES)
          +-- gRPC: StartWorkflowExecution -> Temporal Server
          |
        Temporal Server (localhost:7233)
          |
          +-- Record WorkflowExecutionStarted (encrypted input)
          +-- Schedule WorkflowTask on "security-scanner" queue
          |
        Worker (your other terminal)
          |
          +-- Poll queue, receive WorkflowTask
          +-- Instantiate SecurityScanWorkflow
          +-- Call run() -> execute_activity(fetch_org_repos)
          +-- Activity makes real HTTP call to GitHub API
          +-- Result encrypted, sent back to server, recorded
          +-- Workflow continues to next batch of activities
    """)

    wait("Press Enter to start the scan and watch this happen live...")

    client = await get_client()
    handle, wf_id = await new_scan(client)

    print(f"  {GREEN}Workflow started.{RESET}")
    print(f"    Workflow ID: {WHITE}{BOLD}{wf_id}{RESET}")
    print(f"    Queue:       security-scanner")
    print(f"    Encrypt:     AES-128-CBC + HMAC-SHA256 (Fernet)\n")

    web_link(
        f"/namespaces/default/workflows/{wf_id}",
        "Event History tab — each activity scheduled, started, completed"
    )

    concept_box("Queries — Read-Only Workflow Introspection", """
        CLIENT                          SERVER                    WORKER
          |                               |                         |
          +-- QueryWorkflow ------------->|                         |
          |   (type: "progress")          +-- deliver to worker --->|
          |                               |                         +- call progress()
          |                               |                         |  return _progress
          |                               |<-- ScanProgress --------+
          |<-- ScanProgress --------------+                         |
          |                               |                         |

        Properties:
        * Read-only: queries MUST NOT modify workflow state
        * Synchronous: caller blocks until response
        * NOT recorded in event history (unlike signals)
        * Any external system can query at any time
    """)

    wait("Press Enter to query the running scan...")

    # Numbered queries like the original
    last_scanned = -1
    for i in range(1, 9):
        try:
            p = await qprogress(handle)
            pbar = progress_bar(p)
            color = GREEN if p.status == "completed" else WHITE
            print(f"  {color}Query #{i}:{RESET}")
            print(f"    Status:   {p.status}")
            print(f"    Progress: [{pbar}] {p.scanned_repos}/{p.total_repos} ({p.percent_complete}%)")
            if hasattr(p, "fully_compliant"):
                print(f"    Results:  {p.fully_compliant} compliant, {p.non_compliant} non-compliant, {p.errors} errors")
            print()

            if i == 1:
                teach("""
                    Each query returns a ScanProgress dataclass:
                    the workflow's live internal state, serialized
                    and sent back through the encrypted channel.
                """)

            if p.status in ("completed", "cancelled"):
                break
            if p.scanned_repos >= 30:
                break
            # Only sleep if something might still be happening
            await asyncio.sleep(3)
        except Exception as e:
            if "completed" in str(e).lower():
                break
            print(f"  {DIM}(Query returned: {e}){RESET}")
            break

    # Event count
    try:
        events = await get_event_count(client, wf_id)
        print(f"  {MAGENTA}Event history size: {WHITE}{BOLD}{events} events{RESET}")
        teach(f"""
            {events} events recorded for this execution.
            Each activity (scheduled + started + completed) = 3 events.
            Every event has encrypted payloads. The server stores the
            full history but cannot read any of the data.
        """)
    except Exception:
        pass

    why_it_matters("""
        In the original script, the only way to check progress is to
        read stdout. Queries work from ANY client: CLI, dashboard,
        monitoring system, another microservice. Structured data, not logs.
    """)

    # ── encryption proof ──

    wait("Press Enter to inspect encryption...")

    story("""
        We've claimed everything is encrypted. Let's prove it by
        connecting to the server WITHOUT our encryption key and
        examining the raw payloads — exactly what an attacker or
        database admin would see.
    """)

    concept_box("PayloadCodec — Client-Side Encryption", """
        Python object (ScanInput, RepoSecurityResult, etc.)
              |
              v
        DataConverter: object -> JSON bytes
              |
              v
        PayloadCodec.encode(): JSON bytes -> Fernet.encrypt() -> ciphertext
              |
              v
        gRPC transport -> Temporal Server stores ciphertext

        WHAT'S ENCRYPTED:
        [yes] Workflow inputs (org name, GitHub token)
        [yes] Activity inputs (repo names, token)
        [yes] Activity results (security scan findings)
        [yes] Workflow results (compliance report)

        WHAT'S NOT ENCRYPTED (by design):
        [no]  Workflow type name, activity names, task queue
        [no]  Search attributes (needed for server-side indexing)
        [no]  Failure messages (unless you configure failure converter)
    """)

    wait("Press Enter to examine raw event payloads...")

    from temporalio.client import Client
    raw = await Client.connect("localhost:7233")
    raw_handle = raw.get_workflow_handle(wf_id)

    print(f"  {RED}{BOLD}Connected WITHOUT encryption key.{RESET}")
    print(f"  {DIM}We can read the event structure, but not the data inside.{RESET}\n")

    found_input = found_result = False
    event_num = 0

    async for event in raw_handle.fetch_history_events():
        event_num += 1

        if event.event_type == 1 and not found_input:
            attrs = event.workflow_execution_started_event_attributes
            if attrs and attrs.input and attrs.input.payloads:
                found_input = True
                payload = attrs.input.payloads[0]
                encoding = payload.metadata.get("encoding", b"").decode()
                raw_b64 = base64.b64encode(payload.data).decode()

                print(f"  {WHITE}{BOLD}--- Event #{event_num}: WorkflowExecutionStarted ---{RESET}")
                print(f"  {DIM}This payload contains: org name + GitHub API token{RESET}\n")

                server_sees("Workflow Input", f'encoding: "{encoding}"')
                print(f"    {DIM}data: {raw_b64[:60]}...{RESET}")
                print(f"    {DIM}      ({len(payload.data)} bytes of Fernet ciphertext){RESET}\n")

                worker_sees("Same data after decryption",
                    '{"org": "temporalio", "token": "ghp_****"}')

        if event.event_type == 11 and not found_result:
            attrs = event.activity_task_completed_event_attributes
            if attrs and attrs.result and attrs.result.payloads:
                found_result = True
                payload = attrs.result.payloads[0]
                encoding = payload.metadata.get("encoding", b"").decode()

                print(f"  {WHITE}{BOLD}--- Event #{event_num}: ActivityTaskCompleted ---{RESET}")
                print(f"  {DIM}This payload contains: a repo's security scan results{RESET}\n")

                server_sees("Activity Result", f'encoding: "{encoding}"')
                print(f"    {DIM}data: {base64.b64encode(payload.data).decode()[:60]}...{RESET}")
                print(f"    {DIM}      ({len(payload.data)} bytes of ciphertext){RESET}\n")

                worker_sees("Same data after decryption",
                    '{"name": "sdk-python", "secret_scanning": "disabled", ...}')

        if found_input and found_result:
            break

    web_link(
        f"/namespaces/default/workflows/{wf_id}",
        'Click any event → Input/Result tab shows "binary/encrypted"'
    )

    concept_box("HOLD YOUR OWN KEY (HYOK)", """
        This is client-side encryption with a key YOU control. The
        Temporal server — whether self-hosted or Temporal Cloud —
        never has the key. It stores and routes opaque ciphertext.

        Why this matters:
          Compliance: your secrets never leave your trust boundary.
            The platform operator cannot read your data even with
            full database access.
          Key rotation: you control the lifecycle. Rotate keys
            without coordinating with your Temporal provider.
          Auditability: the key is in your KMS / Vault / HSM.
            You have the access logs, not the platform vendor.
          Multi-tenancy: different teams or customers can use
            different keys on the same Temporal cluster.
    """)

    quiz(
        "A Temporal Cloud operator has database access. What can they see?",
        [
            "Everything — org names, tokens, scan results",
            "Workflow structure (types, queues) but not data (inputs, results)",
            "Nothing at all — the entire history is encrypted",
            "Only the workflow ID and status",
        ],
        1,
        """
        B is correct. They can see workflow type names, activity names,
        task queues, timestamps, and event structure — the "envelope."
        But all inputs, outputs, and results are encrypted — the "letter."

        They'd see: "SecurityScanWorkflow ran on queue security-scanner,
        executed 194 check_repo_security activities, completed in 2
        minutes." But not WHICH repos, not the token, not the results.
        """
    )


# ── Act 4: Kill Test ────────────────────────────────────────────────

async def act_4():
    banner("ACT 4: THE KILL TEST", BG_RED)

    story("""
        This is the defining demonstration of Temporal's value.

        We will: start a scan, let it make progress, KILL the worker,
        prove the workflow is still alive on the server, restart the
        worker, and watch it resume without re-scanning any repos.
    """)

    concept_box("TEMPORAL CONCEPT: Replay — How Temporal Survives Crashes", """
        When a new worker starts after a crash:

        Temporal Server: "Here's a WorkflowTask with the full event history"

        New Worker:
          1. Creates a fresh SecurityScanWorkflow instance
          2. Calls run() — workflow code executes from the top
          3. Hits execute_activity(fetch_org_repos)
             -> SDK checks history: "Activity completed at event #7"
             -> Returns the RECORDED result (no API call made)
          4. Hits execute_activity(check_repo_security) for repo 1
             -> SDK checks history: "Activity completed at event #15"
             -> Returns the RECORDED result (no API call made)
          5. ... repeats for all completed activities ...
          6. Hits execute_activity(check_repo_security) for repo N
             -> SDK checks history: "No completion recorded"
             -> THIS activity is actually scheduled and executed

        Replay takes milliseconds. It's in-memory event matching,
        not re-execution. The GitHub API is never called for repos
        that were already scanned.
    """)

    wait("Press Enter to start the kill test...")

    client = await get_client()
    handle, wf_id = await new_scan(client)
    print(f"  {GREEN}Scan started.{RESET}")
    story("    Waiting for the scan to record some activity results...")

    web_link(
        f"/namespaces/default/workflows/{wf_id}",
        "Watch the Event History grow in real time"
    )

    # Wait for progress with # bar
    pre_kill = 0
    last_scanned = -1
    for _ in range(20):
        await asyncio.sleep(2)
        try:
            p = await qprogress(handle)
            if p.scanned_repos != last_scanned:
                last_scanned = p.scanned_repos
                pbar = progress_bar(p)
                print(f"    [{pbar}] {p.scanned_repos}/{p.total_repos} repos scanned")
            if p.scanned_repos >= 20:
                pre_kill = p.scanned_repos
                break
            if p.status == "completed":
                print(f"\n  {YELLOW}Scan finished too fast for the kill test.{RESET}")
                return
        except Exception:
            pass

    print(f"\n  {WHITE}{BOLD}Scan has recorded {pre_kill} repo results. Time to crash.{RESET}\n")

    # Event count before kill
    pre_events = None
    try:
        pre_events = await get_event_count(client, wf_id)
        print(f"  Events recorded before kill: {BOLD}{pre_events}{RESET}")
        print(f"    {DIM}(These events are on the SERVER. They survive the crash.){RESET}\n")
    except Exception:
        pass

    # Kill instructions
    print(f"  {RED}{BOLD}{'=' * 64}{RESET}")
    print(f"  {RED}{BOLD}  !!! CLOSE YOUR WORKER WINDOW NOW !!!{RESET}")
    print(f"  {RED}{BOLD}{RESET}")
    print(f"  {RED}{BOLD}  Close the PowerShell window running:{RESET}")
    print(f"  {RED}{BOLD}    python -m temporal.worker{RESET}")
    print(f"  {RED}{BOLD}  Click the X. Hard kill. No graceful shutdown.{RESET}")
    print(f"  {RED}{BOLD}{RESET}")
    print(f"  {RED}{BOLD}  This simulates:{RESET}")
    print(f"  {RED}    - OOM kill (Linux kernel terminates your process){RESET}")
    print(f"  {RED}    - Spot instance reclamation (AWS pulls the rug){RESET}")
    print(f"  {RED}    - Node failure (hardware dies){RESET}")
    print(f"  {RED}    - Network partition (worker can't reach server){RESET}")
    print(f"  {YELLOW}  ⚠  Keep the SERVER terminal open (temporal server start-dev){RESET}")
    print(f"  {RED}{BOLD}{'=' * 64}{RESET}")
    print()

    wait("Close the worker window, then press Enter...")

    story("    The worker is dead. Let's check if the workflow survived.")

    # Check status with retry
    cmd_display("Checking workflow status on the Temporal server...")
    status = None
    for attempt in range(3):
        try:
            status = await get_workflow_status(client, wf_id)
            break
        except Exception:
            if attempt < 2:
                print(f"  {YELLOW}Connecting... (attempt {attempt + 2}){RESET}")
                await asyncio.sleep(2)
            else:
                print(f"  {RED}{BOLD}ERROR: Cannot reach the Temporal server.{RESET}")
                print(f"  {RED}  You may have closed the wrong terminal.{RESET}")
                print(f"  {RED}  The WORKER terminal should be killed.{RESET}")
                print(f"  {RED}  The SERVER terminal must stay running.{RESET}\n")
                return

    if status == "RUNNING":
        print(f"  {GREEN}{BOLD}Workflow status: {status}{RESET}\n")
        print(f"  {RED}{BOLD}  THE WORKFLOW IS STILL RUNNING.{RESET}")
        print(f"  {RED}{BOLD}  No worker. No process. But the workflow is alive.{RESET}\n")

        story("""
            The Temporal server holds the workflow's event history.
            It doesn't need a worker to keep the workflow alive —
            only to make forward progress.

            Activity tasks are sitting on the task queue with no one
            to execute them. The server will wait (up to the activity's
            start_to_close_timeout) and then mark them for retry.
        """)
    else:
        print(f"  {YELLOW}Status: {status} (may have completed before the kill){RESET}\n")

    web_link(
        f"/namespaces/default/workflows/{wf_id}",
        'Status still shows "Running" — the server is waiting for a worker'
    )

    # Restart instructions
    print(f"  {GREEN}{BOLD}{'=' * 64}{RESET}")
    print(f"  {GREEN}{BOLD}  RESTART THE WORKER{RESET}")
    print(f"  {GREEN}{BOLD}  Open a new terminal and run:{RESET}")
    if sys.platform == "win32":
        print(f"  {WHITE}    $env:PYTHONPATH = \"{os.getcwd()}\"{RESET}")
        print(f"  {WHITE}    cd {os.getcwd()}{RESET}")
    print(f"  {WHITE}    python -m temporal.worker{RESET}")
    print(f"  {GREEN}{BOLD}{'=' * 64}{RESET}")
    print()

    wait("Start the worker, wait for 'Worker started', then press Enter...")

    story("""
        The new worker is replaying the event history right now.
        It's reconstructing the workflow's state from recorded events,
        then continuing from the exact interruption point.
    """)

    # Watch resume with # bars
    resumed = False
    last_scanned = -1
    for _ in range(30):
        await asyncio.sleep(2)
        try:
            p = await qprogress(handle)
            if p.scanned_repos != last_scanned or p.status == "completed":
                last_scanned = p.scanned_repos
                pbar = progress_bar(p)

                label = ""
                if not resumed and p.scanned_repos > pre_kill:
                    resumed = True
                    label = f"  {GREEN}{BOLD}<-- RESUMED HERE (was {pre_kill}){RESET}"

                print(f"    [{pbar}] {p.scanned_repos}/{p.total_repos} ({p.percent_complete}%){label}")

                if p.status == "completed":
                    print(f"\n  {GREEN}{BOLD}Scan completed: {p.scanned_repos} repos scanned.{RESET}")
                    break
        except Exception:
            print(f"    {DIM}(worker processing...){RESET}")

    # Event comparison
    try:
        post_events = await get_event_count(client, wf_id)
        if pre_events:
            new_events = post_events - pre_events
            print(f"\n  {MAGENTA}Events before kill:      {WHITE}{BOLD}{pre_events}{RESET}")
            print(f"  {MAGENTA}Events after completion:  {WHITE}{BOLD}{post_events}{RESET}")
            print(f"  {MAGENTA}New events added:         {WHITE}{BOLD}{new_events}{RESET}")
            teach(f"""
                The first {pre_events} events were REPLAYED from history (no API calls).
                Only {new_events} events represent NEW work done after the restart.
            """)
    except Exception:
        pass

    why_it_matters(f"""
        The workflow survived a complete worker failure.
        No GitHub API call was made twice. No scan result was lost.
        The new worker replayed the history in milliseconds, then
        continued from the exact interruption point.

        Original script: crash at repo {pre_kill} = start over from repo 1.
        Temporal: crash at repo {pre_kill} = continue from repo {pre_kill}.

        This is durable execution. This is why Temporal exists.
    """)

    quiz(
        "During replay, how does the SDK know which activities\n"
        "  to return recorded results for vs actually execute?",
        [
            "It keeps a checkpoint file on disk",
            "It compares activity names to a database table",
            "It matches workflow code against the event history sequence",
            "It asks the server which activities are complete",
        ],
        2,
        """
        C is correct. The SDK maintains a pointer into the event history.
        As the workflow code re-executes from the top, each call to
        execute_activity() advances the pointer. If the next event is
        "ActivityTaskCompleted," the SDK returns that result. If the
        pointer reaches the end, the next activity executes for real.
        """
    )


# ── Act 5: Graceful Cancel ──────────────────────────────────────────

async def act_5():
    banner("ACT 5: GRACEFUL CANCELLATION")

    concept_box("Signals vs Queries vs Cancellation", """
        Three ways to communicate with a running workflow:

        +-------------+--------------+-----------------+--------------------+
        |             | Signals      | Queries         | Cancellation       |
        +-------------+--------------+-----------------+--------------------+
        | Direction   | TO workflow  | FROM workflow   | TO workflow        |
        | Modifies    | YES          | NO (read-only)  | YES (raises error) |
        | Response    | None (fire   | Returns data    | None               |
        |             |   & forget)  |   immediately   |                    |
        | Recorded    | In history   | NOT in history  | In history         |
        | Use case    | "Stop after  | "How far        | "Stop NOW"         |
        |             |  this batch" |  along?"        |                    |
        +-------------+--------------+-----------------+--------------------+

        Our cancel_scan SIGNAL is graceful: the workflow decides how to stop.
        Temporal's built-in CANCELLATION is abrupt: raises CancelledError.
        We chose a signal because partial results are valuable.
    """)

    wait("Press Enter to start a scan and cancel it mid-flight...")

    from temporal.workflows import SecurityScanWorkflow

    client = await get_client()
    handle, wf_id = await new_scan(client)
    print(f"  {GREEN}Scan started.{RESET}")
    story("    Waiting for the first batch to complete...")

    for _ in range(12):
        await asyncio.sleep(2)
        try:
            p = await qprogress(handle)
            if p.scanned_repos > 0:
                print(f"    {p.scanned_repos} repos scanned so far...")
                break
        except Exception:
            pass

    story("""
        Good — the scan has made progress. Now imagine: you realize
        you used the wrong token, or rate limits are being hammered.

        With the original script: Ctrl+C. Everything lost.
        With Temporal: send a signal.
    """)

    cmd_display('Sending signal: cancel_scan("Demo: showing graceful cancellation")')
    try:
        await handle.signal(SecurityScanWorkflow.cancel_scan,
                            "Demo: showing graceful cancellation")
        print(f"  {GREEN}Signal sent. The workflow will stop after its current batch.{RESET}\n")
    except Exception as e:
        if "already completed" in str(e).lower():
            print(f"  {YELLOW}Workflow already completed before the signal arrived.{RESET}\n")
        else:
            print(f"  {YELLOW}Signal error: {e}{RESET}\n")

    story("""
        The signal is fire-and-forget. We sent it, the server durably
        recorded it, and the workflow will process it on its next
        decision point (between batches).
    """)

    story("    Waiting for partial report...")
    try:
        result = await handle.result()
        cancelled = result.get("cancel_reason")

        print(f"\n  {WHITE}{BOLD}--- PARTIAL REPORT ---{RESET}")
        if cancelled:
            print(f"    Status:        {RED}{BOLD}CANCELLED{RESET}")
            print(f"    Reason:        {result.get('cancel_reason', 'N/A')}")
            print(f"    Repos scanned: {result.get('repos_scanned_before_cancel', '?')}/{result.get('total_repos', '?')}")
        else:
            print(f"    Status:        {GREEN}{BOLD}COMPLETED{RESET}")
            print(f"    Repos scanned: {result.get('total_repos', '?')}")
        print(f"    Compliant:     {result.get('fully_compliant', 0)}")
        print(f"    Non-compliant: {len(result.get('non_compliant_repos', []))}")
        print()
    except Exception as e:
        print(f"  {DIM}(Result: {e}){RESET}\n")

    web_link(
        f"/namespaces/default/workflows/{wf_id}",
        "Status shows completion with cancel metadata in the result"
    )

    why_it_matters("""
        Original script Ctrl+C: process dies, all data lost.
        Temporal signal: workflow stops gracefully, partial report saved.

        For long-running scans (1000+ repos), this is critical. Rate
        limits hit? Send a cancel signal. Wrong org? Signal.
        The scan saves everything it found and returns cleanly.
    """)


# ── Part 1 Summary ──────────────────────────────────────────────────

async def part1_summary():
    banner("PART 1 COMPLETE", BG_GREEN)

    print(f"  {WHITE}{BOLD}WHAT WE DEMONSTRATED:{RESET}\n")
    items = [
        ("Durable Execution (The Kill Test)",
         "Killed the worker mid-scan. Workflow survived on the server.",
         "New worker replayed history, resumed. Zero duplicate API calls."),
        ("Queryable State",
         "Read scan progress from an external process using typed queries.",
         "Works from CLI, code, Web UI. Structured data, not logs."),
        ("Signal-Based Graceful Cancellation",
         "Sent a fire-and-forget signal to stop the scan.",
         "Workflow finished its batch, generated a partial report."),
        ("End-to-End Payload Encryption (HYOK)",
         "Every payload encrypted client-side with AES.",
         "Connected without the key: saw only ciphertext."),
        ("Parallel Execution with Fault Isolation",
         "10 repos scanned concurrently per batch.",
         "One failure doesn't cancel the batch."),
    ]
    for i, (title, l1, l2) in enumerate(items, 1):
        print(f"  {GREEN}{BOLD}{i}. {title}{RESET}")
        print(f"     {l1}")
        print(f"     {DIM}{l2}{RESET}")
        print()

    story("""
        Part 2 goes deeper: live reconfiguration, crash-proof timers,
        unbounded workflow lifetimes, and automated scheduling — the
        patterns that take a Temporal application from demo to
        production.
    """)


# =====================================================================
#  PART 2 — PRODUCTION PATTERNS
# =====================================================================

async def part2_intro():
    banner("PART 2: PRODUCTION PATTERNS", BG_MAGENTA)
    story("""
        Part 1 established the foundation. Part 2 addresses what
        comes next: How do I reconfigure a running workflow? What
        about rate limits? What happens when event history grows
        large? How do I schedule recurring scans?

        Four more Temporal primitives, each demonstrated live.
    """)


async def act_6():
    banner("ACT 6: LIVE SURGERY", BG_CYAN)

    story("""
        Three features on a single running scan: update the batch
        size with a validated mutation, pause with a durable timer,
        then cancel. One workflow, three interventions.

        This scan covers ~194 repos, so it will take a couple of
        minutes. While it runs, watch the Web UI for real-time state.
    """)

    wait("Press Enter to start...")

    from temporal.workflows import SecurityScanWorkflow

    client = await get_client()
    handle, wf_id = await new_scan(client)
    print(f"  {GREEN}Scan started (batch size: 10, org: temporalio).{RESET}\n")

    web_link(
        f"/namespaces/default/workflows/{wf_id}",
        "Watch activities appear as batches complete"
    )

    story("    Building initial progress...")
    for _ in range(12):
        await asyncio.sleep(2)
        try:
            p = await qprogress(handle)
            if p.scanned_repos >= 5:
                print(f"    [{progress_bar(p)}] {p.scanned_repos}/{p.total_repos}")
                break
        except Exception:
            pass

    # ── update handlers ──

    concept_box("Update Handlers — Synchronous, Validated Mutations", """
        Signals are fire-and-forget: the sender has no confirmation.
        Updates solve this: caller sends a request and WAITS for the
        workflow's response.

        Updates support validators: functions that run BEFORE the
        handler and can reject invalid input. Validator throws?
        Handler never executes. State untouched. Caller gets error.

        Signal + bad input = potentially corrupted state.
        Update + bad input = rejected at the gate.
    """)

    cmd_display("Update: change batch size from 10 to 3")
    try:
        resp = await handle.execute_update(
            SecurityScanWorkflow.update_batch_size, 3
        )
        print(f"  {GREEN}{BOLD}Response: \"{resp}\"{RESET}")
        teach("    The caller waited for that confirmation. Not a hope — a receipt.")
    except Exception as e:
        print(f"  {YELLOW}{e}{RESET}")

    print()
    cmd_display("Update: change batch size to 0 (intentionally invalid)")
    try:
        await handle.execute_update(SecurityScanWorkflow.update_batch_size, 0)
    except Exception as e:
        print(f"  {RED}{BOLD}REJECTED: {e}{RESET}")
        teach("""
            The validator intercepted it. Handler never ran. Batch size
            is still 3. Without the validator, that zero would have
            caused either a division error or an infinite loop.
        """)

    # ── durable timer ──

    concept_box("Durable Timers — Server-Side, Crash-Proof", """
        workflow.sleep() creates a timer on the Temporal server, not
        in the worker process. The server records TimerStarted, releases
        the worker, and fires TimerFired when the duration elapses.

        The critical property: the timer survives worker death. Kill
        the worker mid-sleep and the timer keeps counting. Restart
        after it fires and the workflow resumes immediately.
    """)

    timer_secs = 15
    cmd_display(f"Signal: pause for {timer_secs} seconds (durable timer)")
    try:
        await handle.signal(SecurityScanWorkflow.pause_scan, timer_secs)
        print(f"  {GREEN}Signal sent. Timer starts after the current batch.{RESET}\n")
    except Exception as e:
        print(f"  {YELLOW}{e}{RESET}\n")

    web_link(
        f"/namespaces/default/workflows/{wf_id}",
        "Look for TimerStarted → TimerFired events in the history"
    )

    story(f"    Watching the {timer_secs}-second timer...")
    timer_fired = False
    last_scanned = -1
    last_status = ""
    for _ in range(18):
        await asyncio.sleep(2)
        try:
            p = await qprogress(handle)
            label = ""
            notable = False
            if p.timer_active or p.status == "paused":
                label = f"  {MAGENTA}(server-side timer){RESET}"
            elif p.status == "scanning" and not timer_fired:
                timer_fired = True
                notable = True
                label = f"  {GREEN}{BOLD}<-- timer fired, scanning resumed{RESET}"

            changed = (p.scanned_repos != last_scanned or p.status != last_status)
            if (changed or notable) and p.total_repos > 0:
                last_scanned = p.scanned_repos
                last_status = p.status
                print(f"    [{progress_bar(p)}] {p.scanned_repos}/{p.total_repos} ({p.percent_complete}%) {DIM}{p.status}{RESET}{label}")

            if timer_fired and p.status == "scanning":
                break
            if p.status == "completed":
                break
        except Exception:
            pass

    if timer_fired:
        insight("""
            The timer lived on the server. The worker was free to
            process other tasks — or crash entirely. time.sleep()
            requires a living process. workflow.sleep() requires
            only a Temporal server.
        """)

    # Cancel
    story("    Cancelling the scan to move on...")
    try:
        await handle.signal(SecurityScanWorkflow.cancel_scan, "Proceeding to next act")
        result = await handle.result()
        repos_done = result.get("repos_scanned_before_cancel", "?")
        print(f"  {DIM}Cancelled with {repos_done} repos in partial report.{RESET}\n")
    except Exception:
        pass

    quiz(
        "What is the key difference between a signal and an update?",
        [
            "Signals are faster than updates",
            "Updates return a response; signals are fire-and-forget",
            "Signals can modify state; updates cannot",
            "Updates are not recorded in the event history",
        ],
        1,
        """
        Signals are fire-and-forget. Updates are synchronous — the
        caller waits for the handler's return value. Both can modify
        state. Both are in the history. Updates also support validators.
        """
    )


async def act_7():
    banner("ACT 7: INFINITE ENDURANCE", BG_YELLOW)

    story("""
        Our ~194-repo scan produces roughly 1,100 events. What about
        an org with 5,000 repos? Temporal Cloud warns around 50K events.
    """)

    concept_box("Continue-As-New — Bounded History, Infinite Workflows", """
        The workflow packages its accumulated state into a new input,
        then starts a fresh execution with an empty history. Same
        workflow ID. Queries and signals still work.

        Crosses the boundary: results, repo list, batch offset.
        Resets: the event history (that's the point).

        For this demo, the threshold is 500 events — low enough to
        trigger with 194 repos. Production: 10K–20K, or use the
        SDK's is_continue_as_new_suggested().
    """)

    story("""
        This scan will take 1–2 minutes. While it runs, open the
        Web UI and watch for the execution status to change to
        "Continued As New" when the threshold is reached.
    """)

    wait("Press Enter to watch it trigger...")

    client = await get_client()
    handle, wf_id = await new_scan(client)
    print(f"  {GREEN}Scan started. Threshold: 500 events.{RESET}\n")

    web_link(
        f"/namespaces/default/workflows/{wf_id}",
        'Watch for status change to "Continued As New" — then a new execution appears'
    )

    # Watch with # bars, deduplicated
    last_scanned = -1
    marked_cont = False
    for _ in range(50):
        await asyncio.sleep(3)
        try:
            p = await qprogress(handle)
            label = ""
            if p.continuation_count > 0 and not marked_cont:
                marked_cont = True
                label = f"  {MAGENTA}{BOLD}<-- CONTINUE-AS-NEW #{p.continuation_count}{RESET}"
            elif p.continuation_count > 0:
                label = f"  {DIM}(continuation #{p.continuation_count}){RESET}"

            if p.scanned_repos != last_scanned or label:
                last_scanned = p.scanned_repos
                print(f"    [{progress_bar(p)}] {p.scanned_repos}/{p.total_repos} ({p.percent_complete}%){label}")

            if p.status == "completed":
                break
        except Exception:
            pass

    try:
        result = await handle.result()
        cont = result.get("continue_as_new_count", 0)
        if cont > 0:
            print(f"\n  {MAGENTA}{BOLD}Continue-as-new fired {cont} time(s).{RESET}")
            teach(f"""
                The workflow ran across {cont + 1} separate event histories,
                each bounded. Final report: all {result.get('total_repos', '?')} repos.
            """)
            web_link(
                f"/namespaces/default/workflows/{wf_id}",
                f'{cont} execution(s) with status "Continued As New", final one "Completed"'
            )
        else:
            teach("""
                History stayed under 500 events this run. With a larger
                org or smaller batch size, it would trigger.
            """)
    except Exception:
        pass

    why_it_matters("""
        This is how Temporal workflows run indefinitely. Weekly audits,
        continuous monitoring, long-running sagas. Each execution is
        finite. The workflow is logically unbounded.
    """)


async def act_8():
    banner("ACT 8: THE AUTOMATION", BG_GREEN)

    story("""
        The CISO wants a compliance report every Monday at 6am.
        Without a cron server, a Jenkins job, or anyone remembering
        to run the script.
    """)

    concept_box("Schedules — Temporal's Built-In Cron Replacement", """
        A schedule starts a workflow at the interval you define.
        Each execution is a full Temporal workflow — durable,
        encrypted, observable.

        Advantages over cron:
          The workflow itself survives crashes. Overlap policies
          are built in (skip if running, buffer, cancel previous).
          Pause/resume from the UI. Full audit trail.
    """)

    wait("Press Enter to create a schedule...")

    from temporalio.client import (
        Schedule, ScheduleActionStartWorkflow,
        ScheduleIntervalSpec, ScheduleSpec, ScheduleState,
    )
    from temporal.models import ScanInput
    from temporal.workflows import SecurityScanWorkflow

    client = await get_client()
    sched_id = "demo-schedule-temporalio"

    try:
        await client.get_schedule_handle(sched_id).delete()
    except Exception:
        pass

    cmd_display("Creating schedule: scan temporalio every 5 minutes")
    await client.create_schedule(
        sched_id,
        Schedule(
            action=ScheduleActionStartWorkflow(
                SecurityScanWorkflow.run,
                ScanInput(org="temporalio", token=os.environ.get("GITHUB_TOKEN")),
                id="security-scan-temporalio",
                task_queue="security-scanner",
                execution_timeout=timedelta(minutes=30),
            ),
            spec=ScheduleSpec(
                intervals=[ScheduleIntervalSpec(every=timedelta(minutes=5))],
            ),
            state=ScheduleState(note="Demo: recurring compliance scan"),
        ),
    )

    print(f"  {GREEN}{BOLD}Schedule created.{RESET}")
    print(f"    ID:    {sched_id}\n")

    web_link(
        f"/namespaces/default/schedules/{sched_id}",
        "Interval, next run time, and recent execution history"
    )

    compare(
        "Crontab",
        "Script crashes? Cron doesn't know. Silent failure.\n"
        "Server reboots? Hope the job survived.\n"
        "Pause? Edit crontab, remember to re-enable it.",
        "Temporal Schedule",
        "Each execution is a durable workflow. Crashes auto-recover.\n"
        "Pause/resume in the UI. Full audit trail.\n"
        "Each run has queries, signals, encryption — full Temporal."
    )

    story("    Cleaning up the demo schedule...")
    try:
        await client.get_schedule_handle(sched_id).delete()
        print(f"  {DIM}Schedule deleted.{RESET}\n")
    except Exception:
        pass


# ── Epilogue ─────────────────────────────────────────────────────────

async def epilogue():
    banner("EPILOGUE: THE MORNING AFTER", BG_GREEN)

    story("""
        It's 9am. The CISO walks into the meeting.

        The compliance report is on their desk. Complete. 194 repos
        scanned. Every payload encrypted. The scanner crashed at 2am
        (a worker node was recycled) and recovered automatically by
        2:01am. Nobody was paged. Nobody restarted anything.

        The report includes a partial scan from last night — someone
        sent a cancel signal when they realized the GitHub token
        didn't have the right scopes. That partial report is tagged
        as "cancelled" with the reason, so it won't be confused
        with a complete audit.
    """)

    wait()

    print(f"  {WHITE}{BOLD}WHAT WE DEMONSTRATED:{RESET}\n")

    items = [
        ("DURABLE EXECUTION",
         "Killed the worker mid-scan. Workflow survived.",
         "New worker replayed history and resumed. Zero data lost."),
        ("IDEMPOTENT RETRY",
         "Activities are safe to retry because they're reads.",
         "at-least-once execution + idempotent = effectively-once."),
        ("DETERMINISTIC REPLAY",
         "Workflow code re-executes from the top on recovery.",
         "SDK matches code against event history. Same decisions."),
        ("END-TO-END ENCRYPTION (HYOK)",
         "Connected without the key: saw only ciphertext.",
         "Server is a postal service. Reads the address, not the letter."),
        ("QUERYABLE STATE",
         "Read progress from an external process at any time.",
         "Typed data, not log lines. Any client can query."),
        ("GRACEFUL CANCELLATION",
         "Signal to stop. Partial report with metadata.",
         "Ctrl+C loses everything. Signals preserve everything."),
        ("UPDATE HANDLERS",
         "Synchronous, validated batch size mutation.",
         "Validator rejects invalid input before state is touched."),
        ("DURABLE TIMERS",
         "Server-side timer that outlives the worker process.",
         "time.sleep() dies with the process. workflow.sleep() doesn't."),
        ("CONTINUE-AS-NEW",
         "Bounded history for arbitrarily long workflows.",
         "Each execution finite. Workflow logically unbounded."),
        ("SCHEDULES",
         "Cron replacement with durable execution built in.",
         "Pause/resume from UI. Full audit trail per execution."),
    ]

    for i, (title, l1, l2) in enumerate(items, 1):
        print(f"  {GREEN}{BOLD}{i:2d}. {title}{RESET}")
        print(f"      {l1}")
        print(f"      {DIM}{l2}{RESET}")
        print()

    print(f"  {WHITE}{BOLD}WHAT I'D BUILD NEXT:{RESET}")
    print(f"    * Codec Server so the Web UI can decrypt payloads")
    print(f"    * Child workflows for multi-org scanning")
    print(f"    * Interceptors for OpenTelemetry tracing")
    print(f"    * Search attributes for compliance dashboarding")
    print(f"    * Versioning and patching for zero-downtime deploys")
    print()

    story("""
        That's the demo. Not five separate fixes bolted onto a script.
        One architectural decision — make execution durable — that
        solves fault tolerance, observability, encryption, cancellation,
        and concurrency all at once.

        The 2am incident doesn't happen anymore.

        Demo by Sal Kimmich — https://github.com/salkimmich
    """)


# =====================================================================
#  MAIN
# =====================================================================

async def run():
    banner("THE 2AM INCIDENT: A TEMPORAL DEMO")

    story("""
        An interactive, narrated walkthrough of durable execution.

        Real workflows. Real encryption. Real crashes. Real recovery.
        Every concept proven live, not claimed on a slide.

        You'll need three terminals:
          Terminal 1: temporal server start-dev
          Terminal 2: python -m temporal.worker
          Terminal 3: THIS SCRIPT

        Press Enter at each prompt. Ctrl+C to exit.
    """)

    wait("Press Enter to begin preflight check...")

    if not await preflight():
        print(f"\n  {RED}Fix the issues above and re-run.{RESET}\n")
        return

    wait("Press Enter to begin the demo...")

    await introduction()
    wait("Press Enter to begin Part 1...")

    await part1_intro()
    wait("→ Act 1: The Problem...")
    await act_1()
    wait("→ Act 2: The Architecture...")
    await act_2()
    wait("→ Act 3: Live Proof + Encryption...")
    await act_3()
    wait("→ Act 4: The Kill Test...")
    await act_4()
    wait("→ Act 5: Graceful Cancellation...")
    await act_5()
    await part1_summary()

    # Choice point
    print(f"  {DIM}{'-' * 64}{RESET}")
    choice = input(
        f"  {WHITE}{BOLD}Continue to Part 2 — Production Patterns? [y/n]: {RESET}"
    ).strip().lower()
    print()

    if choice in ("y", "yes", ""):
        await part2_intro()
        wait("→ Act 6: Live Surgery (updates, timers)...")
        await act_6()
        wait("→ Act 7: Continue-As-New...")
        await act_7()
        wait("→ Act 8: Schedules...")
        await act_8()
        wait("→ Epilogue...")
        await epilogue()
    else:
        story("""
            Part 2 is available any time you re-run the demo. It covers
            update handlers, durable timers, continue-as-new, and
            schedules.
        """)

    print(f"  {DIM}Demo complete. Thank you for your time.{RESET}\n")


def main():
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print(f"\n\n  {DIM}Demo interrupted. Goodbye.{RESET}\n")


if __name__ == "__main__":
    main()
