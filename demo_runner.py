#!/usr/bin/env python3
"""
The 2am Incident — A Temporal Security Scanner Demo
=====================================================

Built by Sal Kimmich to demonstrate that durable execution isn't a
feature you bolt on — it's an architectural decision that changes
what's possible.

This demo has two parts:

    PART 1 — CORE CONCEPTS  (~3 minutes)
    Start a real security scan, prove encryption works, kill the
    worker mid-scan, watch it recover, cancel gracefully. Five
    Temporal primitives, fast.

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
import sys
import textwrap
import time
from datetime import timedelta

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
BG_YELLOW = "\033[43m"
BG_MAGENTA = "\033[45m"
BG_CYAN = "\033[46m"

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
    for line in textwrap.dedent(text).strip().splitlines():
        print(f"  {WHITE}{line}{RESET}")
    print()

def teach(text):
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
    print(f"  {MAGENTA}{BOLD}+{'-' * 60}+{RESET}")
    print()

def insight(text):
    print(f"  {GREEN}{BOLD}   >> KEY INSIGHT:{RESET}")
    for line in textwrap.dedent(text).strip().splitlines():
        print(f"  {GREEN}   {line}{RESET}")
    print()

def dramatic(text):
    print(f"  {RED}{BOLD}{text}{RESET}")

def compare(label_a, text_a, label_b, text_b):
    print(f"  {RED}{BOLD}WITHOUT: {label_a}{RESET}")
    for line in textwrap.dedent(text_a).strip().splitlines():
        print(f"    {DIM}{line}{RESET}")
    print()
    print(f"  {GREEN}{BOLD}WITH:    {label_b}{RESET}")
    for line in textwrap.dedent(text_b).strip().splitlines():
        print(f"    {line}")
    print()

def server_sees(label, data):
    d = str(data)[:80] + ("..." if len(str(data)) > 80 else "")
    print(f"  {RED}{BOLD}LOCKED   {DIM}(what the server stores){RESET} {label}")
    print(f"    {DIM}{d}{RESET}")
    print()

def worker_sees(label, data):
    d = str(data)[:120] + ("..." if len(str(data)) > 120 else "")
    print(f"  {GREEN}{BOLD}UNLOCKED {DIM}(what the worker decrypts){RESET} {label}")
    print(f"    {d}")
    print()

def cmd_display(cmd):
    print(f"  {GREEN}{BOLD}> {cmd}{RESET}\n")

def web_link(path, what=""):
    print(f"  {MAGENTA}WEB UI: {WHITE}{WEB_UI}{path}{RESET}")
    if what:
        print(f"    {DIM}Look for: {what}{RESET}")
    print()

def wait(msg="Press Enter to continue..."):
    print(f"  {DIM}{'-' * 64}{RESET}")
    input(f"  {DIM}{msg}{RESET}")
    print()

def quiz(question, options, answer_idx, explanation):
    print(f"  {BG_CYAN}{WHITE}{BOLD} QUICK CHECK {RESET}\n")
    print(f"  {WHITE}{BOLD}{question}{RESET}\n")
    for i, opt in enumerate(options):
        print(f"    {WHITE}{chr(65+i)}) {opt}{RESET}")
    print()
    input(f"  {DIM}Think about it, then press Enter to see the answer...{RESET}")
    print()
    print(f"  {GREEN}{BOLD}Answer: {chr(65+answer_idx)}) {options[answer_idx]}{RESET}\n")
    for line in textwrap.dedent(explanation).strip().splitlines():
        print(f"  {GREEN}{line}{RESET}")
    print()


# ===================================================================
# TEMPORAL CLIENT HELPERS
# ===================================================================

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

async def get_event_count(client, workflow_id):
    handle = client.get_workflow_handle(workflow_id)
    count = 0
    async for _ in handle.fetch_history_events():
        count += 1
    return count

def make_bar(p, w=40):
    filled = int(w * p.percent_complete / 100)
    return f"{'#' * filled}{'.' * (w - filled)}"

def format_progress(p, batch_size=10, extra=""):
    if p.total_repos == 0:
        return f"    {DIM}(fetching repository list...){RESET}"
    batch_num = (p.scanned_repos // batch_size) + 1
    batch_total = (p.total_repos + batch_size - 1) // batch_size
    if p.scanned_repos >= p.total_repos and p.total_repos > 0:
        batch_num = batch_total
    status_color = GREEN if p.status == "completed" else WHITE
    return (
        f"  {GREEN}Batch {batch_num}/{batch_total}{RESET} "
        f"{status_color}[{make_bar(p)}] "
        f"{p.scanned_repos}/{p.total_repos} "
        f"({p.percent_complete}%) | {p.status}{RESET}"
        f"{extra}"
    )

async def wait_for_repos(handle, target=1, timeout_s=20, batch_size=10):
    last_scanned = -1
    shown_fetching = False
    for _ in range(timeout_s):
        await asyncio.sleep(1)
        try:
            p = await qprogress(handle)
            if p.total_repos == 0 and not shown_fetching:
                shown_fetching = True
                print(format_progress(p, batch_size))
                continue
            if p.scanned_repos != last_scanned and p.total_repos > 0:
                last_scanned = p.scanned_repos
                print(format_progress(p, batch_size))
            if p.scanned_repos >= target or p.status in ("completed", "cancelled"):
                return p
        except Exception:
            pass
    return None

async def watch_to_completion(handle, resume_marker=None, batch_size=10,
                              poll_s=2, max_polls=40):
    last_scanned = -1
    last_status = ""
    marked_resume = False
    marked_cont = False
    for _ in range(max_polls):
        await asyncio.sleep(poll_s)
        try:
            p = await qprogress(handle)
            extra = ""
            if resume_marker and not marked_resume and p.scanned_repos > resume_marker:
                marked_resume = True
                extra = f"  {GREEN}{BOLD}<< RESUMED (was at {resume_marker}){RESET}"
            if p.continuation_count > 0 and not marked_cont:
                marked_cont = True
                extra = f"  {MAGENTA}{BOLD}<< CONTINUE-AS-NEW #{p.continuation_count}{RESET}"
            elif p.continuation_count > 0:
                extra = f"  {DIM}(continuation #{p.continuation_count}){RESET}"
            if p.timer_active or p.status == "paused":
                extra += f"  {MAGENTA}(server-side timer){RESET}"
            changed = (p.scanned_repos != last_scanned or p.status != last_status)
            if (changed or extra) and p.total_repos > 0:
                last_scanned = p.scanned_repos
                last_status = p.status
                print(format_progress(p, batch_size, extra))
            if p.status == "completed":
                return p
        except Exception:
            pass
    return None

# ===================================================================
# PRE-FLIGHT CHECK
# ===================================================================

async def preflight():
    banner("PRE-FLIGHT CHECK", BG_CYAN)

    story("""
        Verifying environment, dependencies, and running services.
        If anything fails, run:  .\\setup.ps1
    """)

    all_ok = True

    def check_ok(label, detail=""):
        d = f" ({detail})" if detail else ""
        print(f"  {GREEN}  OK{RESET}  {label}{DIM}{d}{RESET}")

    def check_fail(label, detail=""):
        nonlocal all_ok
        all_ok = False
        d = f" — {detail}" if detail else ""
        print(f"  {RED}FAIL{RESET}  {label}{RED}{d}{RESET}")

    def check_warn(label, detail=""):
        print(f"  {YELLOW}WARN{RESET}  {label}{YELLOW} — {detail}{RESET}")

    # 1. Python version
    v = sys.version_info
    if v.major >= 3 and v.minor >= 11:
        check_ok("Python", f"{v.major}.{v.minor}.{v.micro}")
    else:
        check_fail("Python", f"{v.major}.{v.minor} found, need 3.11+")

    # 2. Temporal SDK
    try:
        import temporalio
        check_ok("Temporal SDK", temporalio.__version__)
    except ImportError:
        check_fail("Temporal SDK", "pip install temporalio")

    # 3. Cryptography
    try:
        import cryptography
        check_ok("cryptography", cryptography.__version__)
    except ImportError:
        check_fail("cryptography", "pip install cryptography")

    # 4. Requests
    try:
        import requests as req_mod
        check_ok("requests", req_mod.__version__)
    except ImportError:
        check_fail("requests", "pip install requests")

    # 5. Project files
    files = ["temporal/workflows.py", "temporal/activities.py",
             "temporal/encryption.py", "temporal/models.py",
             "temporal/worker.py", "demo_runner.py"]
    missing = [f for f in files if not os.path.exists(f)]
    if not missing:
        check_ok("Project files", f"{len(files)} found")
    else:
        check_fail("Project files", f"missing: {', '.join(missing)}")

    # 6. Temporal CLI
    temporal_found = False
    try:
        result = subprocess.run(
            ["temporal", "--version"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            ver = result.stdout.strip().split('\n')[0]
            check_ok("Temporal CLI", ver)
            temporal_found = True
        else:
            check_fail("Temporal CLI", "installed but returned error")
    except FileNotFoundError:
        check_fail("Temporal CLI", "not found on PATH — run .\\setup.ps1")
    except Exception as e:
        check_fail("Temporal CLI", str(e))

    # 7. Temporal server
    server_ok = False
    try:
        from temporalio.client import Client
        await Client.connect("localhost:7233")
        check_ok("Temporal server", "localhost:7233")
        server_ok = True
    except Exception:
        if temporal_found:
            check_fail("Temporal server", "not running — start with: temporal server start-dev")
        else:
            check_fail("Temporal server", "install Temporal CLI first, then: temporal server start-dev")

    # 8. Encryption
    try:
        from temporal.encryption import EncryptionCodec
        EncryptionCodec()
        check_ok("Encryption codec", "Fernet AES")
    except Exception as e:
        check_fail("Encryption codec", str(e))

    # 9. PYTHONPATH
    pp = os.environ.get("PYTHONPATH", "")
    cwd = os.getcwd()
    if cwd in pp or os.path.exists("temporal/__init__.py"):
        check_ok("PYTHONPATH", cwd)
    else:
        check_warn("PYTHONPATH", f'not set — run: $env:PYTHONPATH = "{cwd}"')

    # 10. GitHub token
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        check_ok("GITHUB_TOKEN", f"set ({len(token)} chars)")
    else:
        check_warn("GITHUB_TOKEN", "not set — unauthenticated = 60 req/hr limit")

    # 11. Worker (soft check)
    if server_ok:
        try:
            client = await get_client()
            from temporal.workflows import SecurityScanWorkflow
            handle = client.get_workflow_handle("security-scan-temporalio")
            await handle.query(SecurityScanWorkflow.progress)
            check_ok("Worker", "responding on security-scanner queue")
        except Exception:
            check_warn("Worker", "will verify on first scan")

    print()
    if all_ok:
        print(f"  {WHITE}Web UI: {BOLD}{WEB_UI}{RESET}")
        print(f"  {DIM}Open in a browser to follow along visually.{RESET}\n")
    else:
        print(f"  {RED}{BOLD}Environment not ready.{RESET}")
        print(f"  {RED}Run this from the project directory:{RESET}")
        print(f"  {WHITE}{BOLD}  .\\setup.ps1{RESET}\n")

    return all_ok

# ===================================================================
# INTRODUCTION
# ===================================================================

async def introduction():
    banner("THE 2AM INCIDENT")
    story("""
        A Temporal Security Scanner Demo
        by Sal Kimmich
    """)
    story("""
        I built this because I've been the person staring at a dead
        terminal at 2am, re-running a script that lost four hours of
        API calls to a process crash. The standard fixes -- more
        try/except blocks, a Redis checkpoint, a database-backed
        queue -- all add complexity to paper over a fundamental gap:
        your application logic shouldn't be responsible for its own
        durability.

        Temporal separates the two. Your code says what to do. The
        platform guarantees it finishes, even when infrastructure
        doesn't cooperate.

        This demo proves that claim with a live system. We'll scan
        a real GitHub organization for security compliance posture --
        real API calls, real encryption, real crashes, real recovery --
        using Temporal to make the process resilient.
    """)
    concept_box("WHAT YOU'LL LEARN", """
        PART 1 -- Core Concepts  (~3 minutes)
          Durable execution, payload encryption, queries, signals,
          idempotent retry. Start a scan, prove encryption works,
          kill the worker, watch it recover.

        PART 2 -- Production Patterns  (~7 minutes, optional)
          Update handlers with validators, durable timers that
          survive crashes, continue-as-new for bounded history,
          and Temporal's built-in scheduler.
    """)

async def part1_intro():
    banner("PART 1: CORE CONCEPTS", BG_BLUE)
    story("""
        The fast version. Five Temporal primitives, each demonstrated
        live with a real security scan.
    """)
