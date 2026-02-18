"""
Start, query, update, pause, cancel, or schedule Security Scan workflows.

Usage:
    python -m temporal.starter --org temporalio                     # Start scan
    python -m temporal.starter --org temporalio --no-wait           # Start, don't wait
    python -m temporal.starter --org temporalio --query             # Query progress
    python -m temporal.starter --org temporalio --cancel "reason"   # Cancel scan
    python -m temporal.starter --org temporalio --pause 30          # Pause for 30s (durable timer)
    python -m temporal.starter --org temporalio --update-batch-size 5  # Change batch size
    python -m temporal.starter --org temporalio --schedule 3600     # Schedule hourly scans
    python -m temporal.starter --org temporalio --delete-schedule   # Remove schedule
"""

import argparse
import asyncio
import dataclasses
import json
import os
import sys
from datetime import timedelta

import temporalio.converter
from temporalio.client import Client, Schedule, ScheduleActionStartWorkflow, \
    ScheduleIntervalSpec, ScheduleSpec, ScheduleState
from temporalio.common import WorkflowIDConflictPolicy

from .encryption import EncryptionCodec
from .models import ScanInput
from .workflows import SecurityScanWorkflow

TASK_QUEUE = "security-scanner"
WORKFLOW_EXECUTION_TIMEOUT = timedelta(minutes=30)


