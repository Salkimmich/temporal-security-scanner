# SDK Comparison: Python vs Go

This document compares the same Temporal workflow implemented in Python (the primary, runnable implementation) and Go (annotated reference). The goal isn't to declare a winner — it's to show how the same Temporal concepts map to different language idioms, and when you'd choose one over the other.

## At a Glance

| Aspect | Python SDK | Go SDK |
|--------|-----------|--------|
| **Workflow shape** | Class with decorators | Plain exported function |
| **Activity shape** | Decorated functions (or class methods) | Methods on a struct |
| **Concurrency** | `asyncio.gather()` | `workflow.Go()` + channels |
| **Error handling** | Exceptions (raise/except) | Explicit returns (result, error) |
| **Query handlers** | `@workflow.query` method decorator | `workflow.SetQueryHandler()` call |
| **Signal handlers** | `@workflow.signal` method decorator | `workflow.GetSignalChannel()` + select |
| **Activity options** | Kwargs per `execute_activity` call | Applied to context, then reused |
| **Non-retryable errors** | Listed in `RetryPolicy(non_retryable_error_types=["..."])` | Wrapped per-error: `temporal.NewNonRetryableApplicationError()` |
| **Blocking I/O** | Needs `ThreadPoolExecutor` for sync activities | Native goroutines handle it |
| **Determinism enforcement** | Runtime sandbox (re-imports workflow module) | Static analysis tool (`workflowcheck`) |
| **SDK maturity** | Stable (1.x), fastest-growing | Most mature, Temporal's home SDK |
| **Typing** | MyPy (optional, but encouraged) | Compile-time (mandatory) |
| **Payload encryption** | `PayloadCodec` on `DataConverter` | `CodecDataConverter` wrapping default |

## Deep Dives

### 1. Workflow Definition

The most visible difference. Python uses a class; Go uses a function.

**Python:**
```python
@workflow.defn
class SecurityScanWorkflow:
    def __init__(self):
        self._progress = ScanProgress(org="")
        self._results = []

    @workflow.run
    async def run(self, input: ScanInput) -> dict:
        # orchestration logic...

    @workflow.query
    def progress(self) -> ScanProgress:
        return self._progress
```

**Go:**
```go
func SecurityScanWorkflow(ctx workflow.Context, input ScanInput) (map[string]interface{}, error) {
    progress := ScanProgress{Org: input.Org, Status: "starting"}
    var results []RepoSecurityResult

    workflow.SetQueryHandler(ctx, "progress", func() (ScanProgress, error) {
        return progress, nil
    })

    // orchestration logic...
}
```

**What this reveals:** Python groups state and behavior in a class — query handlers are methods alongside the run method. Go uses closure scope — local variables *are* the state, and query handlers capture them. Python is more structured; Go is flatter.

**For developer audiences:** Python developers find the class pattern intuitive. Go developers may initially be surprised there's no struct for the workflow itself — the function-with-closures pattern is actually more Go-idiomatic.

### 2. Parallel Activity Execution

This is the most revealing comparison. Same outcome, very different expression.

**Python:**
```python
tasks = []
for repo in batch:
    task = workflow.execute_activity(
        check_repo_security,
        args=[input.org, repo.name, input.token],
        start_to_close_timeout=timedelta(seconds=60),
        retry_policy=GITHUB_API_RETRY,
    )
    tasks.append(task)
batch_results = await asyncio.gather(*tasks, return_exceptions=True)
```

**Go:**
```go
resultCh := workflow.NewChannel(ctx)
for _, repo := range batch {
    repoName := repo.Name
    workflow.Go(ctx, func(gCtx workflow.Context) {
        var result RepoSecurityResult
        err := workflow.ExecuteActivity(scanCtx, "CheckRepoSecurity",
            input.Org, repoName, input.Token,
        ).Get(gCtx, &result)
        resultCh.Send(gCtx, &result)
    })
}
// Collect results
for i := 0; i < len(batch); i++ {
    var result *RepoSecurityResult
    resultCh.Receive(ctx, &result)
    // process result...
}
```

**What this reveals:** Python's `asyncio.gather()` is more concise for fan-out/fan-in. Go's `workflow.Go()` + channels is more code but mirrors Go's native concurrency model (goroutines + channels). Go developers will recognize this pattern immediately; Python developers might find it verbose.

**Key gotcha in both:** You can't use *native* concurrency primitives in workflows. Python can't use `threading` or `concurrent.futures`. Go can't use `go` goroutines or `sync.WaitGroup`. Both SDKs provide deterministic replacements.

### 3. Error Handling

This is where language philosophy shows through most clearly.

