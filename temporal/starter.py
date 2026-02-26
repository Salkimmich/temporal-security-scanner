"""
Start, query, or cancel a Security Scan workflow.

This is the CLIENT — the entry point for humans and scripts to interact
with a running Temporal application. It connects to the Temporal server
(just like the worker does), but instead of executing tasks, it starts
workflows, sends signals, and queries state.

KEY TEMPORAL CONCEPTS DEMONSTRATED:
- Starting a workflow with a deterministic ID (prevents duplicates)
- id_conflict_policy=TERMINATE_EXISTING (safe re-runs during development)
- Querying a running workflow's state without interrupting it
- Sending a signal to a running workflow (fire-and-forget)
- Execution timeout as a safety net for runaway workflows
- Encrypted data converter (must match the worker's key)

DESIGN DECISION — WHY A DETERMINISTIC WORKFLOW ID:
    We use `security-scan-{org}` as the workflow ID. This means:
    1. Only one scan per org can run at a time (enforced by Temporal)
    2. You can query/signal a scan knowing only the org name
    3. Re-running the starter for the same org terminates the old scan

    Alternative: UUID-based IDs (`security-scan-{org}-{uuid}`). This allows
    concurrent scans of the same org but makes it harder to find/manage them.
    For a security scanner, one-at-a-time is the right semantic.

Usage:
    export GITHUB_TOKEN=ghp_xxx   # recommended: avoid rate limits (60/hr without, 5000/hr with)
    python -m temporal.starter --org temporalio
    python -m temporal.starter --org temporalio --no-wait
    python -m temporal.starter --org temporalio --query
    python -m temporal.starter --org temporalio --cancel "Rate limit hit"

Environment variables:
    GITHUB_TOKEN             — GitHub Personal Access Token
    TEMPORAL_ENCRYPTION_KEY  — Must match the worker's encryption key
    TEMPORAL_HOST            — Temporal server address (default: localhost:7233)
"""

import argparse
import asyncio
import dataclasses
import json
import os
import sys
from datetime import timedelta

import temporalio.converter
from temporalio.client import Client
from temporalio.common import WorkflowIDConflictPolicy

from .encryption import EncryptionCodec
from .models import ScanInput
from .workflows import SecurityScanWorkflow

TASK_QUEUE = "security-scanner"

# Maximum time a scan workflow is allowed to run.
# This is a safety net — if something goes wrong, we don't want
# a workflow running (and consuming API quota) indefinitely.
# For a large org with 1000+ repos, 30 minutes is generous.
WORKFLOW_EXECUTION_TIMEOUT = timedelta(minutes=30)


