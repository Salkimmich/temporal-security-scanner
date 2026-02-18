"""
Temporal Workflow for the Security Scanner.

This is the ORCHESTRATION LOGIC — the brain of the application. It decides
what to do, in what order, and how to handle failures. It contains NO side
effects (no network calls, no file I/O, no randomness). All I/O happens
in activities.

TEMPORAL CONCEPT — DETERMINISM REQUIREMENT:
    Workflow code must be DETERMINISTIC: given the same inputs and event
    history, it must make the same decisions every time. This is because
    Temporal replays the workflow from its event history after a crash.

    If the workflow made non-deterministic calls (datetime.now(), random(),
    network I/O), replay would diverge from the original execution and
    Temporal would raise a NonDeterminismError.

    The Python SDK enforces this with a SANDBOX: workflow code runs in a
    restricted environment that blocks non-deterministic operations. Third-
    party imports must go through `workflow.unsafe.imports_passed_through()`
    to bypass the sandbox (see imports below).

    This is fundamentally different from Go, which uses a static analysis
    tool (`workflowcheck`) instead of a runtime sandbox.

TEMPORAL CONCEPT — REPLAY:
    When a worker crashes and restarts, Temporal replays the workflow from
    the beginning using the recorded event history. But it doesn't re-
    execute completed activities — it replays their recorded results.

    Example timeline:
    1. Workflow starts, fetches 50 repos (recorded)
    2. Scans batch 1 (10 repos) — all 10 activity results recorded
    3. Scans batch 2 (10 repos) — 7 completed, worker crashes at repo 8
    4. Worker restarts. Temporal replays:
       - fetch_org_repos → returns recorded result (50 repos), no API call
       - batch 1 → returns 10 recorded results, no API calls
       - batch 2 → returns 7 recorded results, schedules repo 8-10 as new
    5. Only repos 8-10 actually hit the GitHub API. Everything else is instant.

    This is the Kill Test: you can see this happening in the Temporal Web UI.

SECURITY FEATURES DEMONSTRATED:
    - Signal-based graceful cancellation (stop without losing results)
    - Safe message handler patterns (wait for handlers before exit)
    - Deterministic progress tracking (query state at any time)
    - Parallel batch execution (scan 10 repos concurrently)
"""

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

# ── Sandbox-bypassed imports ──
#
# These imports are for modules used by activities (not workflow logic).
# The workflow only references them as TYPE annotations and activity
# function references. The actual execution happens in the worker's
# activity executor (ThreadPoolExecutor), outside the sandbox.
#
# `workflow.unsafe.imports_passed_through()` tells the sandbox:
# "Don't restrict these imports — they're used by activities, not
# by workflow logic."
with workflow.unsafe.imports_passed_through():
    from .activities import check_repo_security, fetch_org_repos, generate_report
    from .models import RepoSecurityResult, ScanInput, ScanProgress


# ── Retry Policy ──
#
# TEMPORAL CONCEPT — RETRY POLICIES:
#     Temporal can retry failed activities automatically. The retry policy
#     controls HOW retries work. This is one of the biggest wins over the
#     original script, which had no retry logic at all.
#
# HOW THIS POLICY WORKS:
#     Attempt 1: fails → wait 2 seconds
#     Attempt 2: fails → wait 4 seconds  (2s × backoff 2.0)
#     Attempt 3: fails → wait 8 seconds  (4s × backoff 2.0)
#     Attempt 4: fails → wait 16 seconds (8s × backoff 2.0)
#     Attempt 5: fails → workflow gets the error (max attempts reached)
#
#     Maximum interval (60s) caps the backoff so it doesn't grow forever.
#
# NON-RETRYABLE ERRORS:
#     ValueError is non-retryable because it means "the input is wrong"
#     (invalid org, bad token). Retrying won't fix it — it would just
#     burn GitHub API quota. This maps to the HTTP pattern of 4xx (client
#     error, don't retry) vs 5xx (server error, do retry).
#
#     RuntimeError IS retryable — it represents transient failures like
#     timeouts, rate limits, and connection errors.
GITHUB_API_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=60),
    maximum_attempts=5,
    non_retryable_error_types=["ValueError"],
)

