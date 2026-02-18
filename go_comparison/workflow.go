package scanner

// =============================================================================
// Workflow — Go vs Python: Where the Differences Really Matter
// =============================================================================
//
// This file contains the most illuminating differences between the two SDKs.
// The workflow logic is identical — fetch repos, scan in batches, report.
// But the *expression* of that logic differs in ways that matter to developers.
//
// SUMMARY OF KEY DIFFERENCES:
//
// ┌─────────────────────┬──────────────────────────────┬──────────────────────────────┐
// │ Concept             │ Python                       │ Go                           │
// ├─────────────────────┼──────────────────────────────┼──────────────────────────────┤
// │ Workflow definition │ Class + @workflow.defn        │ Exported function            │
// │ Concurrency         │ asyncio.gather()             │ workflow.Go() + channels     │
// │ Query handler       │ @workflow.query method       │ workflow.SetQueryHandler()    │
// │ Signal handler      │ @workflow.signal method      │ workflow.GetSignalChannel()   │
// │ Error handling      │ try/except + exception types │ if err != nil + error wraps  │
// │ Retry policy        │ RetryPolicy dataclass        │ temporal.RetryPolicy struct  │
// │ Activity options    │ Kwargs on execute_activity   │ workflow.WithActivityOptions  │
// │ State               │ self._progress (instance)    │ Closure variables            │
// │ Sandbox             │ Yes (custom import system)   │ No (static analysis tool)    │
// └─────────────────────┴──────────────────────────────┴──────────────────────────────┘
//
// =============================================================================

import (
	"fmt"
	"time"

	"go.temporal.io/sdk/temporal"
	"go.temporal.io/sdk/workflow"
)

