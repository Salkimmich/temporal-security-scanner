#!/usr/bin/env python3
"""
The 2am Incident — A Temporal Security Scanner Demo
=====================================================

One workflow, many jurisdictions. Built by Sal Kimmich to demonstrate
that we run the same security scan for teams in multiple countries —
EU data in the EU, US in the US — with durability (the 2am crash),
encryption (platform never sees secrets), and sovereignty (data stays
in-region). One codebase, same guarantees, anywhere.

This demo has two parts:

    PART 1 — CORE CONCEPTS  (~6 minutes)
    Deep dives on encryption and sovereignty, then a real security scan
    of Temporal's GitHub org, kill the worker mid-scan, watch it recover,
    and cancel gracefully.

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

    teach("""
        If you're responsible for confidentiality and data residency —
        CISO, compliance, architect in a regulated or multi-region org —
        this demo is built so you leave knowing exactly what you can
        have confidence in for encryption and sovereignty, and why.
    """)

    teach("""
        ONE WORKFLOW, MANY JURISDICTIONS — same code, same guarantees,
        in any region. That's the theme. We'll show evidence of it
        throughout and end with a clear summary of your protections.
    """)

    story("""
        We run the same security scan for teams in multiple countries.
        EU data must stay in the EU. US data in the US. One workflow
        definition, one codebase — but we have to meet local confidentiality
        and residency requirements everywhere.

        That's the story: one workflow, many jurisdictions. Durability
        matters (the 2am crash, the worker that died — Temporal fixes that).
        But so do two more things: the platform must never see our secrets
        (encryption), and the platform must never hold our data outside
        the region we chose (sovereignty). Get those right, and we can
        ship one app and run it in any region with the same guarantees.

        This demo proves it with a live system. We'll scan Temporal's
        own GitHub organization (~194 repos) — real API calls, real
        encryption, real crashes, real recovery — and we'll go deep on
        how encryption and sovereignty architecture let you run the same
        workflow safely in many regions.
    """)

    concept_box("WHAT YOU'LL LEARN", """
        PART 1 — Core Concepts (one workflow, many jurisdictions)
          Act 3: Encryption — so the platform never sees plaintext;
          detailed understanding (what's encrypted, threat model, AE).
          Act 4: Sovereignty — so data stays in the region you chose;
          namespace-per-region, workers in-region, Cloud vs self-hosted.
          Then: live scan, kill the worker, recover, cancel gracefully.

        PART 2 — Production Patterns  (~7 minutes, optional)
          Update handlers, durable timers, continue-as-new,
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
        One workflow, many jurisdictions. We need durability (so the scan
        survives crashes), encryption (so the platform never sees our
        tokens or findings), and sovereignty (so we can run this in EU
        or US or APAC and keep data in-region). Five Temporal primitives
        plus two deep dives — each demonstrated with a real security scan.
    """)


# ── Act 1: The Problem ──────────────────────────────────────────────

async def act_1():
    banner("ACT 1: THE PROBLEM")

    story("""
        Every infrastructure team has scripts like this. A Python file
        that calls an API in a loop, prints results, and exits. It works
        on your laptop at 2pm on a Tuesday. It fails at 3am on the server.

        When you run the same workflow in multiple regions, you also need
        the platform to never see your secrets (encryption) and never
        hold your data outside the region you chose (sovereignty). So
        we'll fix five failure modes — each a Temporal concept — and then
        go deep on encryption and sovereignty so one codebase can run
        everywhere with the same guarantees.
    """)

    wait()

    print(f"  {WHITE}{BOLD}1. No Fault Tolerance (general best practice){RESET}\n")
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

    print(f"  {WHITE}{BOLD}2. No State Persistence (general best practice){RESET}\n")
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

    print(f"  {WHITE}{BOLD}3. No Observability (general best practice){RESET}\n")
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

    print(f"  {WHITE}{BOLD}4. No Encryption (core to our story){RESET}\n")
    compare(
        "Without Temporal:",
        "GitHub token in env vars, passed through function args, maybe\n"
        "logged to stdout. No encryption at rest anywhere.",
        "With Temporal:",
        "PayloadCodec encrypts ALL data client-side with Fernet.\n"
        "The server stores ciphertext. For one workflow, many jurisdictions:\n"
        "the platform in each region never sees plaintext."
    )

    wait()

    print(f"  {WHITE}{BOLD}5. Sequential Execution (same workflow, any region){RESET}\n")
    compare(
        "Without Temporal:",
        "200 repos x 3 API calls x ~1s each = 10 minutes minimum.\n"
        "One repo at a time. No concurrency.",
        "With Temporal:",
        "Batches of 10 repos scanned in parallel. 200 repos in ~2 minutes.\n"
        "Same workflow runs efficiently in EU or US — general best practice\n"
        "that makes one codebase viable everywhere."
    )

    story("""
        Five failures. Each one caused by the same root problem:
        application logic tangled with infrastructure concerns.
        Temporal separates them.
    """)

    teach("""
        For our story (one workflow, many jurisdictions): failure modes
        1–3 (fault tolerance, state, observability) are general best
        practices — they make any Temporal app robust. Modes 4 and 5
        are directly part of the story: encryption so the platform
        never sees our secrets in any region, and concurrency so the
        same workflow runs efficiently everywhere.
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

    teach("""
        This same architecture is what lets us run one workflow in many
        regions: same workflow and activity code everywhere; only the
        namespace (region) and worker placement change. We'll see
        encryption and sovereignty in the next two acts.
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


# ── Act 3: Encryption — Detailed Understanding ──────────────────────

async def act_3_encryption_deep_dive():
    banner("ACT 3: ENCRYPTION — DETAILED UNDERSTANDING", BG_CYAN)

    story("""
        One workflow, many jurisdictions: the platform must never see our
        secrets. In the EU we use one key; in the US another. Same code,
        per-region or per-namespace keys. This act is a focused deep dive
        on payload encryption — what gets encrypted, what doesn't, threat
        model, authenticated encryption, and how testing differs from
        production so you can run this workflow safely in any region.
    """)

    # ── Why encrypt ──
    definition("WHY ENCRYPT AT ALL", """
        By default Temporal stores all workflow and activity inputs and
        outputs in event history in PLAINTEXT. Anyone with DB or server
        access can read: workflow args (e.g. org, token), activity
        results (repo names, findings), queries, signals, memo.

        For a security scanner that means API tokens and compliance data
        are visible at rest. Temporal does not encrypt payloads for you —
        encryption is the client's responsibility. You implement a
        Payload Codec so data is encrypted before it leaves your process.
    """)
    wait()

    # ── Where encryption fits ──
    concept_box("Where Encryption Fits in the Pipeline", """
        Your code (Python objects)
          → Data Converter (objects → bytes, e.g. JSON)
          → Payload Codec (bytes → encrypted bytes)  ← YOU implement this
          → gRPC → Temporal Server (stores only ciphertext)

        The server never sees the key or the plaintext. Only the client
        (starter) and worker use the codec; they must share the same key.
        Temporal: "Your data exists unencrypted only on the Client and
        the Worker process... on hosts that you control." Hold-your-own-key.
    """)

    definition("PAYLOAD CODEC", """
        A Payload Codec transforms an array of Payloads (bytes) into another
        array of Payloads — encrypt or compress. It runs OUTSIDE the
        workflow sandbox, so you can use non-deterministic operations
        (encryption, KMS calls). Order: Payload Converter (object → bytes)
        runs first; then the Codec (bytes → bytes).
    """)
    wait()

    # ── What gets encrypted vs not ──
    concept_box("What Gets Encrypted vs What Does Not", """
        ENCRYPTED (when you use a custom Payload Codec):
          • Workflow input and output
          • Activity input and output
          • Query inputs and results, signal inputs, memo
          • Local activity and side-effect results (when applicable)

        NOT ENCRYPTED (by design — server needs these to operate):
          • Search attributes — server-side indexing. Never put secrets here.
          • Workflow type name, activity names, task queue — the "envelope"
            the server needs to route and schedule work.
          • Failure messages and stack traces — unless you configure the
            Failure Converter to encode them (we don't in this demo).
    """)
    insight("""
        If you put a secret in a search attribute, it bypasses the codec.
        Design your data model so secrets only go through workflow/activity
        payloads, memo, or signal payloads — all of which go through the codec.
    """)
    wait()

    # ── Threat model ──
    concept_box("Threat Model — What We Protect (and Don't)", """
        PROTECTED:
          • Storage / DB access — Anyone with read access to Temporal's
            persistence sees only ciphertext. No key, no plaintext. Cloud
            provider staff, DBAs, exfiltrated DB: all see ciphertext.
          • Server — The Temporal server never has the key. It cannot
            decrypt for logging, analytics, or support. HYOK.

        NOT PROTECTED:
          • Compromised client or worker — They have the key. Protect the
            key and the hosts (hardening, access control, secrets management).
          • Key theft — Env dump, secrets manager breach: all ciphertext
            at risk. Rotation and envelope encryption limit blast radius.
          • In-memory exposure — Plaintext and key exist in process memory
            during encode/decode. Memory dumps, debuggers: general limitation
            of application-level encryption.
          • Wire — Codec is application-layer; use TLS for transport.
    """)
    wait()

    # ── Authenticated encryption ──
    concept_box("Authenticated Encryption (AE) — Why It Matters", """
        We use Fernet: AES-128-CBC plus HMAC-SHA256 (encrypt-then-MAC).
        That gives CONFIDENTIALITY (only key holders read) and INTEGRITY
        (tampering detected; decrypt fails if ciphertext is modified).

        Never use "encryption only" without authentication for sensitive
        data — unauthenticated encryption (e.g. raw AES-CBC) is vulnerable
        to tampering: attackers can sometimes alter or infer plaintext.
        Production often uses AES-256-GCM (AEAD); same idea: authenticate
        what you decrypt. See ENCRYPTION.md for IV/nonce and key handling.
    """)
    wait()

    # ── Testing vs production ──
    concept_box("Testing vs Production — Clear Split", """
        TESTING (this demo): Single key (Fernet). Key from env or dev key.
        No rotation; no Codec Server. Web UI shows [binary/encrypted].
        Goal: prove the pattern, zero config.

        PRODUCTION: Envelope encryption (DEK + KEK in KMS). Key rotation
        (codec supports multiple key versions). Codec Server so Web UI/CLI
        can decode on demand — you secure and operate it. Optionally
        encrypt failure messages via Failure Converter.
    """)

    compare(
        "Good enough for TESTING (this demo)",
        "• Single symmetric key (Fernet: AES-128-CBC + HMAC-SHA256)\n"
        "• Key from env (TEMPORAL_ENCRYPTION_KEY) or hardcoded dev key\n"
        "• No key rotation; no Codec Server\n"
        "• Web UI shows [binary/encrypted] — you can't read payloads there\n"
        "• Goal: prove the pattern, zero config for demos",
        "Good enough for PRODUCTION",
        "• Envelope encryption: DEK + KEK in KMS (AWS, GCP, Vault)\n"
        "• Key rotation: multiple key versions; old history still decrypts\n"
        "• Codec Server: HTTP service with same codec; auth and operate it\n"
        "• Optionally encrypt failure messages via Failure Converter"
    )

    teach("""
        In this repo we use Fernet and a dev key so the demo works with
        zero config. For production you swap in envelope encryption and
        a KMS. The PayloadCodec pattern is the same; only the key source
        and rotation logic change. Full write-up: ENCRYPTION.md.
    """)

    why_it_matters("""
        A detailed understanding of encryption here avoids two mistakes:
        (1) leaking tokens and compliance data by assuming Temporal encrypts
        for you, or (2) over-building with a KMS and Codec Server for a
        local demo. Testing = one key, prove the pattern. Production =
        key management, rotation, and optional Codec Server.
    """)

    insight("""
        For sovereignty and encryption: this act is what gives you
        confidence that the platform never sees your plaintext in any
        region — same code, per-region keys if you want. Next: sovereignty
        (where the data lives).
    """)

    wait("Press Enter to continue to Act 4 (Sovereignty architecture)...")


# ── Act 4: Sovereignty Architecture — Detailed Understanding ──────────

async def act_4_sovereignty():
    banner("ACT 4: SOVEREIGNTY — DETAILED UNDERSTANDING", BG_CYAN)

    story("""
        One workflow, many jurisdictions: data from the EU must stay in
        the EU; data from the US in the US. Same workflow definition,
        different namespaces per region, workers in-region. This act is
        a focused deep dive on sovereignty-respecting architecture —
        why residency comes first, which levers to use (Cloud region,
        self-hosted, workers, namespace-per-region), and how to design
        so one codebase runs everywhere without moving data across borders.
    """)

    # ── Why residency first ──
    concept_box("Residency First, Encryption Second", """
        Encryption protects WHO CAN READ the data. Sovereignty and
        residency rules are about WHERE THE DATA LIVES and who has
        jurisdiction. Encryption does not move data: ciphertext
        stored in another country is still "data in that country"
        for many regulators.

        To guarantee "no sensitive data in another nation's server,"
        you must run Temporal and its storage IN that nation (or
        region). Then add encryption as defense in depth. See
        ENCRYPTION.md for why encryption alone isn't enough.
    """)
    insight("""
        "In-region" means: Temporal server, persistence (DB), and
        workers all in the same jurisdiction. If any of those is
        elsewhere, you no longer have a clean residency story.
    """)
    wait()

    # ── Lever 1: Temporal Cloud namespace region ──
    definition("LEVER 1: TEMPORAL CLOUD — NAMESPACE REGION", """
        Each Namespace is created in a specific region. All workflow
        execution and data for that namespace stay in that region;
        Temporal does not share data processing or storage across
        regional boundaries for a given namespace.

        What you do: When creating the namespace, select the region
        that matches your residency requirement (e.g. EU Frankfurt
        for EU data, Sydney for Australian data). Result: workflow
        history, visibility data, and all Temporal state for that
        namespace are stored and processed only in that region.

        Caveat: If you enable multi-region replication for failover,
        replicated data will exist in other regions. For strict
        single-region sovereignty, do not enable replication (or
        confirm with Temporal that a non-replicated namespace is
        available).
    """)
    wait()

    # ── Lever 2: Self-hosted ──
    concept_box("Lever 2: Self-Hosted Temporal in Your Region", """
        Deploy the Temporal server (frontend, history, matching) and
        its persistence store (PostgreSQL, MySQL, or Cassandra)
        entirely within your chosen region or data center.

        Result: No Temporal data leaves your region; you control the
        entire stack and its location. Run workers in the same region
        so activity execution and any data they touch also stay
        in-region. Best for: government, regulated industry,
        on-prem-only policies.
    """)
    wait()

    # ── Lever 3: Workers in same region ──
    concept_box("Lever 3: Worker Placement in the Same Region", """
        Even if the Temporal server is in the right region, workers
        execute your activity code and may call external APIs or
        databases. For sovereignty you want workers in the SAME region
        as the Temporal server (and as any data sources or sinks).

        What you do: Deploy workers in the same cloud region (or same
        country) as your Temporal namespace. Use the same VPC or
        private connectivity where possible. Result: workflow
        orchestration and activity execution both run in-region; no
        cross-border traffic for execution or data.

        Temporal Cloud: You can connect from anywhere; for clearest
        residency story, run workers in the same region as the
        namespace.
    """)
    wait()

    # ── Lever 4: Namespace-per-region ──
    definition("LEVER 4: NAMESPACE-PER-REGION (Multi-Region Orgs)", """
        If you operate in multiple regions (e.g. EU and US) and each
        has its own residency rules, use ONE NAMESPACE PER REGION and
        route traffic accordingly.

        What you do: Create namespace security-scanner-eu in EU and
        security-scanner-us in US. Your app (or a router) chooses the
        namespace based on where the data originates or where the user
        is. Run separate worker pools per region, each polling the
        namespace for that region.

        Result: EU data stays in the EU namespace; US data in the US
        namespace. No mixing in a single namespace. Best for: global
        products with regional data boundaries (e.g. GDPR + US).
    """)
    wait()

    # ── Strict single-region and private connectivity ──
    concept_box("Strict Single-Region and Private Connectivity", """
        STRICT SINGLE-REGION: Create the namespace in the desired region
        and do not enable multi-region or multi-cloud replication.
        Self-hosted: run a single cluster in one region; no replication
        to another country.

        PRIVATE CONNECTIVITY: Temporal Cloud supports AWS PrivateLink
        so traffic between your VPC and Temporal does not traverse the
        public internet. Run workers and starters in the same VPC.
        Defense in depth for "no data on public internet" policies.
    """)

    # ── Checklist and best practices ──
    concept_box("Sovereignty Architecture Checklist", """
        • Namespace region (Cloud): Create namespace in target region.
        • Self-hosted in-region: Deploy Temporal + DB in your region/DC.
        • Workers in same region: Same region as namespace and data.
        • Persistence in-region: Cloud = implied by namespace; self-hosted
          = run DB in-region; backups in-region if policy requires.
        • Namespace-per-region: One per region; route by data origin.
        • No cross-region replication: Disable for strict single-region.
        • Private connectivity: PrivateLink / private endpoints.
        • Encryption (defense in depth): PayloadCodec + key in-region.
    """)

    teach("""
        For this demo we run everything locally — one machine, one region.
        In production you pick a Temporal Cloud region or self-host in
        the jurisdiction that matches your residency requirement, run
        workers there, and add payload encryption with keys held only
        in-region. Full checklist and references: SOVEREIGNTY.md.
    """)

    why_it_matters("""
        Sovereignty is a legal and contractual requirement in many
        industries. A detailed understanding of these levers lets you
        design so "data stays in country X" is achieved by architecture
        (namespace region, worker placement, no unwanted replication),
        not by hoping encryption alone satisfies regulators.
    """)

    insight("""
        For sovereignty and encryption: this act is what gives you
        confidence that data stays in the region you chose — one
        workflow, many jurisdictions, when you follow the checklist.
        Next: we run the same workflow live and prove encryption.
    """)

    wait("Press Enter to continue to the live scan (Act 5)...")


# ── Act 5: Live Scan + Encryption Proof ──────────────────────────────

async def act_5():
    banner("ACT 5: THE LIVE PROOF")

    story("""
        Enough theory. Let's prove every claim with a live system.

        This is the same workflow we'd run in the EU namespace or the
        US namespace — only the namespace and worker location would
        change. We're running it locally for the demo.

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
                    General best practice: queries give you observability
                    from any client; in a multi-region setup each region
                    can query its own workflows.
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
        General best practice — and in multi-region, each region queries
        its own workflows.
    """)

    # ── encryption proof ──

    wait("Press Enter to inspect encryption...")

    story("""
        We've claimed everything is encrypted. In any region — EU, US,
        APAC — the platform never sees plaintext. Same guarantee in
        Frankfurt or Virginia. Let's prove it by connecting to the
        server WITHOUT our encryption key and examining the raw
        payloads — exactly what an attacker or database admin would see.
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

        For one workflow, many jurisdictions: each region can use its
        own key (e.g. EU KMS in EU, US KMS in US); the platform in
        that region still never sees it.

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


# ── Act 6: Kill Test ────────────────────────────────────────────────

async def act_6():
    banner("ACT 6: THE KILL TEST", BG_RED)

    story("""
        Durability is a general best practice — it matters whether you
        run in one region or many. For one workflow, many jurisdictions
        it means the same scan can survive a worker crash in Frankfurt
        or in Virginia; no special case per region.

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


# ── Act 7: Graceful Cancel ──────────────────────────────────────────

async def act_7():
    banner("ACT 7: GRACEFUL CANCELLATION")

    teach("""
        Graceful cancellation is a general best practice. When you run
        in many regions, each region's operators can cancel their own
        runs without touching others — same workflow, same signal.
    """)

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

    story("""
        Core to our story (one workflow, many jurisdictions): encryption
        and sovereignty — so the platform never sees our secrets and
        data stays in the region we chose. The rest — durability (kill
        test), queries, signals, parallel execution — are general best
        practices that make the same workflow robust in any region.
    """)
    print()

    concept_box("WHAT YOU CAN HAVE CONFIDENCE IN (after Part 1)", """
        ENCRYPTION: You can have confidence that the Temporal platform
        (server, DB, operator) never sees your plaintext in any region.
        Why: PayloadCodec encrypts client-side; you hold the key; we
        proved it by connecting without the key and seeing only ciphertext.
        Same guarantee in EU, US, or APAC — per-region keys if you want.

        SOVEREIGNTY: You can have confidence that workflow data stays
        in the region you chose, when you follow the architecture we
        showed (namespace in that region, workers in that region, no
        cross-border replication). Why: Act 4 spelled out the levers;
        encryption then adds defense in depth so even a mistaken copy
        would be unreadable. See SOVEREIGNTY.md and ENCRYPTION.md.
    """)
    print()

    print(f"  {WHITE}{BOLD}WHAT WE DEMONSTRATED:{RESET}\n")
    items = [
        ("Encryption — Detailed Understanding (Act 3) [core to story]",
         "What gets encrypted vs not, threat model, AE. Platform never sees plaintext in any region.",
         "Full deep dive; production: envelope encryption, KMS, Codec Server. See ENCRYPTION.md."),
        ("Sovereignty — Detailed Understanding (Act 4) [core to story]",
         "Residency first; levers: Cloud namespace region, self-hosted, workers, namespace-per-region.",
         "Checklist for sovereignty-respecting architecture. See SOVEREIGNTY.md."),
        ("Durable Execution — Kill Test (Act 6) [general best practice]",
         "Killed the worker mid-scan. Workflow survived on the server.",
         "New worker replayed history, resumed. Zero duplicate API calls."),
        ("Queryable State [general best practice]",
         "Read scan progress from an external process using typed queries.",
         "Works from CLI, code, Web UI. In multi-region, each region queries its own workflows."),
        ("Signal-Based Graceful Cancellation [general best practice]",
         "Sent a fire-and-forget signal to stop the scan.",
         "Workflow finished its batch, generated a partial report. Same in any region."),
        ("End-to-End Payload Encryption (HYOK) [core to story]",
         "Every payload encrypted client-side with AES.",
         "Connected without the key: saw only ciphertext. Same guarantee in any region."),
        ("Parallel Execution with Fault Isolation [general best practice]",
         "10 repos scanned concurrently per batch.",
         "One failure doesn't cancel the batch. Same workflow runs efficiently everywhere."),
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
        Part 2 is general best practices: update handlers, durable
        timers, continue-as-new, and schedules. They make the workflow
        production-ready in any region — not specific to the jurisdiction
        story, but part of running this same workflow everywhere.

        Four more Temporal primitives, each demonstrated live.
    """)


async def act_8():
    banner("ACT 8: LIVE SURGERY", BG_CYAN)

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


async def act_9():
    banner("ACT 9: INFINITE ENDURANCE", BG_YELLOW)

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


async def act_10():
    banner("ACT 10: THE AUTOMATION", BG_GREEN)

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
        scanned. Every payload encrypted. The scanner ran in the EU
        namespace last week and the US namespace this week — same
        workflow, same code; data never left the region they chose.
        The scanner crashed at 2am (a worker node was recycled) and
        recovered automatically by 2:01am. Nobody was paged.

        The report includes a partial scan from last night — someone
        sent a cancel signal when they realized the GitHub token
        didn't have the right scopes. That partial report is tagged
        as "cancelled" with the reason, so it won't be confused
        with a complete audit.
    """)

    wait()

    banner("WHAT WE DEMONSTRATED FOR ENCRYPTION AND SOVEREIGNTY", BG_CYAN)

    story("""
        Before we summarize what you can have confidence in, here is
        exactly what we demonstrated for encryption and for sovereignty.
    """)

    concept_box("WHAT WE DEMONSTRATED FOR ENCRYPTION", """
        • Act 3 — Detailed understanding: what gets encrypted (workflow
          input/output, activity input/output, queries, signals, memo)
          and what does not (search attributes, workflow/activity names,
          task queue). Threat model: who sees ciphertext vs who has the
          key. Authenticated encryption (Fernet: AE). Testing vs production
          (single key vs envelope + KMS, Codec Server).

        • Act 5 — Live proof: we started a real scan, then connected to
          the Temporal server WITHOUT the encryption key and inspected
          event history. We showed that the server sees only ciphertext
          (encoding: binary/encrypted); the worker and client see plaintext
          because they hold the key. Hold-your-own-key (HYOK) in practice.

        • Throughout: the same workflow runs with encryption in any region;
          per-region or per-namespace keys when you run in multiple
          jurisdictions.
    """)

    concept_box("WHAT WE DEMONSTRATED FOR SOVEREIGNTY", """
        • Act 4 — Detailed understanding: why residency comes first
          (encryption does not move data; ciphertext in another country
          is still data there for many regulators). The levers: (1) Temporal
          Cloud — create the namespace in the target region; (2) self-hosted
          — deploy Temporal and persistence in your region; (3) workers in
          the same region as the namespace; (4) namespace-per-region for
          multi-region orgs (e.g. EU namespace, US namespace); (5) no
          cross-region replication for strict single-region; (6) private
          connectivity (e.g. PrivateLink). Checklist and best practices.

        • We did not run multiple regions live (this demo is local), but
          we showed the architecture that makes "data stays in region X"
          true when you deploy that way. Encryption adds defense in depth.
          Full checklist: SOVEREIGNTY.md.
    """)

    wait()

    banner("WHAT YOU CAN HAVE CONFIDENCE IN — ENCRYPTION & SOVEREIGNTY", BG_CYAN)

    story("""
        Based on what we demonstrated above, here is what you can have
        confidence in for encryption and sovereignty, and why.
    """)

    concept_box("ENCRYPTION — What you can have confidence in", """
        The Temporal platform (server, database, cloud operator) never
        sees your plaintext workflow or activity data — in any region.

        WHAT: Tokens, inputs, results, queries, signals, memo — all
        encrypted client-side before they reach the server. The server
        stores and routes only ciphertext. You hold the key (HYOK).

        WHY: We use a PayloadCodec; the server never has the key. We
        proved it: we connected without the key and saw only ciphertext
        in the event history. In a multi-region setup you can use
        per-region or per-namespace keys (e.g. EU KMS in EU, US KMS in US).

        VALUE: Compliance and audit: your secrets never leave your trust
        boundary. The platform operator cannot read your data even with
        full database access. Same guarantee whether you run in one
        region or many.
    """)

    concept_box("SOVEREIGNTY — What you can have confidence in", """
        When you follow the architecture we showed, workflow data stays
        in the region you chose — no copy in another nation's server.

        WHAT: Create the namespace in the target region (Temporal Cloud
        or self-hosted there). Run workers in that same region. Do not
        enable cross-region replication for that namespace. Optionally
        use private connectivity (e.g. PrivateLink). Encryption adds
        defense in depth so even a mistaken copy would be unreadable.

        WHY: Act 4 walked through the levers: namespace region, self-hosted
        in-region, workers in-region, namespace-per-region for multi-region
        orgs. Sovereignty is achieved by where you store and process data,
        not by encryption alone. See SOVEREIGNTY.md for the full checklist.

        VALUE: You can run the same workflow in EU, US, APAC (or on-prem
        in a specific country) and truthfully say: data from region X never
        left region X. One codebase, one workflow definition, same
        guarantees in every jurisdiction.
    """)

    why_it_matters("""
        One workflow, many jurisdictions only works if the person looking
        for sovereignty and encryption protections knows what they can
        rely on. You can rely on: (1) the platform never seeing plaintext
        when you use a PayloadCodec and hold the key, and (2) data staying
        in-region when you place the namespace and workers there and don't
        replicate across borders. This demo gave you the evidence and the
        architecture to implement both.
    """)

    wait()

    print(f"  {WHITE}{BOLD}WHAT WE DEMONSTRATED (full list):{RESET}\n")

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
        That's the demo. One workflow, many jurisdictions. One architectural
        decision — make execution durable, encrypt at the edge, choose the
        region — that gives you fault tolerance, observability, encryption,
        sovereignty, cancellation, and concurrency. Same code, same
        guarantees, in any region you need.

        For encryption and sovereignty: you can have confidence the
        platform never sees your plaintext (you hold the key), and that
        data stays in-region when you follow the architecture we showed.
        The 2am incident doesn't happen anymore.

        Demo by Sal Kimmich — https://github.com/salkimmich
    """)


# =====================================================================
#  MAIN
# =====================================================================

async def run():
    banner("THE 2AM INCIDENT: ONE WORKFLOW, MANY JURISDICTIONS", BG_BLUE)

    story("""
        Story: We run the same security scan for teams in multiple
        countries. EU data stays in the EU; US in the US. This demo
        shows how we get durability, encryption, and sovereignty so
        one codebase runs everywhere with the same guarantees.

        An interactive, narrated walkthrough. Real workflows, real
        encryption, real crashes, real recovery — every concept
        proven live.

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
    wait("→ Act 3: Encryption — detailed understanding...")
    await act_3_encryption_deep_dive()
    wait("→ Act 4: Sovereignty — detailed understanding...")
    await act_4_sovereignty()
    wait("→ Act 5: Live Proof + Encryption...")
    await act_5()
    wait("→ Act 6: The Kill Test...")
    await act_6()
    wait("→ Act 7: Graceful Cancellation...")
    await act_7()
    await part1_summary()

    # Choice point
    print(f"  {DIM}{'-' * 64}{RESET}")
    choice = input(
        f"  {WHITE}{BOLD}Continue to Part 2 — Production Patterns? [y/n]: {RESET}"
    ).strip().lower()
    print()

    if choice in ("y", "yes", ""):
        await part2_intro()
        wait("→ Act 8: Live Surgery (updates, timers)...")
        await act_8()
        wait("→ Act 9: Continue-As-New...")
        await act_9()
        wait("→ Act 10: Schedules...")
        await act_10()
        wait("→ Epilogue...")
        await epilogue()
    else:
        story("""
            Part 2 is available any time you re-run the demo. It covers
            update handlers, durable timers, continue-as-new, and
            schedules.
        """)
        print()
        concept_box("YOUR CONFIDENCE SUMMARY (encryption & sovereignty)", """
            ENCRYPTION: The platform never sees your plaintext — you
            hold the key, PayloadCodec client-side. We proved it.
            SOVEREIGNTY: Data stays in the region you chose when you
            put namespace and workers there and don't replicate across
            borders. See SOVEREIGNTY.md and ENCRYPTION.md.
        """)
        print()

    print(f"  {DIM}Demo complete. Thank you for your time.{RESET}\n")


def main():
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print(f"\n\n  {DIM}Demo interrupted. Goodbye.{RESET}\n")


if __name__ == "__main__":
    main()
