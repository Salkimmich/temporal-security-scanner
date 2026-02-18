"""
Security Scanner Workflow — the orchestration brain.

Decides what to do, in what order, how to handle failures. Contains NO
side effects. All I/O lives in activities. This split isn't a suggestion,
it's enforced by the sandbox.

What's in here:
    - Parallel batch scanning with fault isolation
    - Signal-based cancellation and pause (durable timers)
    - Update handlers with validators (batch size changes)
    - Continue-as-new for bounded history growth
    - Queryable state for external observability
"""

import asyncio
from dataclasses import asdict
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from .activities import check_repo_security, fetch_org_repos, generate_report
    from .models import RepoInfo, RepoSecurityResult, ScanInput, ScanProgress


# Retry: exponential backoff 2s → 4s → 8s → 16s → 60s cap, 5 attempts.
# ValueError is non-retryable (bad input won't get better on retry).
GITHUB_API_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=60),
    maximum_attempts=5,
    non_retryable_error_types=["ValueError"],
)

DEFAULT_BATCH_SIZE = 10

# History threshold for continue-as-new. Low for the demo (500 events
# triggers around batch 15 with ~194 repos). Production: 10K-20K.
# The SDK also provides workflow.info().is_continue_as_new_suggested()
# which factors in both event count and payload size — use that in prod.
MAX_HISTORY_EVENTS = 500