async def main():
    parser = argparse.ArgumentParser(
        description="Start, query, or cancel a security scan workflow",
        epilog=(
            "Examples:\n"
            "  Start a scan:    python -m temporal.starter --org temporalio\n"
            "  Check progress:  python -m temporal.starter --org temporalio --query\n"
            "  Cancel a scan:   python -m temporal.starter --org temporalio --cancel 'reason'\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--org", required=True, help="GitHub organization to scan")
    parser.add_argument("--token", default=None, help="GitHub PAT (or set GITHUB_TOKEN)")
    parser.add_argument(
        "--no-wait", action="store_true", help="Start workflow and exit without waiting"
    )
    parser.add_argument(
        "--query", action="store_true",
        help="Query progress of a running scan (don't start a new one)"
    )
    parser.add_argument(
        "--cancel", metavar="REASON", default=None,
        help="Instead of starting a new scan, cancel a running one"
    )
    args = parser.parse_args()

    temporal_host = os.environ.get("TEMPORAL_HOST", "localhost:7233")

    # ── Build encrypted client ──
    #
    # CRITICAL: The starter and worker MUST use the same encryption key.
    # Without this, the worker can't decrypt the workflow input (which
    # contains the org name and token), and the starter can't decrypt
    # the workflow result (the compliance report).
    #
    # Both processes read the key from TEMPORAL_ENCRYPTION_KEY env var.
    # If unset, both fall back to the same hardcoded dev key. This
    # "just works" for local development without any configuration.
    data_converter = dataclasses.replace(
        temporalio.converter.default(),
        payload_codec=EncryptionCodec(),
    )

    client = await Client.connect(
        temporal_host,
        data_converter=data_converter,
    )

    workflow_id = f"security-scan-{args.org}"

    # ── Query mode: check progress of a running scan ──
    if args.query:
        await _query_progress(client, workflow_id, args.org)
        return

    # ── Cancel mode: signal an existing workflow ──
    if args.cancel is not None:
        await _cancel_scan(client, workflow_id, args.cancel)
        return

    # ── Start mode: launch a new scan ──
    token = args.token or os.getenv("GITHUB_TOKEN")

    if not token:
        print("⚠️  No GitHub token provided. Scanning public repos only.")
        print("   Rate limit: 60 requests/hour (~20 repos max).")
        print("   Set GITHUB_TOKEN for 5,000 requests/hour.\n")

    scan_input = ScanInput(org=args.org, token=token)

    print(f"Starting security scan for '{args.org}'...")
    print(f"  Workflow ID: {workflow_id}")
    print(f"  Task Queue:  {TASK_QUEUE}")
    print(f"  Encryption:  ENABLED (all payloads encrypted at rest)")
    print(f"  Timeout:     {WORKFLOW_EXECUTION_TIMEOUT}")
    print()

    # START THE WORKFLOW.
    #
    # id_conflict_policy=TERMINATE_EXISTING is critical for demo usability.
    # Without it, re-running the starter for the same org while a previous
    # scan is still running (e.g., from a killed worker) would throw
    # WorkflowAlreadyStartedError. With TERMINATE_EXISTING, the old workflow
    # is terminated and a new one starts cleanly.
    #
    # In production, you might prefer FAIL (the default) to prevent
    # accidental duplicate scans. For a demo, TERMINATE_EXISTING means
    # "just run it again" always works.
    handle = await client.start_workflow(
        SecurityScanWorkflow.run,
        scan_input,
        id=workflow_id,
        task_queue=TASK_QUEUE,
        execution_timeout=WORKFLOW_EXECUTION_TIMEOUT,
        id_conflict_policy=WorkflowIDConflictPolicy.TERMINATE_EXISTING,
    )

    if args.no_wait:
        print("Workflow started. Useful commands:")
        print(f"  Query progress:  python -m temporal.starter --org {args.org} --query")
        print(f"  Cancel scan:     python -m temporal.starter --org {args.org} --cancel \"reason\"")
        print(f"  Watch in UI:     http://localhost:8233/namespaces/default/workflows/{workflow_id}")
        return

    # ── Poll for progress while waiting for the result ──
    #
    # We start a background task that waits for the workflow result, then
    # periodically query progress while that task is running. This avoids
    # creating a new gRPC call every 3 seconds (which the old approach did).
    print("Scanning... (query progress at any time with --query)\n")

    result_task = asyncio.create_task(handle.result())

    while not result_task.done():
        await asyncio.sleep(3.0)
        if result_task.done():
            break
        try:
            progress = await handle.query(SecurityScanWorkflow.progress)
            status_icon = {
                "starting": "🔵",
                "scanning": "🟢",
                "completed": "✅",
                "cancelled": "🔴",
                "failed": "❌",
            }.get(progress.status, "⚪")
            # Pad to 80 chars to fully overwrite previous line
            line = (
                f"  {status_icon} [{progress.status}] "
                f"{progress.scanned_repos}/{progress.total_repos} repos "
                f"({progress.percent_complete}%) — "
                f"{progress.compliant_repos} compliant, "
                f"{progress.errors} errors"
            )
            print(f"{line:<80}", end="\r", flush=True)
        except Exception:
            # Query can fail if workflow hasn't started yet or just completed
            pass

    result = await result_task

    # ── Print the final report ──
    _print_report(result)

    # Save full report as JSON
    output_file = f"security_scan_{args.org}.json"
    with open(output_file, "w") as f:
        json.dump(result, f, indent=2)
    print(f"\nReport saved to {output_file}")


async def _query_progress(client: Client, workflow_id: str, org: str):
    """
    Query the progress of a running scan.

    TEMPORAL CONCEPT: Queries are synchronous, read-only operations.
    They don't modify workflow state and return immediately. The workflow
    doesn't even need to be "awake" — queries are handled by the SDK's
    cached workflow state on the worker.
    """
    try:
        handle = client.get_workflow_handle(workflow_id)
        progress = await handle.query(SecurityScanWorkflow.progress)

        status_icon = {
            "starting": "🔵",
            "scanning": "🟢",
            "completed": "✅",
            "cancelled": "🔴",
            "failed": "❌",
        }.get(progress.status, "⚪")

        print(f"Security Scan Progress: {org}")
        print(f"  Status:     {status_icon} {progress.status}")
        print(f"  Progress:   {progress.scanned_repos}/{progress.total_repos} repos ({progress.percent_complete}%)")
        print(f"  Compliant:  {progress.compliant_repos}")
        print(f"  Non-compliant: {progress.non_compliant_repos}")
        print(f"  Errors:     {progress.errors}")
    except Exception as e:
        print(f"Could not query workflow '{workflow_id}': {e}")
        print(f"Is a scan running? Start one with: python -m temporal.starter --org {org}")
        sys.exit(1)


async def _cancel_scan(client: Client, workflow_id: str, reason: str):
    """
    Send a cancel signal to a running scan.

    TEMPORAL CONCEPT: Signals are fire-and-forget messages TO a running
    workflow. The sender doesn't wait for a response. The signal is
    durably recorded in the workflow's event history and processed on
    the workflow's next decision task.

    This is DIFFERENT from workflow cancellation (temporal workflow cancel),
    which is an abrupt termination. Our signal-based cancellation lets the
    workflow finish its current batch and generate a partial report.
    """
    try:
        handle = client.get_workflow_handle(workflow_id)
        print(f"Sending cancel signal to workflow '{workflow_id}'...")
        print(f"  Reason: {reason}")
        await handle.signal(SecurityScanWorkflow.cancel_scan, reason)
        print("\n✅ Signal sent. The scan will stop after the current batch completes.")
        print("   A partial report will still be generated.")
        print(f"\n   Check progress: python -m temporal.starter --org {workflow_id.split('-', 2)[-1]} --query")
    except Exception as e:
        print(f"Could not signal workflow '{workflow_id}': {e}")
        sys.exit(1)


def _print_report(result: dict):
    """Print the final compliance report to stdout."""
    print("\n")
    print("=" * 60)
    if result.get("cancelled"):
        print(f"  Security Scan CANCELLED: {result['org']}")
        print(f"  Reason: {result.get('cancel_reason', 'Unknown')}")
        repos_note = f" ({result.get('repos_scanned_before_cancel', '?')} of {result['total_repos']} repos scanned)"
        print(f"  Partial results{repos_note}")
    else:
        print(f"  Security Scan Complete: {result['org']}")
    print("=" * 60)
    print(f"  Total repositories:      {result['total_repos']}")
    print(f"  Fully compliant:         {result['fully_compliant']}")
    print(f"  Compliance rate:         {result['compliance_rate']}")
    print(f"  Secret scanning:         {result['secret_scanning_enabled']}/{result['total_repos']}")
    print(f"  Dependabot alerts:       {result['dependabot_enabled']}/{result['total_repos']}")
    print(f"  Code scanning (GHAS):    {result['code_scanning_enabled']}/{result['total_repos']}")
    if result.get("errors", 0) > 0:
        print(f"  Errors:                  {result['errors']}")
    if result.get("non_compliant_repos"):
        print(f"\n  Non-compliant repos:")
        for repo in result["non_compliant_repos"]:
            print(f"    - {repo}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