// SecurityScanWorkflow is the main workflow function.
//
// STRUCTURAL DIFFERENCE #1: Workflow shape.
//
// Python: A class with @workflow.defn, @workflow.run, @workflow.query methods.
//
//	@workflow.defn
//	class SecurityScanWorkflow:
//	    def __init__(self):
//	        self._progress = ScanProgress(org="")
//	    @workflow.run
//	    async def run(self, input: ScanInput) -> dict:
//	        ...
//	    @workflow.query
//	    def progress(self) -> ScanProgress:
//	        return self._progress
//
// Go: A plain function. State lives in closure variables. Query handlers
// are registered explicitly inside the function body.
//
// WHY THIS MATTERS: Python's class-based approach groups the workflow,
// its state, and its queries together naturally. Go's function-based approach
// is flatter — state is local variables, queries are registered imperatively.
// Neither is wrong; they reflect the language idioms. Python developers coming
// to Go need to shift from "methods on self" to "closures over local state."
func SecurityScanWorkflow(ctx workflow.Context, input ScanInput) (map[string]interface{}, error) {
	logger := workflow.GetLogger(ctx)

	// ─── State (Python: self._progress, self._results) ───
	// Go uses local variables; Python uses instance attributes.
	progress := ScanProgress{
		Org:    input.Org,
		Status: "starting",
	}
	var results []RepoSecurityResult
	cancelRequested := false
	cancelReason := ""

	// ─── Signal Handler ───
	//
	// DIFFERENCE: Signal registration.
	//
	// Python: Declarative via @workflow.signal decorator on a method.
	//     @workflow.signal
	//     async def cancel_scan(self, reason: str = "Manual cancellation") -> None:
	//         self._cancel_requested = True
	//         self._cancel_reason = reason
	//
	// Go: Get a typed channel, then drain it in a selector or goroutine.
	// There's no decorator — you explicitly create a signal channel and
	// process messages from it. More code, but gives you full control
	// over when and how signals are processed.
	cancelCh := workflow.GetSignalChannel(ctx, "cancel_scan")

	// Drain cancel signals asynchronously so they don't block the main flow.
	// This goroutine sets the flag; the batch loop checks it.
	workflow.Go(ctx, func(gCtx workflow.Context) {
		var reason string
		cancelCh.Receive(gCtx, &reason)
		cancelRequested = true
		cancelReason = reason
		logger.Info("Cancellation requested", "reason", reason)
	})

	// ─── Query Handlers ───
	//
	// DIFFERENCE #2: Query registration.
	//
	// Python: Declarative via @workflow.query decorator on a method.
	//     @workflow.query
	//     def progress(self) -> ScanProgress:
	//         return self._progress
	//
	// Go: Imperative via workflow.SetQueryHandler inside the function.
	// The query reads from closure variables (progress, results).
	//
	// Python's approach is cleaner for simple cases.
	// Go's approach is more flexible (you can register/unregister dynamically).
	err := workflow.SetQueryHandler(ctx, "progress", func() (ScanProgress, error) {
		return progress, nil
	})
	if err != nil {
		return nil, fmt.Errorf("registering progress query: %w", err)
	}

	err = workflow.SetQueryHandler(ctx, "results_so_far", func() ([]RepoSecurityResult, error) {
		return results, nil
	})
	if err != nil {
		return nil, fmt.Errorf("registering results query: %w", err)
	}

	err = workflow.SetQueryHandler(ctx, "is_cancelled", func() (bool, error) {
		return cancelRequested, nil
	})
	if err != nil {
		return nil, fmt.Errorf("registering is_cancelled query: %w", err)
	}

	// ─── Activity Options ───
	//
	// DIFFERENCE #3: How activity options are applied.
	//
	// Python: Passed as kwargs to each execute_activity call.
	//     await workflow.execute_activity(
	//         fetch_org_repos,
	//         args=[input.org, input.token],
	//         start_to_close_timeout=timedelta(seconds=120),
	//         retry_policy=GITHUB_API_RETRY,
	//         heartbeat_timeout=timedelta(seconds=30),
	//     )
	//
	// Go: Applied to the context, then reused. This is more composable —
	// you create different contexts for different option sets and pass
	// them to workflow.ExecuteActivity.
	//
	// Both SDKs define retry policies almost identically:
	//
	// Python:
	//     RetryPolicy(
	//         initial_interval=timedelta(seconds=2),
	//         backoff_coefficient=2.0,
	//         maximum_interval=timedelta(seconds=60),
	//         maximum_attempts=5,
	//         non_retryable_error_types=["ValueError"],
	//     )
	//
	// Go (below): Same fields, different syntax.
	// Note: Go uses NonRetryableErrorTypes matching on error *type names*,
	// while Python matches on exception class names. Same concept.
	retryPolicy := &temporal.RetryPolicy{
		InitialInterval:    2 * time.Second,
		BackoffCoefficient: 2.0,
		MaximumInterval:    60 * time.Second,
		MaximumAttempts:    5,
	}

	// Context with activity options (reusable across multiple activity calls)
	fetchCtx := workflow.WithActivityOptions(ctx, workflow.ActivityOptions{
		StartToCloseTimeout: 120 * time.Second,
		HeartbeatTimeout:    30 * time.Second,
		RetryPolicy:         retryPolicy,
	})

	scanCtx := workflow.WithActivityOptions(ctx, workflow.ActivityOptions{
		StartToCloseTimeout: 60 * time.Second,
		RetryPolicy:         retryPolicy,
	})

	reportCtx := workflow.WithActivityOptions(ctx, workflow.ActivityOptions{
		StartToCloseTimeout: 30 * time.Second,
		RetryPolicy:         retryPolicy,
	})

	// ─── Step 1: Fetch repositories ───
	logger.Info("Starting security scan", "org", input.Org)

	var repos []RepoInfo
	// In Go, ExecuteActivity returns a Future. .Get() blocks until complete.
	// In Python, execute_activity is awaited directly.
	err = workflow.ExecuteActivity(fetchCtx, "FetchOrgRepos", input).Get(ctx, &repos)
	if err != nil {
		return nil, fmt.Errorf("fetching repos: %w", err)
	}

	progress.TotalRepos = len(repos)
	progress.Status = "scanning"
	logger.Info("Found repos, beginning scan", "count", len(repos))

	// ─── Step 2: Scan in parallel batches ───
	//
	// DIFFERENCE #4: Parallel execution — the most revealing difference.
	//
	// PYTHON uses asyncio.gather():
	//     tasks = []
	//     for repo in batch:
	//         task = workflow.execute_activity(check_repo_security, ...)
	//         tasks.append(task)
	//     batch_results = await asyncio.gather(*tasks, return_exceptions=True)
	//
	// GO uses workflow.Go() (Temporal's goroutine) + a channel to collect results.
	// You cannot use native Go goroutines in a workflow (non-deterministic).
	// workflow.Go() is the deterministic replacement.
	//
	// workflow.Go() + Channel is more code than asyncio.gather(), but it's
	// the standard Go concurrency model (goroutines + channels) adapted
	// for Temporal's determinism requirements. Go developers will recognize it.
	// Python developers will find asyncio.gather() more natural.
	//
	// BOTH achieve the same outcome: 10 activities running concurrently per batch.
	batchSize := 10

	for batchStart := 0; batchStart < len(repos); batchStart += batchSize {
		// Check cancellation between batches — same pattern as Python.
		// Python: if self._cancel_requested: break
		// Go: just check the flag set by the signal goroutine.
		if cancelRequested {
			logger.Info("Scan cancelled", "reason", cancelReason,
				"scanned", progress.ScannedRepos)
			progress.Status = "cancelled"
			break
		}

		batchEnd := batchStart + batchSize
		if batchEnd > len(repos) {
			batchEnd = len(repos)
		}
		batch := repos[batchStart:batchEnd]

		// Create a channel to collect results from concurrent activities
		resultCh := workflow.NewChannel(ctx)

		// Launch concurrent activities using workflow.Go (NOT native goroutines)
		for _, repo := range batch {
			// Capture loop variable (same reason as Python's closure gotcha)
			repoName := repo.Name
			workflow.Go(ctx, func(gCtx workflow.Context) {
				var result RepoSecurityResult
				err := workflow.ExecuteActivity(scanCtx, "CheckRepoSecurity",
					input.Org, repoName, input.Token,
				).Get(gCtx, &result)

				if err != nil {
					// Send error result
					errMsg := err.Error()
					resultCh.Send(gCtx, &RepoSecurityResult{
						Repository: repoName,
						Error:      &errMsg,
					})
				} else {
					resultCh.Send(gCtx, &result)
				}
			})
		}

		// Collect all results from this batch
		for i := 0; i < len(batch); i++ {
			var result *RepoSecurityResult
			resultCh.Receive(ctx, &result)

			if result.Error != nil {
				progress.Errors++
			} else {
				results = append(results, *result)
				progress.ScannedRepos++
				if result.IsFullyCompliant() {
					progress.CompliantRepos++
				} else {
					progress.NonCompliantRepos++
				}
			}
		}
	}

	// ─── Step 3: Generate report ───
	// Generate a report even on cancellation — partial data is still valuable.
	if progress.Status != "cancelled" {
		progress.Status = "completed"
	}
	logger.Info("Scan complete",
		"scanned", progress.ScannedRepos,
		"total", progress.TotalRepos,
		"cancelled", cancelRequested,
	)

	var report map[string]interface{}
	err = workflow.ExecuteActivity(reportCtx, "GenerateReport",
		input.Org, results,
	).Get(ctx, &report)
	if err != nil {
		return nil, fmt.Errorf("generating report: %w", err)
	}

	// Add cancellation metadata if applicable
	if cancelRequested {
		report["cancelled"] = true
		report["cancel_reason"] = cancelReason
		report["repos_scanned_before_cancel"] = progress.ScannedRepos
	}

	return report, nil
}

// =============================================================================
// SANDBOX vs STATIC ANALYSIS
// =============================================================================
//
// One more notable difference not visible in the code:
//
// PYTHON uses a runtime sandbox. Workflow code runs in a restricted environment
// that catches non-deterministic calls (datetime.now(), random(), file I/O)
// at runtime. You import third-party modules via `with workflow.unsafe.imports_passed_through()`.
// It's convenient but has performance costs and edge cases.
//
// GO uses a static analysis tool: `workflowcheck`. It scans your code at
// build time and flags non-deterministic calls. No runtime overhead, no sandbox.
// But it can't catch everything — it's best-effort at compile time.
//
//     go install go.temporal.io/sdk/contrib/tools/workflowcheck@latest
//     workflowcheck ./...
//
// Both approaches serve the same purpose: helping developers write deterministic
// workflow code. Python catches more at runtime; Go catches more at build time.
// =============================================================================