@workflow.defn
class SecurityScanWorkflow:

    def __init__(self) -> None:
        self._progress = ScanProgress(org="")
        self._results: list[RepoSecurityResult] = []
        self._cancel_requested = False
        self._cancel_reason: str = ""
        self._batch_size = DEFAULT_BATCH_SIZE
        self._pause_requested = False
        self._pause_duration_secs: int = 0
        self._timer_active = False
        self._continuation_count = 0

    @workflow.run
    async def run(self, input: ScanInput) -> dict:
        self._progress = ScanProgress(org=input.org, status="starting")

        repos = []
        batch_offset = 0

        # If we got here via continue-as-new, restore everything from the
        # previous execution. The repo list, accumulated results, batch
        # position — all serialized in the input so we don't re-fetch or
        # re-scan anything.
        if input.continuation_state:
            state = input.continuation_state
            self._continuation_count = state.get("continuation_count", 0)
            self._progress.continuation_count = self._continuation_count
            self._batch_size = state.get("batch_size", DEFAULT_BATCH_SIZE)
            batch_offset = state.get("batch_offset", 0)

            repos = [RepoInfo(**r) for r in state.get("repos", [])]

            for r in state.get("results", []):
                result = RepoSecurityResult(**r)
                self._results.append(result)
                self._progress.scanned_repos += 1
                if result.is_fully_compliant:
                    self._progress.compliant_repos += 1
                else:
                    self._progress.non_compliant_repos += 1

            self._progress.total_repos = len(repos)
            self._progress.batch_size = self._batch_size
            self._progress.status = "scanning"
            workflow.logger.info(
                f"Continued as new (#{self._continuation_count}): "
                f"{self._progress.scanned_repos}/{len(repos)} already done, "
                f"resuming at offset {batch_offset}"
            )
        else:
            # Fresh start — go get the repo list.
            workflow.logger.info(f"Starting scan for {input.org}")
            repos = await workflow.execute_activity(
                fetch_org_repos,
                args=[input.org, input.token],
                start_to_close_timeout=timedelta(seconds=120),
                retry_policy=GITHUB_API_RETRY,
                heartbeat_timeout=timedelta(seconds=30),
            )
            self._progress.total_repos = len(repos)
            self._progress.batch_size = self._batch_size
            self._progress.status = "scanning"
            workflow.logger.info(f"Found {len(repos)} repos")

        # Main loop: scan in batches, check for cancellation/pause/history
        # limits between each batch.
        batch_start = batch_offset
        while batch_start < len(repos):

            if self._cancel_requested:
                workflow.logger.info(f"Cancelled at {self._progress.scanned_repos} repos")
                self._progress.status = "cancelled"
                break

            # Durable timer: workflow.sleep() is a SERVER-SIDE timer. It
            # survives worker crashes because the timer event lives on the
            # server, not in your process. Kill the worker mid-sleep, restart
            # it 45 minutes later — the timer still fires on schedule.
            if self._pause_requested:
                duration = timedelta(seconds=self._pause_duration_secs)
                workflow.logger.info(f"Pausing for {duration}")
                self._timer_active = True
                self._progress.timer_active = True
                self._progress.timer_remaining_secs = self._pause_duration_secs
                self._progress.status = "paused"

                await workflow.sleep(duration)  # server-side, crash-proof

                self._timer_active = False
                self._pause_requested = False
                self._progress.timer_active = False
                self._progress.timer_remaining_secs = 0
                self._progress.status = "scanning"
                workflow.logger.info("Timer fired, resuming")

            # Continue-as-new: if the event history is getting fat, package
            # up our state and start a fresh execution. Same workflow ID,
            # clean history, scan picks up right here.
            current_length = workflow.info().get_current_history_length()
            if current_length > MAX_HISTORY_EVENTS:
                workflow.logger.info(
                    f"History at {current_length} events, continuing as new "
                    f"at offset {batch_start}"
                )
                workflow.continue_as_new(ScanInput(
                    org=input.org,
                    token=input.token,
                    continuation_state={
                        "results": [asdict(r) for r in self._results],
                        "repos": [asdict(r) for r in repos],
                        "batch_offset": batch_start,
                        "batch_size": self._batch_size,
                        "continuation_count": self._continuation_count + 1,
                    },
                ))

            # Fire off the batch. Each activity runs independently on the
            # worker's thread pool. return_exceptions=True means one failure
            # doesn't take down the other nine.
            batch = repos[batch_start : batch_start + self._batch_size]
            tasks = [
                workflow.execute_activity(
                    check_repo_security,
                    args=[input.org, repo.name, input.token],
                    start_to_close_timeout=timedelta(seconds=60),
                    retry_policy=GITHUB_API_RETRY,
                )
                for repo in batch
            ]
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)

            for result in batch_results:
                if isinstance(result, Exception):
                    workflow.logger.warning(f"Activity failed: {result}")
                    self._progress.errors += 1
                elif isinstance(result, RepoSecurityResult):
                    self._results.append(result)
                    self._progress.scanned_repos += 1
                    if result.is_fully_compliant:
                        self._progress.compliant_repos += 1
                    else:
                        self._progress.non_compliant_repos += 1

            batch_start += self._batch_size

        # Generate report even on cancellation — partial data beats no data.
        if self._progress.status != "cancelled":
            self._progress.status = "completed"

        report = await workflow.execute_activity(
            generate_report,
            args=[input.org, self._results],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=GITHUB_API_RETRY,
        )

        if self._cancel_requested:
            report["cancelled"] = True
            report["cancel_reason"] = self._cancel_reason
            report["repos_scanned_before_cancel"] = self._progress.scanned_repos

        if self._continuation_count > 0:
            report["continue_as_new_count"] = self._continuation_count

        # Don't exit while signal/update handlers are still running.
        await workflow.wait_condition(workflow.all_handlers_finished)
        return report

    # -- Signals (fire-and-forget, CAN modify state) --

    @workflow.signal
    async def cancel_scan(self, reason: str = "Manual cancellation") -> None:
        self._cancel_requested = True
        self._cancel_reason = reason
        workflow.logger.info(f"Cancel requested: {reason}")

    @workflow.signal
    async def pause_scan(self, duration_seconds: int = 60) -> None:
        """Sets a flag; the main loop creates the durable timer between batches."""
        self._pause_requested = True
        self._pause_duration_secs = max(1, duration_seconds)
        workflow.logger.info(f"Pause requested: {duration_seconds}s")

    # -- Updates (synchronous request-response, caller WAITS for confirmation) --
    #
    # The difference from signals: when you send an update, you get a response.
    # "Batch size changed: 10 → 3" or "Rejected: must be >= 1". With a signal,
    # you fire and hope. With an update, you know.

    @workflow.update
    async def update_batch_size(self, new_size: int) -> str:
        old = self._batch_size
        self._batch_size = new_size
        self._progress.batch_size = new_size
        msg = f"Batch size updated: {old} -> {new_size}"
        workflow.logger.info(msg)
        return msg

    @update_batch_size.validator
    def validate_batch_size(self, new_size: int) -> None:
        """Runs BEFORE the handler. Rejects bad input before it touches state."""
        if not isinstance(new_size, int):
            raise ValueError(f"Expected int, got {type(new_size).__name__}")
        if new_size < 1:
            raise ValueError(f"Batch size must be >= 1, got {new_size}")
        if new_size > 50:
            raise ValueError(f"Batch size must be <= 50, got {new_size}")
        if self._progress.status in ("completed", "cancelled", "failed"):
            raise ValueError(f"Scan already {self._progress.status}")

    # -- Queries (read-only, CANNOT modify state) --

    @workflow.query
    def progress(self) -> ScanProgress:
        return self._progress

    @workflow.query
    def results_so_far(self) -> list[RepoSecurityResult]:
        return self._results

    @workflow.query
    def is_cancelled(self) -> bool:
        return self._cancel_requested

    @workflow.query
    def current_batch_size(self) -> int:
        return self._batch_size