# ── Batch Size ──
#
# WHY 10:
#     Each repo takes 3 GitHub API calls. Batch of 10 = 30 API calls.
#     GitHub rate limit: 5,000/hour authenticated ≈ 83/minute.
#     At 30 calls/batch with ~2s per call, each batch takes ~6 seconds.
#     That's well within rate limits.
#
#     Batch of 10 also provides a good demo experience:
#     - Large enough that the Kill Test has time to interrupt a batch
#     - Small enough that progress updates are visually meaningful
#     - The cancellation check between batches fires every ~6 seconds
BATCH_SIZE = 10


@workflow.defn
class SecurityScanWorkflow:
    """
    Workflow that scans a GitHub organization's security posture.

    TEMPORAL CONCEPTS DEMONSTRATED:
        - Activities with retry policies and timeouts
        - Parallel activity execution in batches (asyncio.gather)
        - Queryable state (check progress mid-scan)
        - Signal-based cancellation (graceful stop)
        - Safe handler patterns (wait for handlers before exit)
        - Durability (survives worker crashes)

    STATE MANAGEMENT:
        All state lives in instance attributes (self._progress, self._results).
        These are set during execution and readable via queries.

        IMPORTANT: Workflow state is reconstructed from event history on
        replay. Don't store anything that can't be deterministically
        reconstructed (no timestamps, no random IDs, no external state).
    """

    def __init__(self) -> None:
        self._progress = ScanProgress(org="")
        self._results: list[RepoSecurityResult] = []
        self._cancel_requested = False
        self._cancel_reason: str = ""

    @workflow.run
    async def run(self, input: ScanInput) -> dict:
        """
        Main workflow execution — the entry point.

        TEMPORAL CONCEPT — @workflow.run:
            This is the method Temporal calls when the workflow starts.
            A workflow class must have exactly one @workflow.run method.
            Its signature defines the workflow's input and return types.

        DESIGN DECISION — Token in workflow input:
            The GitHub token is passed as part of ScanInput. This means
            it's stored in the workflow's event history (encrypted by our
            PayloadCodec). Alternative: store the token as an env var on
            the worker and never pass it through the workflow. Trade-off:

            Token in input:    ✅ Different callers can use different tokens
                               ❌ Token is in event history (encrypted, but still there)

            Token on worker:   ✅ Token never enters Temporal's storage
                               ❌ All callers share one token
                               ❌ Can't scan orgs that need different tokens

            For this demo, we choose input + encryption. For production with
            a single org, the env var approach is simpler.
        """
        self._progress = ScanProgress(org=input.org, status="starting")

        # ── Step 1: Fetch the list of repositories ──
        #
        # TEMPORAL CONCEPT — execute_activity:
        #     This schedules the activity on the task queue. A worker picks
        #     it up and executes it. The workflow SUSPENDS here (it's async)
        #     and resumes when the activity completes.
        #
        #     start_to_close_timeout: max time for one attempt (not total).
        #         120s is generous for paginating GitHub's API.
        #
        #     heartbeat_timeout: if the activity doesn't heartbeat within
        #         30s, the server assumes it's dead and schedules a retry.
        #         This catches stuck workers faster than start_to_close_timeout.
        #
        #     retry_policy: how to handle failures (see GITHUB_API_RETRY above).
        workflow.logger.info(f"Starting security scan for org: {input.org}")
        repos = await workflow.execute_activity(
            fetch_org_repos,
            args=[input.org, input.token],
            start_to_close_timeout=timedelta(seconds=120),
            retry_policy=GITHUB_API_RETRY,
            heartbeat_timeout=timedelta(seconds=30),
        )

        self._progress.total_repos = len(repos)
        self._progress.status = "scanning"
        workflow.logger.info(f"Found {len(repos)} repos, beginning scan")

        # ── Step 2: Scan repositories in parallel batches ──
        #
        # TEMPORAL CONCEPT — PARALLEL EXECUTION:
        #     We schedule all activities in a batch concurrently using
        #     asyncio.gather(). Each activity runs independently on the
        #     worker's thread pool. If one fails, the others still complete.
        #
        #     The workflow awaits the entire batch before starting the next.
        #     This creates natural checkpoints where we can:
        #     1. Check for cancellation signals
        #     2. Update progress state (queryable by external clients)
        #     3. Let Temporal record completed activities before proceeding
        #
        #     In Go, this pattern uses workflow.Go() + channels instead of
        #     asyncio.gather(). See go_comparison/workflow.go for details.
        for batch_start in range(0, len(repos), BATCH_SIZE):
            # ── Cancellation check (between batches) ──
            #
            # This is a NATURAL SAFE POINT. The previous batch is complete
            # (all results recorded), so stopping here loses no data.
            # We check the flag set by the cancel_scan signal handler.
            if self._cancel_requested:
                workflow.logger.info(
                    f"Scan cancelled after {self._progress.scanned_repos} repos: "
                    f"{self._cancel_reason}"
                )
                self._progress.status = "cancelled"
                break

            batch = repos[batch_start : batch_start + BATCH_SIZE]

            # Schedule all activities in this batch concurrently.
            # Each call to execute_activity returns an awaitable (a Future).
            # We collect them all, then await them together.
            tasks = []
            for repo in batch:
                task = workflow.execute_activity(
                    check_repo_security,
                    args=[input.org, repo.name, input.token],
                    start_to_close_timeout=timedelta(seconds=60),
                    retry_policy=GITHUB_API_RETRY,
                )
                tasks.append(task)

            # TEMPORAL CONCEPT — asyncio.gather with return_exceptions:
            #     return_exceptions=True means failed activities return their
            #     exception as a result instead of raising it. This lets us
            #     process successful and failed results in the same loop.
            #
            #     Without return_exceptions=True, one failed activity would
            #     cancel all other pending activities in the batch. That would
            #     waste work and lose scan results unnecessarily.
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)

            for result in batch_results:
                if isinstance(result, Exception):
                    # Activity failed after all retries. Log and continue.
                    # The error count is visible via the progress query.
                    workflow.logger.warning(f"Activity failed: {result}")
                    self._progress.errors += 1
                elif isinstance(result, RepoSecurityResult):
                    self._results.append(result)
                    self._progress.scanned_repos += 1
                    if result.is_fully_compliant:
                        self._progress.compliant_repos += 1
                    else:
                        self._progress.non_compliant_repos += 1

        # ── Step 3: Generate the compliance report ──
        #
        # We generate a report EVEN ON CANCELLATION. Partial data is
        # still valuable — "45 of 200 repos were compliant when we
        # stopped" is better than "scan cancelled, no data."
        if self._progress.status != "cancelled":
            self._progress.status = "completed"

        workflow.logger.info(
            f"Scan {'cancelled' if self._cancel_requested else 'complete'}: "
            f"{self._progress.scanned_repos}/{self._progress.total_repos} repos scanned"
        )

        report = await workflow.execute_activity(
            generate_report,
            args=[input.org, self._results],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=GITHUB_API_RETRY,
        )

        # Enrich the report with cancellation metadata if applicable.
        # This is workflow-level logic, not activity logic, because it
        # depends on workflow state (_cancel_requested, _cancel_reason).
        if self._cancel_requested:
            report["cancelled"] = True
            report["cancel_reason"] = self._cancel_reason
            report["repos_scanned_before_cancel"] = self._progress.scanned_repos

        # ── Safe handler cleanup ──
        #
        # TEMPORAL CONCEPT — SAFE MESSAGE HANDLERS:
        #     Signals are processed asynchronously. A signal could arrive
        #     while we're in the generate_report activity, creating this race:
        #
        #         1. generate_report completes
        #         2. Signal handler starts executing
        #         3. Workflow returns (run() exits)
        #         4. Signal handler is interrupted mid-execution
        #
        #     workflow.all_handlers_finished returns True when all signal/update
        #     handlers have completed. Waiting for this condition prevents the
        #     race — the workflow won't exit until all handlers finish.
        #
        #     In our case, the signal handler just sets a flag, so this is
        #     effectively a no-op. But it demonstrates the pattern and would
        #     be critical if the handler did more work (e.g., logging, calling
        #     an activity to notify an external system of cancellation).
        #
        #     This pattern comes from Temporal's safe_message_handlers sample.
        await workflow.wait_condition(workflow.all_handlers_finished)

        return report

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # SIGNALS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #
    # TEMPORAL CONCEPT — SIGNALS:
    #     Signals are fire-and-forget messages TO a running workflow.
    #     The sender doesn't wait for a response. The signal is durably
    #     recorded in the event history and processed by the workflow on
    #     its next decision task.
    #
    #     Signals vs Queries:
    #         Signal: CAN modify state, sender doesn't wait for response
    #         Query:  MUST NOT modify state, sender gets immediate response
    #
    #     Signals vs Workflow Cancellation:
    #         Signal (cancel_scan): graceful, workflow decides how to stop
    #         Cancel (temporal workflow cancel): abrupt, raises CancelledError
    #
    #     Our cancel_scan signal lets the workflow finish its current batch
    #     and generate a partial report. Temporal's built-in cancellation
    #     would interrupt the workflow immediately.

    @workflow.signal
    async def cancel_scan(self, reason: str = "Manual cancellation") -> None:
        """
        Signal the workflow to stop scanning after the current batch.

        This sets a flag that the batch loop checks. The workflow will:
        1. Finish the current batch (don't lose in-progress results)
        2. Set status to "cancelled"
        3. Generate a partial report with everything scanned so far
        4. Return the report with cancellation metadata

        Send via CLI:
            temporal workflow signal -w security-scan-<org> --name cancel_scan --input '"Reason"'

        Send via starter:
            python -m temporal.starter --org <org> --cancel "Reason"

        Send programmatically:
            await handle.signal(SecurityScanWorkflow.cancel_scan, "Rate limit concerns")
        """
        self._cancel_requested = True
        self._cancel_reason = reason
        workflow.logger.info(f"Cancellation requested: {reason}")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # QUERIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #
    # TEMPORAL CONCEPT — QUERIES:
    #     Queries are SYNCHRONOUS, READ-ONLY operations. They:
    #     - Return immediately with the workflow's current state
    #     - MUST NOT modify any workflow state
    #     - Can be called at any time (even while the workflow is running)
    #     - Are handled by the SDK's cached state on the worker
    #       (no event history replay needed)
    #
    #     Queries are how external systems observe a running workflow
    #     without interfering with it. The progress query is what makes
    #     the starter's progress bar possible.

    @workflow.query
    def progress(self) -> ScanProgress:
        """
        Query the current scan progress.

        Returns a ScanProgress dataclass with:
        - org, total_repos, scanned_repos
        - compliant_repos, non_compliant_repos, errors
        - status (starting/scanning/completed/cancelled/failed)
        - percent_complete (computed property)

        Query via CLI:
            temporal workflow query -w security-scan-<org> --type progress

        Query via starter:
            python -m temporal.starter --org <org> --query
        """
        return self._progress

    @workflow.query
    def results_so_far(self) -> list[RepoSecurityResult]:
        """Query partial results during a scan. Returns all completed repo checks."""
        return self._results

    @workflow.query
    def is_cancelled(self) -> bool:
        """Check whether cancellation has been requested."""
        return self._cancel_requested