**Python — exception-based:**
```python
# In the activity:
raise ValueError("Organization not found")  # non-retryable
raise RuntimeError("Rate limit exceeded")   # retryable

# In the retry policy:
RetryPolicy(non_retryable_error_types=["ValueError"])

# In the workflow:
batch_results = await asyncio.gather(*tasks, return_exceptions=True)
for result in batch_results:
    if isinstance(result, Exception):
        self._progress.errors += 1
```

**Go — explicit error returns:**
```go
// In the activity:
return nil, temporal.NewNonRetryableApplicationError(
    "organization not found", "NOT_FOUND", nil,
)  // non-retryable
return nil, fmt.Errorf("rate limit exceeded")  // retryable

// In the workflow:
err = workflow.ExecuteActivity(...).Get(ctx, &result)
if err != nil {
    progress.Errors++
}
```

**What this reveals:** Python centralizes retry decisions in the `RetryPolicy` — you list which exception types shouldn't be retried. Go decentralizes them — each activity marks its own errors as retryable or not at the point of failure. Go's approach is more granular and explicit. Python's is more centralized and concise.

### 4. Activity Registration & Dependencies

**Python — functions registered directly:**
```python
worker = Worker(
    client,
    task_queue=TASK_QUEUE,
    workflows=[SecurityScanWorkflow],
    activities=[fetch_org_repos, check_repo_security, generate_report],
    activity_executor=executor,  # ThreadPoolExecutor needed!
)
```

**Go — struct instance registered:**
```go
activities := &Activities{
    HTTPClient: &http.Client{Timeout: 30 * time.Second},
}
w := worker.New(c, TaskQueue, worker.Options{})
w.RegisterWorkflow(SecurityScanWorkflow)
w.RegisterActivity(activities)
```

**What this reveals:** Go's struct-method approach makes dependency injection explicit — the HTTP client is constructed once and shared across all activities. Python's function approach is simpler but requires passing dependencies as parameters or using module-level globals.

Also note: Python needs a `ThreadPoolExecutor` for synchronous activities (because `requests` blocks the asyncio event loop). Go handles concurrency natively — no executor needed.

### 5. Determinism Enforcement

**Python — runtime sandbox:**
- Workflow code runs in a restricted sandbox that intercepts non-deterministic calls
- Third-party imports need `with workflow.unsafe.imports_passed_through()`
- Catches violations at runtime with clear error messages
- Has some performance overhead and edge cases with certain libraries

**Go — static analysis:**
- `workflowcheck` tool scans code at build time
- No runtime overhead
- Can't catch everything (it's heuristic)
- `go install go.temporal.io/sdk/contrib/tools/workflowcheck@latest`

## Encryption / PayloadCodec

Both SDKs use the same codec pattern for payload encryption, but with slightly different integration points.

**Python:**
```python
class EncryptionCodec(PayloadCodec):
    async def encode(self, payloads: List[Payload]) -> List[Payload]: ...
    async def decode(self, payloads: List[Payload]) -> List[Payload]: ...

# Set on client via data_converter
client = await Client.connect("localhost:7233",
    data_converter=dataclasses.replace(
        temporalio.converter.default(),
        payload_codec=EncryptionCodec(),
    ),
)
```

**Go:**
```go
type EncryptionCodec struct{}
func (e *EncryptionCodec) Encode(payloads []*commonpb.Payload) ([]*commonpb.Payload, error) { ... }
func (e *EncryptionCodec) Decode(payloads []*commonpb.Payload) ([]*commonpb.Payload, error) { ... }

// Set on client via DataConverter option
client, err := client.Dial(client.Options{
    DataConverter: converter.NewCodecDataConverter(
        converter.GetDefaultDataConverter(),
        &EncryptionCodec{},
    ),
})
```

The Go SDK wraps the codec with `converter.NewCodecDataConverter`, while Python uses `dataclasses.replace` on the default converter. Both result in the same behavior: payloads are encrypted client-side before reaching the server.

**Key difference:** Go's codec methods return `error` (can signal encryption failure). Python's are `async` (could support async key fetching from a KMS, though typically not needed).

## When to Choose Which

| Scenario | Recommended SDK | Why |
|----------|----------------|-----|
| Team already writes Python | Python | Lowest adoption friction |
| Data/ML pipeline orchestration | Python | Ecosystem alignment (pandas, numpy, etc.) |
| Platform/infrastructure team | Go | Matches the system programming context |
| Microservices in Go | Go | Keep the stack consistent |
| Rapid prototyping | Python | Less boilerplate, faster iteration |
| Performance-critical workers | Go | Lower overhead, no GIL |
| Mixed team | Either — they interoperate! | Workflows in one language can call activities in another |

## The Polyglot Story

One of Temporal's strengths: a Python workflow can start a Go activity (and vice versa). The wire protocol is the same. If your organization has teams in different languages, each can use their preferred SDK while sharing the same Temporal cluster. The security scanner workflow could run in Python while a high-throughput data processing activity runs in Go — on different workers, same task queue.