async def main():
    parser = argparse.ArgumentParser(
        description="Manage security scan workflows",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--org", required=True, help="GitHub organization to scan")
    parser.add_argument("--token", default=None, help="GitHub PAT (or set GITHUB_TOKEN)")
    parser.add_argument("--no-wait", action="store_true", help="Start and exit")
    parser.add_argument("--query", action="store_true", help="Query progress")
    parser.add_argument("--cancel", metavar="REASON", default=None, help="Cancel scan")
    # New commands for advanced features
    parser.add_argument("--pause", metavar="SECONDS", type=int, default=None,
                        help="Pause scan with a durable timer (survives crashes)")
    parser.add_argument("--update-batch-size", metavar="N", type=int, default=None,
                        help="Update batch size via validated update handler")
    parser.add_argument("--schedule", metavar="SECONDS", type=int, default=None,
                        help="Create a recurring schedule (interval in seconds)")
    parser.add_argument("--delete-schedule", action="store_true",
                        help="Delete the schedule for this org")
    args = parser.parse_args()

    temporal_host = os.environ.get("TEMPORAL_HOST", "localhost:7233")

    data_converter = dataclasses.replace(
        temporalio.converter.default(),
        payload_codec=EncryptionCodec(),
    )

    client = await Client.connect(temporal_host, data_converter=data_converter)

    workflow_id = f"security-scan-{args.org}"
    schedule_id = f"security-scan-schedule-{args.org}"

    # ── Route to the right handler ──
    if args.query:
        await _query_progress(client, workflow_id, args.org)
    elif args.cancel is not None:
        await _cancel_scan(client, workflow_id, args.cancel)
    elif args.pause is not None:
        await _pause_scan(client, workflow_id, args.pause)
    elif args.update_batch_size is not None:
        await _update_batch_size(client, workflow_id, args.update_batch_size)
    elif args.schedule is not None:
        await _create_schedule(client, schedule_id, args.org,
                               args.token or os.getenv("GITHUB_TOKEN"),
                               args.schedule)
    elif args.delete_schedule:
        await _delete_schedule(client, schedule_id)
    else:
        await _start_scan(client, workflow_id, args)


async def _start_scan(client, workflow_id, args):
    token = args.token or os.getenv("GITHUB_TOKEN")

    if not token:
        print("\u26a0\ufe0f  No GitHub token provided. Scanning public repos only.")
        print("   Rate limit: 60 requests/hour (~20 repos max).")
        print("   Set GITHUB_TOKEN for 5,000 requests/hour.\n")

    scan_input = ScanInput(org=args.org, token=token)

    print(f"Starting security scan for '{args.org}'...")
    print(f"  Workflow ID: {workflow_id}")
    print(f"  Task Queue:  {TASK_QUEUE}")
    print(f"  Encryption:  ENABLED (all payloads encrypted at rest)")
    print(f"  Timeout:     {WORKFLOW_EXECUTION_TIMEOUT}")
    print()

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
        print(f"  Query progress:    python -m temporal.starter --org {args.org} --query")
        print(f"  Cancel scan:       python -m temporal.starter --org {args.org} --cancel \"reason\"")
        print(f"  Pause (timer):     python -m temporal.starter --org {args.org} --pause 30")
        print(f"  Update batch size: python -m temporal.starter --org {args.org} --update-batch-size 5")
        print(f"  Schedule hourly:   python -m temporal.starter --org {args.org} --schedule 3600")
        print(f"  Watch in UI:       http://localhost:8233/namespaces/default/workflows/{workflow_id}")
        return

    print("Scanning... (query progress at any time with --query)\n")

    result_task = asyncio.create_task(handle.result())

    while not result_task.done():
        await asyncio.sleep(3.0)
        if result_task.done():
            break
        try:
            progress = await handle.query(SecurityScanWorkflow.progress)
            status_icon = {
                "starting": "\U0001f535", "scanning": "\U0001f7e2",
                "completed": "\u2705", "cancelled": "\U0001f534",
                "failed": "\u274c", "paused": "\u23f8\ufe0f",
            }.get(progress.status, "\u26aa")
            line = (
                f"  {status_icon} [{progress.status}] "
                f"{progress.scanned_repos}/{progress.total_repos} repos "
                f"({progress.percent_complete}%) \u2014 "
                f"{progress.compliant_repos} compliant, "
                f"{progress.errors} errors"
            )
            if progress.timer_active:
                line += f" | TIMER: ~{progress.timer_remaining_secs}s"
            if progress.batch_size != 10:
                line += f" | batch={progress.batch_size}"
            print(f"{line:<90}", end="\r", flush=True)
        except Exception:
            pass

    result = await result_task
    _print_report(result)

    output_file = f"security_scan_{args.org}.json"
    with open(output_file, "w") as f:
        json.dump(result, f, indent=2)
    print(f"\nReport saved to {output_file}")


async def _query_progress(client, workflow_id, org):
    try:
        handle = client.get_workflow_handle(workflow_id)
        progress = await handle.query(SecurityScanWorkflow.progress)

        status_icon = {
            "starting": "\U0001f535", "scanning": "\U0001f7e2",
            "completed": "\u2705", "cancelled": "\U0001f534",
            "failed": "\u274c", "paused": "\u23f8\ufe0f",
        }.get(progress.status, "\u26aa")

        print(f"Security Scan Progress: {org}")
        print(f"  Status:       {status_icon} {progress.status}")
        print(f"  Progress:     {progress.scanned_repos}/{progress.total_repos} repos ({progress.percent_complete}%)")
        print(f"  Compliant:    {progress.compliant_repos}")
        print(f"  Non-compliant: {progress.non_compliant_repos}")
        print(f"  Errors:       {progress.errors}")
        print(f"  Batch size:   {progress.batch_size}")
        if progress.continuation_count > 0:
            print(f"  Continuations: {progress.continuation_count}")
        if progress.timer_active:
            print(f"  Timer:        ACTIVE (~{progress.timer_remaining_secs}s remaining)")
    except Exception as e:
        print(f"Could not query workflow '{workflow_id}': {e}")
        print(f"Is a scan running? Start one with: python -m temporal.starter --org {org}")
        sys.exit(1)


async def _cancel_scan(client, workflow_id, reason):
    try:
        handle = client.get_workflow_handle(workflow_id)
        print(f"Sending cancel signal to workflow '{workflow_id}'...")
        print(f"  Reason: {reason}")
        await handle.signal(SecurityScanWorkflow.cancel_scan, reason)
        print("\n\u2705 Signal sent. The scan will stop after the current batch completes.")
        print("   A partial report will still be generated.")
    except Exception as e:
        print(f"Could not signal workflow '{workflow_id}': {e}")
        sys.exit(1)


async def _pause_scan(client, workflow_id, duration_seconds):
    """
    Send a pause signal that triggers a DURABLE TIMER.

    The timer is created on the Temporal server. It survives worker crashes.
    """
    try:
        handle = client.get_workflow_handle(workflow_id)
        print(f"Sending pause signal to workflow '{workflow_id}'...")
        print(f"  Duration: {duration_seconds} seconds (durable timer)")
        await handle.signal(SecurityScanWorkflow.pause_scan, duration_seconds)
        print(f"\n\u2705 Signal sent. The scan will pause for {duration_seconds}s after the current batch.")
        print("   The timer is SERVER-SIDE \u2014 it survives worker crashes.")
        print("   Kill the worker, restart it, and the timer still fires on schedule.")
    except Exception as e:
        print(f"Could not signal workflow '{workflow_id}': {e}")
        sys.exit(1)


async def _update_batch_size(client, workflow_id, new_size):
    """
    Send an UPDATE (not a signal) to change the batch size.

    Unlike signals, the caller WAITS for the response.
    The update validator rejects invalid values before the handler runs.
    """
    try:
        handle = client.get_workflow_handle(workflow_id)
        print(f"Sending update to workflow '{workflow_id}'...")
        print(f"  New batch size: {new_size}")

        # This WAITS for the workflow to process the update and return.
        # If the validator rejects it, we get an error immediately.
        result = await handle.execute_update(
            SecurityScanWorkflow.update_batch_size,
            new_size,
        )
        print(f"\n\u2705 {result}")
    except Exception as e:
        error_msg = str(e)
        if "Batch size must be" in error_msg or "Cannot update" in error_msg:
            print(f"\n\u274c Update REJECTED by validator: {error_msg}")
            print("   Workflow state was NOT modified (validator caught it).")
        else:
            print(f"\nCould not update workflow '{workflow_id}': {e}")
        sys.exit(1)


async def _create_schedule(client, schedule_id, org, token, interval_seconds):
    """
    TEMPORAL CONCEPT — SCHEDULES:
        Schedules are Temporal's built-in cron replacement. Instead of a
        crontab entry that runs a script, you define a schedule that starts
        a workflow at the specified interval.

        Advantages over cron:
        - The workflow itself is durable (survives crashes)
        - Built-in overlap policies (skip, buffer, cancel previous, etc)
        - Pause/resume without removing the schedule
        - Full audit trail in the Temporal UI
        - No need for a separate cron server

        The schedule creates workflow executions. Each execution is a
        normal workflow with its own event history, queries, signals, etc.
    """
    try:
        scan_input = ScanInput(org=org, token=token)
        interval = timedelta(seconds=interval_seconds)

        await client.create_schedule(
            schedule_id,
            Schedule(
                action=ScheduleActionStartWorkflow(
                    SecurityScanWorkflow.run,
                    scan_input,
                    id=f"security-scan-{org}",
                    task_queue=TASK_QUEUE,
                    execution_timeout=WORKFLOW_EXECUTION_TIMEOUT,
                ),
                spec=ScheduleSpec(
                    intervals=[ScheduleIntervalSpec(every=interval)],
                ),
                state=ScheduleState(
                    note=f"Recurring security scan for {org}",
                ),
            ),
        )

        minutes = interval_seconds / 60
        hours = interval_seconds / 3600
        if hours >= 1:
            human = f"every {hours:.0f} hour(s)"
        else:
            human = f"every {minutes:.0f} minute(s)"

        print(f"\u2705 Schedule created: '{schedule_id}'")
        print(f"   Frequency: {human}")
        print(f"   Org:       {org}")
        print(f"   View:      http://localhost:8233/namespaces/default/schedules/{schedule_id}")
        print(f"\n   Delete:    python -m temporal.starter --org {org} --delete-schedule")
    except Exception as e:
        if "already exists" in str(e).lower() or "already running" in str(e).lower():
            print(f"Schedule '{schedule_id}' already exists.")
            print(f"Delete it first: python -m temporal.starter --org {org} --delete-schedule")
        else:
            print(f"Could not create schedule: {e}")
        sys.exit(1)


async def _delete_schedule(client, schedule_id):
    try:
        handle = client.get_schedule_handle(schedule_id)
        await handle.delete()
        print(f"\u2705 Schedule '{schedule_id}' deleted.")
    except Exception as e:
        print(f"Could not delete schedule '{schedule_id}': {e}")
        sys.exit(1)


def _print_report(result: dict):
    print("\n")
    print("=" * 60)
    if result.get("cancelled"):
        print(f"  Security Scan CANCELLED: {result['org']}")
        print(f"  Reason: {result.get('cancel_reason', 'Unknown')}")
    else:
        print(f"  Security Scan Complete: {result['org']}")
    print("=" * 60)
    print(f"  Total repositories:      {result['total_repos']}")
    print(f"  Fully compliant:         {result['fully_compliant']}")
    print(f"  Compliance rate:         {result['compliance_rate']}")
    print(f"  Secret scanning:         {result['secret_scanning_enabled']}/{result['total_repos']}")
    print(f"  Dependabot alerts:       {result['dependabot_enabled']}/{result['total_repos']}")
    print(f"  Code scanning (GHAS):    {result['code_scanning_enabled']}/{result['total_repos']}")
    if result.get("continue_as_new_count"):
        print(f"  Continue-as-new count:   {result['continue_as_new_count']}")
    if result.get("non_compliant_repos"):
        print(f"\n  Non-compliant repos:")
        for repo in result["non_compliant_repos"]:
            print(f"    - {repo}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
